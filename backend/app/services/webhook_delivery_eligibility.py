from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_compat import (
    ensure_webhook_config_schema_compatible,
    lock_notification_webhook,
)
from app.services.integration_delivery_data_policy import (
    IntegrationDeliveryDataPolicyDenied,
    IntegrationDeliveryDataPolicyUnavailable,
    IntegrationDeliveryPolicyFence,
    enforce_integration_delivery_data_policy,
    lock_integration_delivery_policy_fence,
)
from app.services.report_event_compatibility import (
    validate_report_ready_delivery_owner,
)

if TYPE_CHECKING:
    from app.services.integration_delivery_data_policy import (
        IntegrationDeliveryPolicyAudit,
    )

_DELIVERY_SENDING = "sending"


class WebhookDeliveryIneligibleError(RuntimeError):
    """The webhook control plane was revoked before external I/O began."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data_policy_audit: IntegrationDeliveryPolicyAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data_policy_audit = data_policy_audit


class WebhookDeliveryTemporarilyIneligibleError(WebhookDeliveryIneligibleError):
    """A revoked owner may become eligible again within the retry budget."""


def _lock_webhook_delivery_data_policy_fence(
    db: Session,
) -> IntegrationDeliveryPolicyFence:
    try:
        return lock_integration_delivery_policy_fence(db)
    except IntegrationDeliveryDataPolicyUnavailable as exc:
        raise WebhookDeliveryTemporarilyIneligibleError(
            "webhook_data_policy_unavailable",
            str(exc),
        ) from exc


def lock_webhook_delivery_external_io_eligibility(
    db: Session,
    *,
    webhook_id: uuid.UUID,
    legacy_delivery_id: uuid.UUID,
    integration_delivery_id: uuid.UUID,
    expected_attempt_number: int,
) -> None:
    """Fence webhook revocation at the outbound HTTP side-effect boundary.

    The locks intentionally remain transaction-scoped. Callers must not commit
    between this check and the outbound request. Lease renewal may commit, but it
    must invoke this function again before the next request or redirect.
    """
    policy_fence = _lock_webhook_delivery_data_policy_fence(db)
    # Policy -> custodian -> team -> destination is shared with administration.
    # Capture IDs without locks, acquire final lock modes, then reject a changed
    # snapshot after the destination wait instead of upgrading locks out of order.
    from app.models.notification_webhook import NotificationWebhook
    from app.models.team import Team
    ownership = db.execute(select(NotificationWebhook.user_id, NotificationWebhook.team_id).where(NotificationWebhook.id == webhook_id)).first()
    if ownership is not None:
        if ownership.user_id:
            db.scalar(select(User.id).where(User.id == ownership.user_id).with_for_update())
        if ownership.team_id:
            db.scalar(select(Team.id).where(Team.id == ownership.team_id).with_for_update(read=True))
    webhook = lock_notification_webhook(
        db,
        webhook_id,
        refresh_existing=True,
    )
    if webhook is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_missing", "Webhook configuration no longer exists."
        )

    if ownership is None or (webhook.user_id, webhook.team_id) != (ownership.user_id, ownership.team_id):
        raise WebhookDeliveryTemporarilyIneligibleError("webhook_ownership_changed", "Destination ownership changed while waiting; retry after reauthorization")

    legacy = db.scalar(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.id == legacy_delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if legacy is None or legacy.webhook_id != webhook_id:
        raise WebhookDeliveryIneligibleError(
            "webhook_delivery_missing",
            "Webhook delivery no longer exists or belongs to this webhook.",
        )
    if legacy.delivery_state != _DELIVERY_SENDING or int(
        legacy.attempt_count or 0
    ) != int(expected_attempt_number):
        raise RuntimeError("Webhook delivery lease is no longer owned by this worker")

    generic = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == integration_delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if generic is None:
        raise WebhookDeliveryIneligibleError(
            "integration_delivery_missing",
            "Webhook integration delivery no longer exists.",
        )
    if (
        generic.connector_type != "webhook"
        or generic.state != _DELIVERY_SENDING
        or int(generic.attempt_count or 0) != int(expected_attempt_number)
    ):
        raise RuntimeError("Webhook delivery lease is no longer owned by this worker")

    if not webhook.enabled:
        raise WebhookDeliveryIneligibleError(
            "webhook_disabled", "Webhook configuration is disabled."
        )
    if webhook.integration_id is None or webhook.subscription_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration configuration is incomplete.",
        )
    if generic.integration_id != webhook.integration_id:
        raise WebhookDeliveryIneligibleError(
            "integration_projection_mismatch",
            "Webhook delivery no longer belongs to its configured integration.",
        )

    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == generic.integration_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        raise WebhookDeliveryIneligibleError(
            "integration_missing", "Webhook integration no longer exists."
        )
    if instance.integration_type != "webhook" or not instance.enabled:
        raise WebhookDeliveryIneligibleError(
            "integration_disabled", "Webhook integration is disabled."
        )
    ensure_webhook_config_schema_compatible(instance)

    subscription = db.scalar(
        select(IntegrationSubscription)
        .where(IntegrationSubscription.id == webhook.subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        subscription is None
        or subscription.integration_id != instance.id
        or generic.subscription_id != subscription.id
        or not subscription.enabled
    ):
        raise WebhookDeliveryIneligibleError(
            "subscription_disabled",
            "Webhook event subscription is disabled or no longer exists.",
        )

    if legacy.integration_delivery_id != generic.id:
        raise RuntimeError("Webhook delivery lease is no longer owned by this worker")

    if instance.owner_user_id is None:
        if (
            instance.system_key is not None
            and generic.owner_user_id is None
            and generic.event_type != "report_ready"
        ):
            _enforce_webhook_delivery_data_policy(
                db,
                instance=instance,
                delivery=generic,
                policy_fence=policy_fence,
            )
            return
        raise WebhookDeliveryIneligibleError(
            "integration_owner_missing",
            "Webhook integration owner configuration is incomplete.",
        )
    if (
        generic.owner_user_id != instance.owner_user_id
        or webhook.user_id != instance.owner_user_id
        or legacy.user_id != instance.owner_user_id
    ):
        raise WebhookDeliveryIneligibleError(
            "integration_owner_mismatch",
            "Webhook delivery owner no longer matches its integration owner.",
        )

    if generic.event_type == "report_ready":
        _validate_report_ready_delivery(db, delivery=generic)

    owner = db.scalar(
        select(User)
        .where(User.id == instance.owner_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if owner is None or not owner.is_active or not owner.is_approved:
        error_type = (
            WebhookDeliveryTemporarilyIneligibleError
            if generic.event_type == "report_ready"
            else WebhookDeliveryIneligibleError
        )
        raise error_type(
            "webhook_owner_not_eligible",
            "Webhook owner is no longer active and approved for outbound delivery.",
        )
    if owner.role not in {ROLE_ADMIN, ROLE_ANALYST}:
        raise WebhookDeliveryIneligibleError(
            "webhook_owner_not_authorized",
            "Webhook owner is no longer authorized to manage outbound deliveries.",
        )
    _validate_automation_authority(db, webhook=webhook, delivery=generic, owner=owner)
    _enforce_webhook_delivery_data_policy(
        db,
        instance=instance,
        delivery=generic,
        policy_fence=policy_fence,
    )


def _validate_automation_authority(
    db: Session, *, webhook, delivery: IntegrationDelivery, owner: User
) -> None:
    generic = delivery
    if getattr(webhook, "team_id", None):
        from app.services.team_access import team_access_predicate
        if not db.scalar(select(team_access_predicate(webhook.team_id, owner.id))):
            raise WebhookDeliveryIneligibleError("team_custodian_unavailable", "The team delivery custodian no longer has current team access; a manager must adopt the destination")
    if webhook.conditions_json:
        from pydantic import ValidationError
        from app.schemas.webhook_automation import WebhookConditionGroup
        from app.services.webhook_conditions import (
            evaluate_conditions,
            event_condition_values,
        )

        try:
            conditions = WebhookConditionGroup.model_validate(webhook.conditions_json)
        except ValidationError as exc:
            raise WebhookDeliveryIneligibleError(
                "webhook_conditions_invalid",
                "Saved webhook conditions are invalid; edit the subscription before retrying",
            ) from exc
        created_at = db.scalar(
            select(IntegrationEvent.created_at).where(
                IntegrationEvent.id == generic.event_id
            )
        )
        if (
            created_at is None
            or not evaluate_conditions(
                conditions,
                event_condition_values(
                    generic.payload_json or {},
                    created_at=created_at,
                    event_type=generic.event_type,
                ),
            )[0]
        ):
            raise WebhookDeliveryIneligibleError(
                "webhook_conditions_changed",
                "Current subscription conditions or freshness limits no longer permit this delivery",
            )
    if generic.event_type in {
        "intel.extraction.ready",
        "intel.indicators.changed",
        "hunt.approved",
    }:
        from app.services.intel_event_eligibility import (
            automation_event_current,
            IntelEventBusy,
        )

        try:
            current = automation_event_current(
                db, generic.payload_json or {}, generic.event_type, lock=True
            )
        except IntelEventBusy as exc:
            raise WebhookDeliveryTemporarilyIneligibleError(
                "intelligence_source_busy", str(exc)
            ) from exc
        if not current:
            raise WebhookDeliveryIneligibleError(
                "intelligence_event_superseded",
                "The intelligence evidence or approved hunt revision has changed; this historical action was not sent",
            )
    if webhook.credential_profile_id is not None:
        from app.services.webhook_credentials import load_credential

        try:
            load_credential(
                db,
                profile_id=webhook.credential_profile_id,
                user_id=owner.id,
                lock=True,
            )
        except ValueError as exc:
            raise WebhookDeliveryIneligibleError(
                "webhook_credential_unavailable", str(exc)
            ) from exc
    if generic.event_type in {
        "intel.extraction.ready",
        "intel.indicators.changed",
        "hunt.approved",
    }:
        from app.services.authorization import authorization_context_for_user

        authorization = authorization_context_for_user(db, owner)
        permissions = ("read:items", "write:notifications")
        if (
            generic.event_type == "hunt.approved"
            or (generic.payload_json or {}).get("team_id") is not None
        ):
            permissions += ("read:teams", "read:ai")
        if not all(authorization.has_durable(permission) for permission in permissions):
            raise WebhookDeliveryIneligibleError(
                "webhook_owner_not_authorized",
                "Current permissions do not permit this intelligence delivery",
            )
    if (
        generic.event_type == "hunt.approved"
        or (generic.payload_json or {}).get("team_id") is not None
    ):
        from app.services.team_access import team_access_predicate

        try:
            team_id = uuid.UUID(str((generic.payload_json or {}).get("team_id")))
        except (ValueError, TypeError) as exc:
            raise WebhookDeliveryIneligibleError(
                "hunt_team_unavailable", "Hunt event team is unavailable"
            ) from exc
        if not db.scalar(select(team_access_predicate(team_id, owner.id))):
            raise WebhookDeliveryIneligibleError(
                "hunt_team_unavailable",
                "Current team membership does not permit this hunt delivery",
            )


def _enforce_webhook_delivery_data_policy(
    db: Session,
    *,
    instance: IntegrationInstance,
    delivery: IntegrationDelivery,
    policy_fence: IntegrationDeliveryPolicyFence,
) -> None:
    try:
        enforce_integration_delivery_data_policy(
            db,
            instance=instance,
            delivery=delivery,
            surface="webhook.external_io",
            policy_fence=policy_fence,
        )
    except IntegrationDeliveryDataPolicyDenied as exc:
        raise WebhookDeliveryIneligibleError(
            "webhook_data_policy_denied",
            str(exc),
            data_policy_audit=exc.audit,
        ) from exc
    except IntegrationDeliveryDataPolicyUnavailable as exc:
        raise WebhookDeliveryTemporarilyIneligibleError(
            "webhook_data_policy_unavailable",
            str(exc),
        ) from exc


def _validate_report_ready_delivery(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> None:
    if delivery.event_id is None:
        raise WebhookDeliveryIneligibleError(
            "report_event_missing",
            "Webhook report delivery is missing its source event.",
        )
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == delivery.event_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if event is None or event.event_type != "report_ready":
        raise WebhookDeliveryIneligibleError(
            "report_event_missing",
            "Webhook report delivery source event no longer exists.",
        )
    try:
        validate_report_ready_delivery_owner(
            db,
            event=event,
            delivery_payload=delivery.payload_json,
            delivery_owner_user_id=delivery.owner_user_id,
            require_eligible=False,
        )
    except ValueError as exc:
        raise WebhookDeliveryIneligibleError(
            "report_owner_context_invalid",
            "Webhook report delivery has invalid owner context.",
        ) from exc
