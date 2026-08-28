from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.user import User
from app.services.integration_storage import (
    SMTP_INTEGRATION_TYPE,
    ActiveSMTPSettings,
    SMTPSecretError,
    acquire_smtp_configuration_read_lock,
    build_active_smtp_settings,
    smtp_instance_is_archived,
)

_DELIVERY_SENDING = "sending"


class SMTPDeliveryIneligibleError(RuntimeError):
    """The SMTP control plane changed before an external operation began."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def persisted_smtp_settings_heartbeat(
    heartbeat: Callable[[int, ActiveSMTPSettings], None],
    *,
    persisted_settings: ActiveSMTPSettings,
) -> Callable[[int, ActiveSMTPSettings], None]:
    def _heartbeat(
        lease_seconds: int, _effective_settings: ActiveSMTPSettings
    ) -> None:
        heartbeat(lease_seconds, persisted_settings)

    return _heartbeat


def lock_smtp_delivery_external_io_eligibility(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
    expected_settings: ActiveSMTPSettings,
) -> None:
    """Fence SMTP configuration and lease changes at the side-effect boundary.

    The shared advisory lock remains transaction-scoped. Configuration writers
    take its exclusive counterpart, so callers must commit or roll back only
    after the next SMTP operation has completed.
    """

    acquire_smtp_configuration_read_lock(db)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise SMTPDeliveryIneligibleError(
            "smtp_delivery_missing", "SMTP delivery no longer exists."
        )
    if (
        delivery.connector_type != SMTP_INTEGRATION_TYPE
        or delivery.state != _DELIVERY_SENDING
        or int(delivery.attempt_count or 0) != int(expected_attempt_number)
    ):
        raise RuntimeError("SMTP delivery lease is no longer owned by this worker")

    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == delivery.integration_id)
        .execution_options(populate_existing=True)
    )
    if instance is None or instance.integration_type != SMTP_INTEGRATION_TYPE:
        raise SMTPDeliveryIneligibleError(
            "smtp_integration_missing", "SMTP integration no longer exists."
        )
    if not instance.enabled or smtp_instance_is_archived(instance):
        raise SMTPDeliveryIneligibleError(
            "smtp_integration_disabled", "SMTP integration is disabled."
        )

    subscription = db.scalar(
        select(IntegrationSubscription)
        .where(IntegrationSubscription.id == delivery.subscription_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        subscription is None
        or subscription.integration_id != instance.id
        or subscription.event_type != delivery.event_type
        or not subscription.enabled
    ):
        raise SMTPDeliveryIneligibleError(
            "smtp_subscription_disabled",
            "SMTP event subscription is disabled or no longer exists.",
        )

    credential_source = _lock_credential_source(db, instance=instance)
    try:
        current_settings = build_active_smtp_settings(
            instance,
            credential_source=credential_source,
        )
    except SMTPSecretError as exc:
        raise SMTPDeliveryIneligibleError(
            "smtp_configuration_invalid",
            "SMTP credentials changed and the current configuration is invalid.",
        ) from exc
    if current_settings != expected_settings:
        raise SMTPDeliveryIneligibleError(
            "smtp_configuration_changed",
            "SMTP configuration changed after this delivery was claimed.",
        )

    if delivery.owner_user_id != instance.owner_user_id:
        raise SMTPDeliveryIneligibleError(
            "smtp_owner_mismatch",
            "SMTP delivery owner no longer matches its integration owner.",
        )
    if instance.owner_user_id is None:
        return
    owner = db.scalar(
        select(User)
        .where(User.id == instance.owner_user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if owner is None or not owner.is_active or not owner.is_approved:
        raise SMTPDeliveryIneligibleError(
            "smtp_owner_not_eligible",
            "SMTP owner is no longer active and approved for outbound delivery.",
        )


def _lock_credential_source(
    db: Session, *, instance: IntegrationInstance
) -> IntegrationInstance | None:
    source_id = instance.credential_source_integration_id
    if source_id is None:
        return None
    source = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == source_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        source is None
        or source.integration_type != SMTP_INTEGRATION_TYPE
        or smtp_instance_is_archived(source)
        or source.credential_source_integration_id is not None
    ):
        raise SMTPDeliveryIneligibleError(
            "smtp_credential_source_invalid",
            "The shared SMTP credential source is no longer available.",
        )
    return source
