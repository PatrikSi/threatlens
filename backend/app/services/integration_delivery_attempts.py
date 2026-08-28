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
    compatibility_error_code: str | None = None,
    compatibility_error_message: str | None = None,
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

    compatibility_wait = known_pre_side_effect and compatibility_error_code is not None
    if compatibility_wait:
        response["retry_budget_consumed"] = False

    attempt.status = ATTEMPT_INTERRUPTED
    attempt.finished_at = now
    attempt.error_code = (
        compatibility_error_code if compatibility_wait else "worker_interrupted"
    )
    attempt.error_message = (
        compatibility_error_message
        if compatibility_wait
        else "Delivery worker stopped before recording an outcome."
    )
    attempt.retryable = (
        True
        if compatibility_wait
        else delivery.connector_type != "smtp" or known_pre_side_effect
    )
    attempt.response_json = response
    db.add(attempt)
    return side_effect_possible


def retry_budget_attempt_count(
    db: Session,
    *,
    delivery: IntegrationDelivery,
) -> int:
    responses = db.scalars(
        select(IntegrationAttempt.response_json).where(
            IntegrationAttempt.delivery_id == delivery.id
        )
    ).all()
    exempt_attempts = sum(
        1
        for response in responses
        if isinstance(response, dict)
        and response.get("retry_budget_consumed") is False
    )
    return max(0, int(delivery.attempt_count or 0) - exempt_attempts)
