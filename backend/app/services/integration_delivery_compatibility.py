from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import IntegrationDelivery
from app.services.integration_delivery import (
    DEFERRED,
    DELIVERY_PENDING,
    DELIVERY_RETRY_WAIT,
    DELIVERY_SENDING,
    DELIVERY_TERMINAL_STATES,
    MISSING,
    TERMINAL,
)
from app.services.integration_delivery_attempts import interrupt_running_attempt
from app.services.integration_delivery_state import (
    coerce_utc,
    dead_letter_without_attempt,
    safe_error_message,
)

settings = get_settings()


@dataclass(frozen=True)
class IntegrationDeliveryCompatibilityDeferral:
    status: str
    delivery_id: uuid.UUID
    reason: str
    scheduled_for: datetime | None = None


def defer_integration_delivery_for_compatibility(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    error_code: str,
    error_message: str,
    delay_seconds: int = 60,
    now: datetime | None = None,
) -> IntegrationDeliveryCompatibilityDeferral:
    """Defer unsupported work without stealing or abandoning an active attempt."""

    current_time = now or datetime.now(timezone.utc)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        return IntegrationDeliveryCompatibilityDeferral(
            status=MISSING,
            delivery_id=delivery_id,
            reason="delivery_not_found",
        )
    if delivery.state in DELIVERY_TERMINAL_STATES:
        return IntegrationDeliveryCompatibilityDeferral(
            status=TERMINAL,
            delivery_id=delivery.id,
            reason=f"delivery_{delivery.state}",
        )

    scheduled_for = coerce_utc(delivery.not_before)
    if scheduled_for is not None and scheduled_for > current_time:
        return IntegrationDeliveryCompatibilityDeferral(
            status=DEFERRED,
            delivery_id=delivery.id,
            reason=(
                "active_lease" if delivery.state == DELIVERY_SENDING else "not_due"
            ),
            scheduled_for=scheduled_for,
        )

    if delivery.state == DELIVERY_SENDING:
        stale_cutoff = current_time - timedelta(
            seconds=settings.notification_delivery_sending_stale_after_seconds
        )
        claimed_at = coerce_utc(delivery.claimed_at)
        if claimed_at is not None and claimed_at >= stale_cutoff:
            return IntegrationDeliveryCompatibilityDeferral(
                status=DEFERRED,
                delivery_id=delivery.id,
                reason="already_claimed",
                scheduled_for=claimed_at,
            )
        side_effect_possible = interrupt_running_attempt(
            db,
            delivery=delivery,
            now=current_time,
            compatibility_error_code=error_code,
            compatibility_error_message=safe_error_message(error_message),
        )
        if delivery.connector_type == "smtp" and side_effect_possible is not False:
            dead_letter_without_attempt(
                delivery,
                code="unknown_delivery_outcome",
                message=(
                    "The SMTP worker stopped after delivery began, so message acceptance "
                    "is unknown. Replay the delivery explicitly to avoid an automatic duplicate."
                ),
                now=current_time,
            )
            return IntegrationDeliveryCompatibilityDeferral(
                status=TERMINAL,
                delivery_id=delivery.id,
                reason="unknown_delivery_outcome",
            )
    elif delivery.state not in {DELIVERY_PENDING, DELIVERY_RETRY_WAIT}:
        return IntegrationDeliveryCompatibilityDeferral(
            status=DEFERRED,
            delivery_id=delivery.id,
            reason=f"delivery_{delivery.state}",
        )

    retry_at = current_time + timedelta(seconds=max(1, int(delay_seconds)))
    delivery.state = DELIVERY_RETRY_WAIT
    delivery.claimed_at = None
    delivery.not_before = retry_at
    delivery.last_error_code = error_code
    delivery.last_error_message = safe_error_message(error_message)
    delivery.last_error_retryable = True
    db.add(delivery)
    return IntegrationDeliveryCompatibilityDeferral(
        status=DELIVERY_RETRY_WAIT,
        delivery_id=delivery.id,
        reason=error_code,
        scheduled_for=retry_at,
    )
