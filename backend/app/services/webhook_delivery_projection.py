from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery
from app.models.notification_webhook_delivery import NotificationWebhookDelivery

_GENERIC_TERMINAL_STATES = frozenset({"succeeded", "failed", "dead_letter"})


def reconcile_linked_terminal_webhook_projection(
    db: Session,
    *,
    legacy_delivery: NotificationWebhookDelivery,
    current_time: datetime,
) -> bool:
    if legacy_delivery.integration_delivery_id is None:
        return False

    generic = db.scalar(
        select(IntegrationDelivery)
        .where(
            IntegrationDelivery.id == legacy_delivery.integration_delivery_id,
            IntegrationDelivery.connector_type == "webhook",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if generic is None:
        return False
    return sync_terminal_webhook_projection(
        generic=generic,
        legacy_delivery=legacy_delivery,
        current_time=current_time,
    )


def sync_terminal_webhook_projection(
    *,
    generic: IntegrationDelivery,
    legacy_delivery: NotificationWebhookDelivery,
    current_time: datetime,
) -> bool:
    if generic.state not in _GENERIC_TERMINAL_STATES:
        return False

    succeeded = generic.state == "succeeded"
    legacy_delivery.delivery_state = "succeeded" if succeeded else "failed"
    legacy_delivery.success = succeeded
    legacy_delivery.status_code = generic.last_status_code
    legacy_delivery.duration_ms = generic.last_duration_ms
    legacy_delivery.error = None if succeeded else generic.last_error_message
    legacy_delivery.attempt_count = max(
        int(legacy_delivery.attempt_count or 0),
        int(generic.attempt_count or 0),
    )
    legacy_delivery.claimed_at = None
    legacy_delivery.not_before = None
    legacy_delivery.attempted_at = (
        generic.completed_at or generic.dead_lettered_at or current_time
    )
    return True
