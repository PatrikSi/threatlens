from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.audit_log import AuditLog, AuditLogDataAccessLabel
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.audit import AuditLogResponse
from app.services.ai_ops_common import AI_TASK_TYPE_CONNECTION_TEST
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    DATA_ACCESS_RESOURCE_REPORT,
    get_data_access_envelope,
)
from app.services.data_access_policy import DataAccessContext, DataPolicyError


_ENVELOPE_RESOURCE_TYPES = frozenset(
    {
        DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
        DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        DATA_ACCESS_RESOURCE_INVESTIGATION,
        DATA_ACCESS_RESOURCE_REPORT,
    }
)
_DIRECT_GOVERNED_RESOURCE_TYPES = frozenset(
    {
        "feed",
        "item",
        "item_ai_enrichment",
        "daily_brief",
        "notification_webhook_delivery",
        "ai_task_run",
        "ai_provider_attempt_receipt",
    }
)


@dataclass(frozen=True, slots=True)
class AuditDataAccessProjection:
    logs: tuple[AuditLogResponse, ...]
    affected_count: int
    handling_label_ids: frozenset[uuid.UUID]


def resolve_audit_data_access_labels(
    db: Session,
    *,
    resource_type: str,
    resource_id: str | None,
) -> frozenset[uuid.UUID] | None:
    """Resolve an immutable label snapshot for a newly written audit record.

    ``None`` means the resource type is not data governed. An empty set means the
    resource is governed but its provenance cannot be established, which remains
    fail-closed when policy is active.
    """

    if resource_type not in _ENVELOPE_RESOURCE_TYPES | _DIRECT_GOVERNED_RESOURCE_TYPES:
        return None
    if resource_id is None:
        return None
    resource_uuid = _uuid_or_none(resource_id)
    if resource_uuid is None:
        return frozenset()

    if resource_type == "feed":
        return _feed_labels(db, resource_uuid)
    if resource_type in {"item", "item_ai_enrichment"}:
        return _item_labels(db, resource_uuid)
    if resource_type in _ENVELOPE_RESOURCE_TYPES:
        return _envelope_labels(db, resource_type, resource_uuid)
    if resource_type == "daily_brief":
        return _envelope_labels(
            db,
            DATA_ACCESS_RESOURCE_DAILY_BRIEF,
            resource_uuid,
        )
    if resource_type == "notification_webhook_delivery":
        return _notification_delivery_labels(db, resource_uuid)
    if resource_type == "ai_task_run":
        return _ai_task_run_labels(db, resource_uuid)
    if resource_type == "ai_provider_attempt_receipt":
        return _ai_provider_receipt_labels(db, resource_uuid)
    return frozenset()


def project_audit_logs(
    db: Session,
    rows: Sequence[AuditLog],
    *,
    context: DataAccessContext,
) -> AuditDataAccessProjection:
    normalized_labels: dict[uuid.UUID, set[uuid.UUID]] = {}
    row_ids = [row.id for row in rows]
    if row_ids:
        for audit_log_id, label_id in db.execute(
            select(
                AuditLogDataAccessLabel.audit_log_id,
                AuditLogDataAccessLabel.label_id,
            ).where(AuditLogDataAccessLabel.audit_log_id.in_(row_ids))
        ).all():
            normalized_labels.setdefault(audit_log_id, set()).add(label_id)

    projected: list[AuditLogResponse] = []
    affected_count = 0
    restricted_label_ids: set[uuid.UUID] = set()
    for row in rows:
        response = AuditLogResponse.model_validate(row)
        normalized = normalized_labels.get(row.id, set())
        governed = row.data_access_governed or bool(normalized)
        if context.mode == "disabled" or not governed:
            projected.append(response)
            continue

        stored_label_ids, valid_snapshot = _stored_label_ids(
            row.data_access_label_ids
        )
        label_ids = frozenset({*stored_label_ids, *normalized})
        restricted = (
            not valid_snapshot
            or not label_ids
            or not label_ids.issubset(context.allowed_label_ids)
        )
        if not restricted:
            projected.append(response)
            continue

        affected_count += 1
        restricted_label_ids.update(label_ids)
        if not valid_snapshot or not label_ids:
            restricted_label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
        if context.auditing:
            projected.append(response)
            continue
        projected.append(
            response.model_copy(
                update={
                    "request_id": None,
                    "authorization_approval_id": None,
                    "execution_receipt_id": None,
                    "resource_id": None,
                    "metadata_json": {
                        "data_access_redacted": True,
                        "reason": "handling_label_access_required",
                    },
                    "data_access_redacted": True,
                }
            )
        )
    return AuditDataAccessProjection(
        logs=tuple(projected),
        affected_count=affected_count,
        handling_label_ids=frozenset(restricted_label_ids),
    )


def _feed_labels(db: Session, feed_id: uuid.UUID) -> frozenset[uuid.UUID]:
    pending = _session_resource(db, Feed, feed_id)
    if pending is not None:
        return frozenset({pending.handling_label_id})
    with db.no_autoflush:
        label_id = db.scalar(select(Feed.handling_label_id).where(Feed.id == feed_id))
    return frozenset({label_id}) if label_id is not None else frozenset()


def _item_labels(db: Session, item_id: uuid.UUID) -> frozenset[uuid.UUID]:
    pending = _session_resource(db, Item, item_id)
    feed_id = pending.feed_id if pending is not None else None
    if feed_id is None:
        with db.no_autoflush:
            feed_id = db.scalar(select(Item.feed_id).where(Item.id == item_id))
    return _feed_labels(db, feed_id) if feed_id is not None else frozenset()


def _envelope_labels(
    db: Session,
    resource_type: str,
    resource_id: uuid.UUID,
) -> frozenset[uuid.UUID]:
    try:
        envelope = get_data_access_envelope(
            db,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    except DataPolicyError:
        return frozenset()
    return envelope.label_ids if envelope is not None else frozenset()


def _notification_delivery_labels(
    db: Session,
    delivery_id: uuid.UUID,
) -> frozenset[uuid.UUID]:
    delivery = _session_resource(db, NotificationWebhookDelivery, delivery_id)
    if delivery is None:
        with db.no_autoflush:
            delivery = db.get(NotificationWebhookDelivery, delivery_id)
    if delivery is None:
        return frozenset()

    labels: set[uuid.UUID] = set()
    if delivery.integration_delivery_id is not None:
        labels.update(
            _envelope_labels(
                db,
                DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
                delivery.integration_delivery_id,
            )
        )
    if delivery.item_id is not None:
        labels.update(_item_labels(db, delivery.item_id))
    if delivery.feed_id is not None:
        labels.update(_feed_labels(db, delivery.feed_id))
    return frozenset(labels)


def _ai_task_run_labels(
    db: Session,
    run_id: uuid.UUID,
) -> frozenset[uuid.UUID] | None:
    run = _session_resource(db, AITaskRun, run_id)
    if run is None:
        with db.no_autoflush:
            run = db.get(AITaskRun, run_id)
    if run is None:
        return frozenset()
    if run.task_type == AI_TASK_TYPE_CONNECTION_TEST:
        return None

    labels: set[uuid.UUID] = set()
    if run.item_id is not None:
        labels.update(_item_labels(db, run.item_id))
    if run.daily_brief_id is not None:
        labels.update(
            _envelope_labels(
                db,
                DATA_ACCESS_RESOURCE_DAILY_BRIEF,
                run.daily_brief_id,
            )
        )
    if run.report_id is not None:
        labels.update(
            _envelope_labels(
                db,
                DATA_ACCESS_RESOURCE_REPORT,
                run.report_id,
            )
        )
    return frozenset(labels)


def _ai_provider_receipt_labels(
    db: Session,
    receipt_id: uuid.UUID,
) -> frozenset[uuid.UUID]:
    receipt = _session_resource(db, AIProviderAttemptReceipt, receipt_id)
    if receipt is None:
        with db.no_autoflush:
            receipt = db.get(AIProviderAttemptReceipt, receipt_id)
    if receipt is None or receipt.resource_id is None:
        return frozenset()
    if receipt.resource_type in {"item", "item_ai_enrichment"}:
        return _item_labels(db, receipt.resource_id)
    if receipt.resource_type in {"daily_brief", DATA_ACCESS_RESOURCE_DAILY_BRIEF}:
        return _envelope_labels(
            db,
            DATA_ACCESS_RESOURCE_DAILY_BRIEF,
            receipt.resource_id,
        )
    if receipt.resource_type == DATA_ACCESS_RESOURCE_REPORT:
        return _envelope_labels(
            db,
            DATA_ACCESS_RESOURCE_REPORT,
            receipt.resource_id,
        )
    return frozenset()


def _session_resource(db: Session, model, resource_id: uuid.UUID):
    for collection in (db.new, db.dirty, db.deleted):
        for candidate in collection:
            if isinstance(candidate, model) and candidate.id == resource_id:
                return candidate
    return None


def _stored_label_ids(
    values: Iterable[object],
) -> tuple[frozenset[uuid.UUID], bool]:
    normalized: set[uuid.UUID] = set()
    for value in values:
        parsed = _uuid_or_none(value)
        if parsed is None:
            return frozenset(), False
        normalized.add(parsed)
    return frozenset(normalized), True


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "AuditDataAccessProjection",
    "project_audit_logs",
    "resolve_audit_data_access_labels",
]
