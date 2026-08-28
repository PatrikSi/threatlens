from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session as db_session_module
from app.core.config import get_settings
from app.models.integration import IntegrationAttempt, IntegrationDelivery
from app.services.integration_delivery_state import (
    coerce_utc,
    safe_error_message,
)

ATTEMPT_RUNNING = "running"
ATTEMPT_INTERRUPTED = "interrupted"

settings = get_settings()


def persist_external_side_effect_marker(
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int,
) -> bool:
    """Durably mark the point after which an interrupted delivery is ambiguous."""

    with db_session_module.SessionLocal() as marker_db:
        attempt = marker_db.scalar(
            select(IntegrationAttempt)
            .where(
                IntegrationAttempt.delivery_id == delivery_id,
                IntegrationAttempt.attempt_number == expected_attempt_number,
            )
            .with_for_update()
        )
        if attempt is None or attempt.status != ATTEMPT_RUNNING:
            marker_db.rollback()
            return False
        response = (
            dict(attempt.response_json)
            if isinstance(attempt.response_json, dict)
            else {}
        )
        response.update(
            {
                "delivery_outcome": "unknown",
                "external_side_effect_possible": True,
            }
        )
        attempt.response_json = response
        marker_db.add(attempt)
        marker_db.commit()
        return True


def interrupt_running_attempt(
    db: Session,
    *,
    delivery: IntegrationDelivery,
    now: datetime,
) -> bool | None:
    """Interrupt an attempt and return its last durable side-effect marker."""

    attempt = db.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == delivery.id,
            IntegrationAttempt.attempt_number
            == max(1, int(delivery.attempt_count or 0)),
            IntegrationAttempt.status == ATTEMPT_RUNNING,
        )
    )
    if attempt is None:
        return None

    response = (
        dict(attempt.response_json) if isinstance(attempt.response_json, dict) else {}
    )
    marker = response.get("external_side_effect_possible")
    side_effect_possible = marker if type(marker) is bool else None
    known_pre_side_effect = side_effect_possible is False
    response.update(
        {
            "delivery_outcome": (
                "not_attempted" if known_pre_side_effect else "unknown"
            ),
            "external_side_effect_possible": not known_pre_side_effect,
        }
    )

    attempt.status = ATTEMPT_INTERRUPTED
    attempt.finished_at = now
    attempt.error_code = "worker_interrupted"
    attempt.error_message = "Delivery worker stopped before recording an outcome."
    attempt.retryable = delivery.connector_type != "smtp" or known_pre_side_effect
    attempt.response_json = response
    db.add(attempt)
    return side_effect_possible


def defer_stale_pre_side_effect_attempt(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    error_code: str,
    error_message: str,
    delay_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Defer a stale SMTP attempt only when durable state proves DATA never began."""

    current_time = now or datetime.now(timezone.utc)
    delivery = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None or delivery.connector_type != "smtp":
        return False
    scheduled_for = coerce_utc(delivery.not_before)
    if delivery.state != "sending" or (
        scheduled_for is not None and scheduled_for > current_time
    ):
        return False
    stale_cutoff = current_time - timedelta(
        seconds=settings.notification_delivery_sending_stale_after_seconds
    )
    claimed_at = coerce_utc(delivery.claimed_at)
    if claimed_at is not None and claimed_at >= stale_cutoff:
        return False
    if interrupt_running_attempt(db, delivery=delivery, now=current_time) is not False:
        return False

    delivery.state = "retry_wait"
    delivery.claimed_at = None
    delivery.not_before = current_time + timedelta(
        seconds=max(1, int(delay_seconds))
    )
    delivery.last_error_code = error_code
    delivery.last_error_message = safe_error_message(error_message)
    delivery.last_error_retryable = True
    db.add(delivery)
    return True
