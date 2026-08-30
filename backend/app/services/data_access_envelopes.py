from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import ColumnElement, delete, exists, false, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    DataPolicyUnavailable,
)


DATA_ACCESS_RESOURCE_REPORT = "report"
DATA_ACCESS_RESOURCE_DAILY_BRIEF = "ai_daily_brief"
DATA_ACCESS_RESOURCE_INVESTIGATION = "investigation"
DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE = "alert_occurrence"
DATA_ACCESS_RESOURCE_INTEGRATION_EVENT = "integration_event"
DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY = "integration_delivery"

SUPPORTED_DATA_ACCESS_RESOURCE_TYPES = frozenset(
    {
        DATA_ACCESS_RESOURCE_REPORT,
        DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        DATA_ACCESS_RESOURCE_INVESTIGATION,
        DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
        DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    }
)
_RESOURCE_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DataAccessEnvelopeConflict(DataPolicyError):
    code = "data_access_envelope_conflict"
    status_code = 409


class DataPolicyEgressDenied(DataPolicyError):
    code = "data_policy_egress_denied"
    status_code = 403


@dataclass(frozen=True)
class DataAccessEnvelopeSnapshot:
    envelope_id: uuid.UUID
    resource_type: str
    resource_id: uuid.UUID
    source_count: int
    policy_revision: int
    label_counts: Mapping[uuid.UUID, int]

    @property
    def label_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(self.label_counts)


@dataclass(frozen=True)
class DataAccessDecision:
    allowed: bool
    would_deny: bool
    envelope_missing: bool
    label_ids: frozenset[uuid.UUID]
    policy_revision: int | None


def put_data_access_envelope(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    label_counts: Mapping[uuid.UUID, int],
    source_count: int,
    replace: bool = False,
) -> DataAccessEnvelopeSnapshot:
    normalized_type = _validate_resource_type(resource_type)
    normalized_counts = _normalize_label_counts(label_counts)
    if source_count < 0:
        raise DataAccessEnvelopeConflict(
            "Data access envelope source count cannot be negative."
        )
    _validate_active_labels(db, normalized_counts)
    policy_revision = _current_policy_revision(db)

    envelope = db.scalar(
        select(DataAccessEnvelope)
        .where(
            DataAccessEnvelope.resource_type == normalized_type,
            DataAccessEnvelope.resource_id == resource_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if envelope is None:
        envelope = DataAccessEnvelope(
            resource_type=normalized_type,
            resource_id=resource_id,
            source_count=source_count,
            policy_revision=policy_revision,
        )
        try:
            with db.begin_nested():
                db.add(envelope)
                db.flush()
        except IntegrityError as exc:
            envelope = db.scalar(
                select(DataAccessEnvelope)
                .where(
                    DataAccessEnvelope.resource_type == normalized_type,
                    DataAccessEnvelope.resource_id == resource_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if envelope is None:
                raise DataPolicyUnavailable(
                    "The data access envelope could not be created or reloaded. Retry the operation."
                ) from exc

    current_counts = _label_counts(db, envelope.id)
    if current_counts and not replace:
        if current_counts != normalized_counts or envelope.source_count != source_count:
            raise DataAccessEnvelopeConflict(
                "A different data access envelope already exists for this resource.",
                context={
                    "resource_type": normalized_type,
                    "resource_id": str(resource_id),
                },
            )
        return _snapshot(envelope, current_counts)

    if current_counts:
        db.execute(
            delete(DataAccessEnvelopeLabel).where(
                DataAccessEnvelopeLabel.envelope_id == envelope.id
            )
        )
    envelope.source_count = source_count
    envelope.policy_revision = policy_revision
    db.add(envelope)
    db.add_all(
        [
            DataAccessEnvelopeLabel(
                envelope_id=envelope.id,
                label_id=label_id,
                source_count=count,
            )
            for label_id, count in normalized_counts.items()
        ]
    )
    db.flush()
    return _snapshot(envelope, normalized_counts)


def merge_data_access_envelope(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    label_counts: Mapping[uuid.UUID, int],
    source_count_increment: int,
) -> DataAccessEnvelopeSnapshot:
    if source_count_increment < 0:
        raise DataAccessEnvelopeConflict(
            "Data access envelope source increment cannot be negative."
        )
    current = get_data_access_envelope(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        for_update=True,
    )
    if current is None:
        return put_data_access_envelope(
            db,
            resource_type=resource_type,
            resource_id=resource_id,
            label_counts=label_counts,
            source_count=source_count_increment,
        )
    merged = dict(current.label_counts)
    for label_id, count in _normalize_label_counts(label_counts).items():
        merged[label_id] = merged.get(label_id, 0) + count
    return put_data_access_envelope(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        label_counts=merged,
        source_count=current.source_count + source_count_increment,
        replace=True,
    )


def copy_data_access_envelope(
    db: Session,
    *,
    source_resource_type: str,
    source_resource_id: uuid.UUID,
    target_resource_type: str,
    target_resource_id: uuid.UUID,
) -> DataAccessEnvelopeSnapshot:
    source = get_data_access_envelope(
        db,
        resource_type=source_resource_type,
        resource_id=source_resource_id,
    )
    if source is None:
        raise DataPolicyUnavailable(
            "The source data access envelope is missing. Retry after repairing data-policy provenance.",
            context={
                "resource_type": source_resource_type,
                "resource_id": str(source_resource_id),
            },
        )
    return put_data_access_envelope(
        db,
        resource_type=target_resource_type,
        resource_id=target_resource_id,
        label_counts=source.label_counts,
        source_count=source.source_count,
    )


def get_data_access_envelope(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    for_update: bool = False,
) -> DataAccessEnvelopeSnapshot | None:
    normalized_type = _validate_resource_type(resource_type)
    statement = select(DataAccessEnvelope).where(
        DataAccessEnvelope.resource_type == normalized_type,
        DataAccessEnvelope.resource_id == resource_id,
    )
    if for_update:
        statement = statement.with_for_update()
    envelope = db.scalar(statement.execution_options(populate_existing=True))
    if envelope is None:
        return None
    counts = _label_counts(db, envelope.id)
    if not counts:
        raise DataPolicyUnavailable(
            "A data access envelope has no handling labels. Repair provenance before serving this resource.",
            context={
                "resource_type": normalized_type,
                "resource_id": str(resource_id),
            },
        )
    return _snapshot(envelope, counts)


def evaluate_data_access_envelope(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    context: DataAccessContext,
) -> DataAccessDecision:
    envelope = get_data_access_envelope(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if envelope is None:
        return DataAccessDecision(
            allowed=not context.enforced and context.principal_eligible,
            would_deny=context.auditing,
            envelope_missing=True,
            label_ids=frozenset(),
            policy_revision=None,
        )
    inaccessible = not envelope.label_ids.issubset(context.allowed_label_ids)
    return DataAccessDecision(
        allowed=context.principal_eligible and (not context.enforced or not inaccessible),
        would_deny=context.auditing and inaccessible,
        envelope_missing=False,
        label_ids=envelope.label_ids,
        policy_revision=envelope.policy_revision,
    )


def require_data_access_for_egress(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    context: DataAccessContext,
) -> DataAccessDecision:
    decision = evaluate_data_access_envelope(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        context=context,
    )
    if decision.envelope_missing and context.mode in {"audit", "enforced"}:
        raise DataPolicyUnavailable(
            "Outbound delivery is paused because data-policy provenance is missing. Repair the source envelope and retry.",
            context={
                "resource_type": resource_type,
                "resource_id": str(resource_id),
            },
        )
    if not decision.allowed:
        raise DataPolicyEgressDenied(
            "Outbound delivery is not allowed for the principal's handling-label grants.",
            context={
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "policy_revision": context.policy_revision,
            },
        )
    return decision


def data_access_envelope_predicate(
    resource_type: str,
    resource_id_column,
    context: DataAccessContext,
) -> ColumnElement[bool]:
    normalized_type = _validate_resource_type(resource_type)
    if not context.principal_eligible:
        return false()
    if not context.enforced:
        return true()

    envelope = aliased(DataAccessEnvelope)
    label = aliased(DataAccessEnvelopeLabel)
    any_label = exists(
        select(label.envelope_id).where(label.envelope_id == envelope.id)
    )
    unauthorized_label = exists(
        select(label.envelope_id).where(
            label.envelope_id == envelope.id,
            label.label_id.not_in(context.allowed_label_ids),
        )
    )
    return exists(
        select(envelope.id).where(
            envelope.resource_type == normalized_type,
            envelope.resource_id == resource_id_column,
            any_label,
            ~unauthorized_label,
        )
    )


def unrestricted_label_counts(source_count: int = 1) -> dict[uuid.UUID, int]:
    return {UNRESTRICTED_HANDLING_LABEL_ID: max(1, source_count)}


def _validate_resource_type(resource_type: str) -> str:
    normalized = resource_type.strip().lower()
    if (
        not _RESOURCE_TYPE_PATTERN.fullmatch(normalized)
        or normalized not in SUPPORTED_DATA_ACCESS_RESOURCE_TYPES
    ):
        raise DataAccessEnvelopeConflict(
            "Unsupported data access envelope resource type.",
            context={"resource_type": resource_type},
        )
    return normalized


def _normalize_label_counts(
    label_counts: Mapping[uuid.UUID, int],
) -> dict[uuid.UUID, int]:
    normalized: dict[uuid.UUID, int] = {}
    for label_id, count in label_counts.items():
        if not isinstance(label_id, uuid.UUID) or count < 1:
            raise DataAccessEnvelopeConflict(
                "Data access envelope labels require UUID identifiers and positive source counts."
            )
        normalized[label_id] = normalized.get(label_id, 0) + count
    if not normalized:
        raise DataAccessEnvelopeConflict(
            "Every data access envelope must contain at least one handling label."
        )
    return normalized


def _validate_active_labels(
    db: Session, label_counts: Mapping[uuid.UUID, int]
) -> None:
    active_ids = set(
        db.scalars(
            select(HandlingLabel.id).where(
                HandlingLabel.id.in_(label_counts),
                HandlingLabel.is_active.is_(True),
            )
        ).all()
    )
    missing = sorted(set(label_counts) - active_ids, key=str)
    if missing:
        raise DataPolicyUnavailable(
            "One or more source handling labels are missing or inactive. Repair data-policy provenance before continuing.",
            context={"handling_label_ids": [str(value) for value in missing]},
        )


def _current_policy_revision(db: Session) -> int:
    revision = db.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
    if revision is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore it before creating derived data."
        )
    return int(revision)


def _label_counts(db: Session, envelope_id: uuid.UUID) -> dict[uuid.UUID, int]:
    return {
        label_id: int(source_count)
        for label_id, source_count in db.execute(
            select(
                DataAccessEnvelopeLabel.label_id,
                DataAccessEnvelopeLabel.source_count,
            ).where(DataAccessEnvelopeLabel.envelope_id == envelope_id)
        ).all()
    }


def _snapshot(
    envelope: DataAccessEnvelope,
    label_counts: Mapping[uuid.UUID, int],
) -> DataAccessEnvelopeSnapshot:
    return DataAccessEnvelopeSnapshot(
        envelope_id=envelope.id,
        resource_type=envelope.resource_type,
        resource_id=envelope.resource_id,
        source_count=envelope.source_count,
        policy_revision=envelope.policy_revision,
        label_counts=dict(label_counts),
    )


__all__ = [
    "DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE",
    "DATA_ACCESS_RESOURCE_DAILY_BRIEF",
    "DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY",
    "DATA_ACCESS_RESOURCE_INTEGRATION_EVENT",
    "DATA_ACCESS_RESOURCE_INVESTIGATION",
    "DATA_ACCESS_RESOURCE_REPORT",
    "DataAccessDecision",
    "DataAccessEnvelopeConflict",
    "DataAccessEnvelopeSnapshot",
    "DataPolicyEgressDenied",
    "SUPPORTED_DATA_ACCESS_RESOURCE_TYPES",
    "copy_data_access_envelope",
    "data_access_envelope_predicate",
    "evaluate_data_access_envelope",
    "get_data_access_envelope",
    "merge_data_access_envelope",
    "put_data_access_envelope",
    "require_data_access_for_egress",
    "unrestricted_label_counts",
]
