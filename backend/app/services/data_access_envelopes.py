from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Sequence

from sqlalchemy import ColumnElement, delete, exists, false, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
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


@dataclass(frozen=True, slots=True)
class DataAccessSourceInput:
    source_type: str
    source_id: str
    source_version: str
    handling_label_id: uuid.UUID
    captured_policy_revision: int
    source_feed_id: uuid.UUID | None = None
    source_parent_id: uuid.UUID | None = None
    source_digest: str | None = None
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DataAccessSourceSnapshot:
    id: uuid.UUID
    envelope_id: uuid.UUID
    source_type: str
    source_id: str
    source_version: str
    source_feed_id: uuid.UUID | None
    source_parent_id: uuid.UUID | None
    handling_label_id: uuid.UUID
    captured_policy_revision: int
    source_digest: str | None
    captured_at: datetime


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
    from app.services import data_access_lineage as lineage

    normalized_type = _validate_resource_type(resource_type)
    normalized_counts = _normalize_label_counts(label_counts)
    if source_count < 0:
        raise DataAccessEnvelopeConflict(
            "Data access envelope source count cannot be negative."
        )
    _validate_aggregate_totals(normalized_counts, source_count)
    policy_revision = _lock_policy_revision_for_lineage(db)
    _validate_active_labels(db, normalized_counts)
    _require_aggregate_compatibility_mode(db)

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
    if lineage.has_normalized_sources(db, envelope.id):
        lineage.validate_normalized_source_invariants(
            db,
            envelope=envelope,
            aggregate_counts=current_counts,
        )
        if (
            current_counts == normalized_counts
            and envelope.source_count == source_count
        ):
            return _snapshot(envelope, current_counts)
        raise DataAccessEnvelopeConflict(
            "Normalized data access lineage already exists for this resource and "
            "cannot be rewritten through the aggregate compatibility API.",
            context={
                "resource_type": normalized_type,
                "resource_id": str(resource_id),
            },
        )
    if current_counts and not replace:
        if current_counts != normalized_counts or envelope.source_count != source_count:
            raise DataAccessEnvelopeConflict(
                "A different data access envelope already exists for this resource.",
                context={
                    "resource_type": resource_type,
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
    normalized_increment = _normalize_label_counts(label_counts)
    _validate_aggregate_totals(normalized_increment, source_count_increment)
    _lock_policy_revision_for_lineage(db)
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
            label_counts=normalized_increment,
            source_count=source_count_increment,
        )
    merged = dict(current.label_counts)
    for label_id, count in normalized_increment.items():
        merged[label_id] = merged.get(label_id, 0) + count
    return put_data_access_envelope(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        label_counts=merged,
        source_count=current.source_count + source_count_increment,
        replace=True,
    )


def put_data_access_envelope_sources(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    sources: Sequence[DataAccessSourceInput],
) -> DataAccessEnvelopeSnapshot:
    return _write_data_access_envelope_sources(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        sources=sources,
        operation="put",
    )


def replace_data_access_envelope_sources(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    sources: Sequence[DataAccessSourceInput],
) -> DataAccessEnvelopeSnapshot:
    return _write_data_access_envelope_sources(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        sources=sources,
        operation="replace",
    )


def merge_data_access_envelope_sources(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    sources: Sequence[DataAccessSourceInput],
) -> DataAccessEnvelopeSnapshot:
    return _write_data_access_envelope_sources(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        sources=sources,
        operation="merge",
    )


def get_data_access_envelope_sources(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    for_update: bool = False,
) -> tuple[DataAccessSourceSnapshot, ...]:
    from app.services import data_access_lineage as lineage

    envelope = _get_envelope_model(
        db,
        resource_type=_validate_resource_type(resource_type),
        resource_id=resource_id,
        for_update=for_update,
    )
    if envelope is None:
        return ()
    return tuple(
        lineage.source_snapshot(source)
        for source in lineage.source_models(
            db,
            envelope.id,
            for_update=for_update,
        )
    )


def copy_data_access_envelope_lineage(
    db: Session,
    *,
    source_resource_type: str,
    source_resource_id: uuid.UUID,
    target_resource_type: str,
    target_resource_id: uuid.UUID,
    operation: Literal["put", "replace", "merge"] = "put",
) -> DataAccessEnvelopeSnapshot:
    from app.services import data_access_lineage as lineage

    _lock_policy_revision_for_lineage(db)
    normalized_source_type = _validate_resource_type(source_resource_type)
    normalized_target_type = _validate_resource_type(target_resource_type)
    if (
        normalized_source_type == normalized_target_type
        and source_resource_id == target_resource_id
    ):
        raise DataAccessEnvelopeConflict(
            "A data access envelope cannot copy lineage from itself."
        )

    source_envelope = _get_envelope_model(
        db,
        resource_type=normalized_source_type,
        resource_id=source_resource_id,
        for_update=True,
    )
    if source_envelope is None:
        raise DataPolicyUnavailable(
            "The source data access envelope is missing. Retry after repairing data-policy provenance.",
            context={
                "resource_type": normalized_source_type,
                "resource_id": str(source_resource_id),
            },
        )
    source_rows = lineage.source_models(db, source_envelope.id, for_update=True)
    if not source_rows:
        raise DataPolicyUnavailable(
            "The source data access envelope has no normalized lineage. Repair provenance before copying it.",
            context={
                "resource_type": normalized_source_type,
                "resource_id": str(source_resource_id),
            },
        )
    lineage.validate_normalized_source_invariants(
        db,
        envelope=source_envelope,
        aggregate_counts=_label_counts(db, source_envelope.id),
        source_rows=source_rows,
    )
    copied_sources = [
        DataAccessSourceInput(
            source_type=source.source_type,
            source_id=source.source_id,
            source_version=source.source_version,
            source_feed_id=source.source_feed_id,
            source_parent_id=source.id,
            handling_label_id=source.handling_label_id,
            captured_policy_revision=source.captured_policy_revision,
            source_digest=source.source_digest,
            captured_at=source.captured_at,
        )
        for source in source_rows
    ]
    writer = {
        "put": put_data_access_envelope_sources,
        "replace": replace_data_access_envelope_sources,
        "merge": merge_data_access_envelope_sources,
    }.get(operation)
    if writer is None:
        raise DataAccessEnvelopeConflict(
            "Unsupported data access lineage copy operation.",
            context={"operation": operation},
        )
    return writer(
        db,
        resource_type=normalized_target_type,
        resource_id=target_resource_id,
        sources=copied_sources,
    )


def taint_data_access_envelopes_for_feed(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
    policy_revision: int | None = None,
) -> int:
    from app.services import data_access_lineage as lineage

    if not isinstance(feed_id, uuid.UUID):
        raise DataAccessEnvelopeConflict(
            "Feed tainting requires a UUID feed identifier."
        )
    current_revision = _lock_policy_revision_for_lineage(db)
    captured_revision = current_revision if policy_revision is None else policy_revision
    feed = db.scalar(
        select(Feed)
        .where(Feed.id == feed_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if feed is None:
        raise DataAccessEnvelopeConflict(
            "The feed referenced by the data access lineage does not exist.",
            context={"feed_id": str(feed_id)},
        )
    if feed.handling_label_id != handling_label_id:
        raise DataAccessEnvelopeConflict(
            "Feed lineage must use the feed's current handling label.",
            context={"feed_id": str(feed_id)},
        )
    _validate_active_labels(db, {handling_label_id: 1})

    return lineage.taint_sources_for_feed(
        db,
        feed_id=feed_id,
        handling_label_id=handling_label_id,
        policy_revision=captured_revision,
    )


def copy_data_access_envelope(
    db: Session,
    *,
    source_resource_type: str,
    source_resource_id: uuid.UUID,
    target_resource_type: str,
    target_resource_id: uuid.UUID,
) -> DataAccessEnvelopeSnapshot:
    from app.services import data_access_lineage as lineage

    _lock_policy_revision_for_lineage(db)
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
    if lineage.has_normalized_sources(db, source.envelope_id):
        return copy_data_access_envelope_lineage(
            db,
            source_resource_type=source_resource_type,
            source_resource_id=source_resource_id,
            target_resource_type=target_resource_type,
            target_resource_id=target_resource_id,
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
    from app.services import data_access_lineage as lineage

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
    source_rows = lineage.source_models(db, envelope.id, for_update=for_update)
    if source_rows:
        lineage.validate_normalized_source_invariants(
            db,
            envelope=envelope,
            aggregate_counts=counts,
            source_rows=source_rows,
        )
    elif _current_coverage_version(db) > 0:
        raise DataPolicyUnavailable(
            "A data access envelope has no normalized source lineage. Repair provenance before serving this resource.",
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
        allowed=context.principal_eligible
        and (not context.enforced or not inaccessible),
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
    if context.coverage_version > 0:
        source = aliased(DataAccessEnvelopeSource)
        active_label = aliased(HandlingLabel)
        source_count = (
            select(func.count(source.id))
            .where(source.envelope_id == envelope.id)
            .scalar_subquery()
        )
        max_source_revision = (
            select(func.max(source.captured_policy_revision))
            .where(source.envelope_id == envelope.id)
            .scalar_subquery()
        )
        invalid_source = exists(
            select(source.id).where(
                source.envelope_id == envelope.id,
                or_(
                    source.handling_label_id.not_in(context.allowed_label_ids),
                    ~exists(
                        select(active_label.id).where(
                            active_label.id == source.handling_label_id,
                            active_label.is_active.is_(True),
                        )
                    ),
                ),
            )
        )
        return exists(
            select(envelope.id).where(
                envelope.resource_type == normalized_type,
                envelope.resource_id == resource_id_column,
                envelope.source_count > 0,
                source_count == envelope.source_count,
                envelope.policy_revision >= max_source_revision,
                ~invalid_source,
            )
        )

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


def _write_data_access_envelope_sources(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    sources: Sequence[DataAccessSourceInput],
    operation: Literal["put", "replace", "merge"],
) -> DataAccessEnvelopeSnapshot:
    from app.services import data_access_lineage as lineage

    normalized_type = _validate_resource_type(resource_type)
    if not isinstance(resource_id, uuid.UUID):
        raise DataAccessEnvelopeConflict(
            "Data access envelopes require UUID resource identifiers."
        )
    current_revision = _lock_policy_revision_for_lineage(db)
    normalized_sources = lineage.normalize_sources(
        sources,
        current_revision=current_revision,
    )
    _validate_active_labels(
        db,
        {source.handling_label_id: 1 for source in normalized_sources},
    )
    with db.begin_nested():
        return _persist_data_access_envelope_sources(
            db,
            resource_type=normalized_type,
            resource_id=resource_id,
            sources=normalized_sources,
            operation=operation,
            current_revision=current_revision,
        )


def _persist_data_access_envelope_sources(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    sources: Sequence[DataAccessSourceInput],
    operation: Literal["put", "replace", "merge"],
    current_revision: int,
) -> DataAccessEnvelopeSnapshot:
    from app.services import data_access_lineage as lineage

    envelope = _get_envelope_model(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        for_update=True,
    )
    lineage.validate_source_references(
        db,
        envelope_id=envelope.id if envelope is not None else None,
        sources=sources,
    )
    if envelope is None:
        envelope = _get_or_create_envelope(
            db,
            resource_type=resource_type,
            resource_id=resource_id,
            policy_revision=current_revision,
        )

    existing_rows = lineage.source_models(db, envelope.id, for_update=True)
    existing_by_identity = {
        lineage.source_identity_from_model(row): row for row in existing_rows
    }
    desired_by_identity = {
        lineage.source_identity(source): source for source in sources
    }
    aggregate_counts = _label_counts(db, envelope.id)

    if not existing_rows and aggregate_counts:
        desired_counts = lineage.source_label_counts(sources)
        aggregate_matches = (
            aggregate_counts == desired_counts and envelope.source_count == len(sources)
        )
        if operation != "replace" and not aggregate_matches:
            raise DataAccessEnvelopeConflict(
                "An aggregate-only data access envelope cannot be merged with "
                "partial normalized lineage; replace its complete source set.",
                context={
                    "resource_type": resource_type,
                    "resource_id": str(resource_id),
                },
            )

    changed = False
    if existing_rows and operation == "put":
        if set(existing_by_identity) != set(desired_by_identity):
            raise DataAccessEnvelopeConflict(
                "A different normalized data access lineage already exists for this resource.",
                context={
                    "resource_type": resource_type,
                    "resource_id": str(resource_id),
                },
            )
        for identity, source in desired_by_identity.items():
            lineage.require_matching_source(existing_by_identity[identity], source)
    else:
        for identity, source in desired_by_identity.items():
            existing = existing_by_identity.get(identity)
            if existing is not None:
                lineage.require_matching_source(existing, source)
                continue
            db.add(lineage.source_model(envelope.id, source))
            changed = True

        if operation == "replace":
            removed_ids = [
                row.id
                for identity, row in existing_by_identity.items()
                if identity not in desired_by_identity
            ]
            if removed_ids:
                lineage.assert_sources_not_referenced(db, removed_ids)
                db.execute(
                    delete(DataAccessEnvelopeSource).where(
                        DataAccessEnvelopeSource.id.in_(removed_ids)
                    )
                )
                changed = True

    if changed or not existing_rows:
        db.flush()
        lineage.rebuild_source_aggregates(
            db,
            envelope,
            current_revision=current_revision,
        )
    else:
        lineage.validate_normalized_source_invariants(
            db,
            envelope=envelope,
            aggregate_counts=aggregate_counts,
            source_rows=existing_rows,
        )
    return _snapshot(envelope, _label_counts(db, envelope.id))


def _get_or_create_envelope(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    policy_revision: int,
) -> DataAccessEnvelope:
    envelope = _get_envelope_model(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        for_update=True,
    )
    if envelope is not None:
        return envelope

    envelope = DataAccessEnvelope(
        resource_type=resource_type,
        resource_id=resource_id,
        source_count=0,
        policy_revision=policy_revision,
    )
    try:
        with db.begin_nested():
            db.add(envelope)
            db.flush()
    except IntegrityError as exc:
        envelope = _get_envelope_model(
            db,
            resource_type=resource_type,
            resource_id=resource_id,
            for_update=True,
        )
        if envelope is None:
            raise DataPolicyUnavailable(
                "The data access envelope could not be created or reloaded. Retry the operation."
            ) from exc
    return envelope


def _get_envelope_model(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    for_update: bool,
) -> DataAccessEnvelope | None:
    statement = select(DataAccessEnvelope).where(
        DataAccessEnvelope.resource_type == resource_type,
        DataAccessEnvelope.resource_id == resource_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement.execution_options(populate_existing=True))


def _validate_aggregate_totals(
    label_counts: Mapping[uuid.UUID, int], source_count: int
) -> None:
    if sum(label_counts.values()) != source_count:
        raise DataAccessEnvelopeConflict(
            "Data access envelope source counts must equal the sum of label counts."
        )


def _require_aggregate_compatibility_mode(db: Session) -> None:
    if _current_coverage_version(db) > 0:
        raise DataPolicyUnavailable(
            "Aggregate-only data access envelopes are no longer accepted after normalized lineage coverage is enabled."
        )


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


def _validate_active_labels(db: Session, label_counts: Mapping[uuid.UUID, int]) -> None:
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
    revision = db.scalar(
        select(DataPolicyState.revision).where(DataPolicyState.id == 1)
    )
    if revision is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore it before creating derived data."
        )
    return int(revision)


def _lock_policy_revision_for_lineage(db: Session) -> int:
    revision = db.scalar(
        select(DataPolicyState.revision)
        .where(DataPolicyState.id == 1)
        .with_for_update(read=True)
    )
    if revision is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore it before creating derived data."
        )
    return int(revision)


def _current_coverage_version(db: Session) -> int:
    coverage_version = db.scalar(
        select(DataPolicyState.coverage_version).where(DataPolicyState.id == 1)
    )
    if coverage_version is None:
        raise DataPolicyUnavailable(
            "Data policy state is missing. Restore it before using data access envelopes."
        )
    return int(coverage_version)


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
    "DataAccessSourceInput",
    "DataAccessSourceSnapshot",
    "DataPolicyEgressDenied",
    "SUPPORTED_DATA_ACCESS_RESOURCE_TYPES",
    "copy_data_access_envelope",
    "copy_data_access_envelope_lineage",
    "data_access_envelope_predicate",
    "evaluate_data_access_envelope",
    "get_data_access_envelope",
    "get_data_access_envelope_sources",
    "merge_data_access_envelope",
    "merge_data_access_envelope_sources",
    "put_data_access_envelope",
    "put_data_access_envelope_sources",
    "replace_data_access_envelope_sources",
    "require_data_access_for_egress",
    "taint_data_access_envelopes_for_feed",
    "unrestricted_label_counts",
]
