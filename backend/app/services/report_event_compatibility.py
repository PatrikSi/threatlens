from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationEvent
from app.models.report import Report
from app.models.user import User
from app.services.integration_connectors.base import (
    IntegrationEventCompatibilityError,
    IntegrationEventContextError,
)


class ReportEventOwnerIneligible(IntegrationEventCompatibilityError):
    pass


def report_ready_event_owner_id(
    db: Session,
    *,
    event: IntegrationEvent,
    require_eligible: bool = True,
) -> uuid.UUID:
    """Return the immutable report owner, enriching legacy events once."""
    payload = event.payload_json if isinstance(event.payload_json, dict) else None
    if payload is None:
        raise IntegrationEventContextError("report_ready event has invalid payload")
    report_id = _validated_report_id(event, payload)

    if "owner_user_id" in payload:
        owner_user_id = _parse_owner_id(payload["owner_user_id"])
        if event.actor_user_id is not None and event.actor_user_id != owner_user_id:
            raise IntegrationEventContextError(
                "report_ready event owner does not match its immutable actor"
            )
        _require_owner(db, owner_user_id, require_eligible=require_eligible)
        return owner_user_id

    if event.schema_version != 1:
        raise IntegrationEventContextError(
            "report_ready event is missing its immutable owner"
        )
    owner_user_id = event.actor_user_id
    if owner_user_id is None:
        raise IntegrationEventContextError(
            "Legacy report_ready event is missing its immutable actor owner"
        )

    persisted_report = db.scalar(
        select(Report).where(Report.id == report_id).with_for_update(read=True)
    )
    if persisted_report is None:
        raise IntegrationEventContextError(
            f"Legacy report_ready event cannot resolve source report {report_id}"
        )
    if persisted_report.owner_user_id != owner_user_id:
        raise IntegrationEventContextError(
            "Legacy report_ready event actor does not match its source report owner"
        )
    _require_owner(db, owner_user_id, require_eligible=require_eligible)

    enriched_payload = dict(payload)
    enriched_payload["owner_user_id"] = str(owner_user_id)
    enriched_payload["schema_version"] = 2
    event.payload_json = enriched_payload
    event.schema_version = 2
    db.add(event)
    db.flush()
    return owner_user_id


def validate_report_ready_delivery_owner(
    db: Session,
    *,
    event: IntegrationEvent,
    delivery_payload: object,
    delivery_owner_user_id: uuid.UUID | None,
    require_eligible: bool = True,
) -> uuid.UUID:
    owner_user_id = report_ready_event_owner_id(
        db,
        event=event,
        require_eligible=require_eligible,
    )
    if not isinstance(delivery_payload, dict):
        raise IntegrationEventContextError("report_ready delivery has invalid payload")
    event_report_id = _validated_report_id(event, event.payload_json)
    delivery_report_id = _validated_payload_report_id(delivery_payload)
    if delivery_report_id != event_report_id:
        raise IntegrationEventContextError(
            "report_ready delivery does not match its source event"
        )
    payload_owner_id = _parse_owner_id(delivery_payload.get("owner_user_id"))
    if payload_owner_id != owner_user_id or delivery_owner_user_id != owner_user_id:
        raise IntegrationEventContextError(
            "report_ready delivery owner does not match its source event"
        )
    return owner_user_id


def _require_owner(
    db: Session,
    owner_user_id: uuid.UUID,
    *,
    require_eligible: bool,
) -> None:
    owner = db.scalar(
        select(User).where(User.id == owner_user_id).with_for_update(read=True)
    )
    if owner is None:
        raise IntegrationEventContextError("report_ready event owner no longer exists")
    if require_eligible and (not owner.is_active or not owner.is_approved):
        raise ReportEventOwnerIneligible(
            "report_ready event owner is temporarily inactive or unapproved"
        )


def _validated_report_id(event: IntegrationEvent, payload: dict) -> uuid.UUID:
    if event.source_type != "report":
        raise IntegrationEventContextError(
            "report_ready event is missing its source report"
        )
    source_id = _parse_report_id(event.source_id, label="source_id")
    payload_id = _validated_payload_report_id(payload)
    if source_id != payload_id:
        raise IntegrationEventContextError(
            "report_ready event report identifiers do not match"
        )
    return source_id


def _validated_payload_report_id(payload: dict) -> uuid.UUID:
    payload_id = _parse_report_id(payload.get("report_id"), label="report_id")
    snapshot = payload.get("daily_brief")
    if not isinstance(snapshot, dict):
        raise IntegrationEventContextError(
            "report_ready event is missing its report snapshot"
        )
    snapshot_id = _parse_report_id(snapshot.get("id"), label="daily_brief.id")
    if payload_id != snapshot_id:
        raise IntegrationEventContextError(
            "report_ready event report identifiers do not match"
        )
    return payload_id


def _parse_report_id(value: object, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrationEventContextError(
            f"report_ready event has invalid {label}"
        ) from exc


def _parse_owner_id(value: object) -> uuid.UUID:
    if value is None or value == "":
        raise IntegrationEventContextError(
            "report_ready event is missing owner_user_id"
        )
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrationEventContextError(
            "report_ready event has invalid owner_user_id"
        ) from exc
