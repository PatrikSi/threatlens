from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session as db_session_module
from app.models.integration import IntegrationAttempt, IntegrationDelivery

ATTEMPT_RUNNING = "running"
ATTEMPT_INTERRUPTED = "interrupted"

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
