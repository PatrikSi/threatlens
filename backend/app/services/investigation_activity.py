from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.investigation import InvestigationActivity


MAX_ACTIVITY_MEMBER_EMAIL_CHARS = 320
MAX_ACTIVITY_SOURCE_TITLE_CHARS = 512


def member_activity_details(
    *,
    member_email: str | None,
    role: str | None = None,
    from_role: str | None = None,
    to_role: str | None = None,
) -> dict[str, object]:
    details: dict[str, object] = {}
    if role is not None:
        details["role"] = role
    if from_role is not None:
        details["from_role"] = from_role
    if to_role is not None:
        details["to_role"] = to_role
    bounded_email = _bounded_activity_text(
        member_email,
        MAX_ACTIVITY_MEMBER_EMAIL_CHARS,
    )
    if bounded_email is not None:
        details["member_email"] = bounded_email
    return details


def evidence_activity_details(
    *,
    source_type: str,
    source_id: uuid.UUID,
    source_title: str,
) -> dict[str, object]:
    details: dict[str, object] = {
        "source_type": source_type,
        "source_id": str(source_id),
    }
    bounded_title = _bounded_activity_text(
        source_title,
        MAX_ACTIVITY_SOURCE_TITLE_CHARS,
    )
    if bounded_title is not None:
        details["source_title"] = bounded_title
    return details


def record_investigation_activity(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    details: dict[str, object],
) -> None:
    db.add(
        InvestigationActivity(
            investigation_id=investigation_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details_json=details,
        )
    )


def _bounded_activity_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


__all__ = [
    "evidence_activity_details",
    "member_activity_details",
    "record_investigation_activity",
]
