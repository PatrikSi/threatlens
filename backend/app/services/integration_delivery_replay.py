from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationAttempt, IntegrationDelivery


def smtp_replay_recipient_override(
    db: Session,
    *,
    source: IntegrationDelivery,
) -> list[str]:
    """Return only refused SMTP recipients when a partial attempt is replayed."""
    attempt = db.scalar(
        select(IntegrationAttempt)
        .where(IntegrationAttempt.delivery_id == source.id)
        .order_by(IntegrationAttempt.attempt_number.desc())
        .limit(1)
    )
    response = (
        attempt.response_json
        if attempt is not None and isinstance(attempt.response_json, dict)
        else {}
    )
    accepted = _recipient_disposition_values(response.get("accepted_recipients"))
    refused = _recipient_disposition_values(response.get("refused_recipients"))
    if not accepted or not refused:
        return []
    return refused


def _recipient_disposition_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    recipients: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, str):
            continue
        recipient = entry.strip()
        normalized = recipient.casefold()
        if not recipient or normalized in seen:
            continue
        seen.add(normalized)
        recipients.append(recipient)
    return recipients
