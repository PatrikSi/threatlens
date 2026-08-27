from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User

_DELIVERY_SENDING = "sending"


class WebhookDeliveryIneligibleError(RuntimeError):
    """The webhook control plane was revoked before external I/O began."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    webhook = db.scalar(
        select(NotificationWebhook)
        .where(NotificationWebhook.id == webhook_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if webhook is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_missing", "Webhook configuration no longer exists."
        )
    if not webhook.enabled:
        raise WebhookDeliveryIneligibleError(
            "webhook_disabled", "Webhook configuration is disabled."
        )
    if webhook.integration_id is None or webhook.subscription_id is None:
        raise WebhookDeliveryIneligibleError(
            "webhook_projection_missing",
            "Webhook integration configuration is incomplete.",
        )

    legacy = db.scalar(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.id == legacy_delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if legacy is None or legacy.webhook_id != webhook.id:
        raise WebhookDeliveryIneligibleError(
            "webhook_delivery_missing",
            "Webhook delivery no longer exists or belongs to this webhook.",
        )

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

    if (
        legacy.integration_delivery_id != generic.id
        or legacy.delivery_state != _DELIVERY_SENDING
        or int(legacy.attempt_count or 0) != int(expected_attempt_number)
    ):
        raise RuntimeError("Webhook delivery lease is no longer owned by this worker")

    if instance.owner_user_id is None:
        if instance.system_key is not None and generic.owner_user_id is None:
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

    owner = db.scalar(
        select(User)
        .where(User.id == instance.owner_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if owner is None or not owner.is_active or not owner.is_approved:
        raise WebhookDeliveryIneligibleError(
            "webhook_owner_not_eligible",
            "Webhook owner is no longer active and approved for outbound delivery.",
        )
    if owner.role not in {ROLE_ADMIN, ROLE_ANALYST}:
        raise WebhookDeliveryIneligibleError(
            "webhook_owner_not_authorized",
            "Webhook owner is no longer authorized to manage outbound deliveries.",
        )
