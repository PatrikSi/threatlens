from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.ai_daily_brief import AIDailyBrief
from app.models.alert_occurrence import AlertOccurrence
from app.models.data_policy import DataAccessEnvelope, DataAccessEnvelopeSource
from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.models.investigation import Investigation
from app.models.report import Report
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    DATA_ACCESS_RESOURCE_REPORT,
)


logger = logging.getLogger(__name__)

DataAccessResourceRef = tuple[str, uuid.UUID]

_RESOURCE_MODELS = {
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE: AlertOccurrence,
    DATA_ACCESS_RESOURCE_DAILY_BRIEF: AIDailyBrief,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY: IntegrationDelivery,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT: IntegrationEvent,
    DATA_ACCESS_RESOURCE_INVESTIGATION: Investigation,
    DATA_ACCESS_RESOURCE_REPORT: Report,
}
_TARGETED_QUERY_BATCH_SIZE = 100
_MAX_TARGETED_RESOURCES = 1_000


@dataclass(frozen=True, slots=True)
class DataAccessOrphanPruneResult:
    deleted_count: int
    candidates_scanned: int
    unknown_resource_types: int
    backlog_remaining: bool


def prune_deleted_resource_envelopes(
    db: Session,
    *,
    resources: Iterable[DataAccessResourceRef],
) -> int:
    """Delete a bounded set of missing-resource leaves and orphaned ancestors."""

    normalized = _normalize_resource_refs(resources)
    deleted_count = 0
    for offset in range(0, len(normalized), _TARGETED_QUERY_BATCH_SIZE):
        remaining_budget = _MAX_TARGETED_RESOURCES - deleted_count
        if remaining_budget <= 0:
            break
        batch = normalized[offset : offset + _TARGETED_QUERY_BATCH_SIZE]
        candidates = set(
            db.scalars(
                select(DataAccessEnvelope.id).where(
                    or_(
                        *(
                            and_(
                                DataAccessEnvelope.resource_type == resource_type,
                                DataAccessEnvelope.resource_id == resource_id,
                            )
                            for resource_type, resource_id in batch
                        )
                    )
                )
            ).all()
        )
        deleted_count += _delete_envelope_candidates(
            db,
            candidates=candidates,
            max_deletions=remaining_budget,
        )
    return deleted_count


def prune_orphan_data_access_envelopes(
    db: Session,
    *,
    limit: int = 1_000,
) -> DataAccessOrphanPruneResult:
    """Bound repair for missing-resource lineage leaves left by older workers."""

    bounded_limit = max(1, min(int(limit), 10_000))
    known_orphan = _known_orphan_predicate()
    orphan_leaf = and_(known_orphan, ~_lineage_child_exists())
    unknown_resource_types = int(
        db.scalar(
            select(func.count(DataAccessEnvelope.id)).where(
                DataAccessEnvelope.resource_type.not_in(tuple(_RESOURCE_MODELS))
            )
        )
        or 0
    )
    if unknown_resource_types:
        logger.warning(
            "data_access_retention_unknown_resource_types count=%s",
            unknown_resource_types,
        )

    candidates = set(
        db.scalars(
            select(DataAccessEnvelope.id)
            .where(orphan_leaf)
            .order_by(DataAccessEnvelope.created_at, DataAccessEnvelope.id)
            .limit(bounded_limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    deleted_count = _delete_envelope_candidates(
        db,
        candidates=candidates,
        max_deletions=bounded_limit,
    )
    backlog_remaining = bool(
        unknown_resource_types
        or db.scalar(select(exists().where(known_orphan, ~_lineage_child_exists())))
    )
    return DataAccessOrphanPruneResult(
        deleted_count=deleted_count,
        candidates_scanned=len(candidates),
        unknown_resource_types=unknown_resource_types,
        backlog_remaining=backlog_remaining,
    )


def _delete_envelope_candidates(
    db: Session,
    *,
    candidates: set[uuid.UUID],
    max_deletions: int,
) -> int:
    deleted_count = 0
    while candidates and deleted_count < max_deletions:
        remaining_budget = max_deletions - deleted_count
        locked_candidates = set(
            db.scalars(
                select(DataAccessEnvelope.id)
                .where(DataAccessEnvelope.id.in_(candidates))
                .order_by(DataAccessEnvelope.id)
                .limit(remaining_budget)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if not locked_candidates:
            break
        # Parent-source locks serialize with lineage copy validation so a child
        # cannot appear after the unreferenced check and before the cascade.
        source_locks = db.execute(
            select(DataAccessEnvelopeSource.id)
            .where(DataAccessEnvelopeSource.envelope_id.in_(locked_candidates))
            .order_by(
                DataAccessEnvelopeSource.envelope_id,
                DataAccessEnvelopeSource.source_type,
                DataAccessEnvelopeSource.source_id,
                DataAccessEnvelopeSource.source_version,
                DataAccessEnvelopeSource.id,
            )
            .with_for_update()
        )
        source_locks.close()
        deletable = set(
            db.scalars(
                select(DataAccessEnvelope.id)
                .where(
                    DataAccessEnvelope.id.in_(locked_candidates),
                    ~_lineage_child_exists(),
                )
                .order_by(DataAccessEnvelope.id)
                .limit(remaining_budget)
            ).all()
        )
        if not deletable:
            break

        child = aliased(DataAccessEnvelopeSource)
        parent = aliased(DataAccessEnvelopeSource)
        ancestor_ids = set(
            db.scalars(
                select(parent.envelope_id)
                .join(child, child.source_parent_id == parent.id)
                .where(child.envelope_id.in_(deletable))
            ).all()
        )
        result = db.execute(
            delete(DataAccessEnvelope)
            .where(DataAccessEnvelope.id.in_(deletable))
            .execution_options(synchronize_session=False)
        )
        deleted_count += int(result.rowcount or 0)
        db.flush()
        candidates = _orphan_envelope_ids(db, envelope_ids=ancestor_ids)
    return deleted_count


def _known_orphan_predicate():
    predicates = []
    for resource_type, model in _RESOURCE_MODELS.items():
        predicates.append(
            and_(
                DataAccessEnvelope.resource_type == resource_type,
                ~exists(
                    select(model.id).where(model.id == DataAccessEnvelope.resource_id)
                ),
            )
        )
    return or_(*predicates)


def _lineage_child_exists():
    parent_source = aliased(DataAccessEnvelopeSource)
    child_source = aliased(DataAccessEnvelopeSource)
    return exists(
        select(child_source.id)
        .join(
            parent_source,
            child_source.source_parent_id == parent_source.id,
        )
        .where(
            parent_source.envelope_id == DataAccessEnvelope.id,
            child_source.envelope_id != DataAccessEnvelope.id,
        )
    )


def _normalize_resource_refs(
    resources: Iterable[DataAccessResourceRef],
) -> list[DataAccessResourceRef]:
    normalized: set[DataAccessResourceRef] = set()
    for resource in resources:
        try:
            resource_type, resource_id = resource
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Data access retention resources must be (resource_type, UUID) pairs."
            ) from exc
        if resource_type not in _RESOURCE_MODELS:
            raise ValueError(
                f"Unsupported data access retention resource type: {resource_type!r}."
            )
        if not isinstance(resource_id, uuid.UUID):
            raise ValueError("Data access retention resource IDs must be UUID values.")
        normalized.add((resource_type, resource_id))
        if len(normalized) > _MAX_TARGETED_RESOURCES:
            raise ValueError(
                "Data access retention cleanup accepts at most "
                f"{_MAX_TARGETED_RESOURCES} unique resources per transaction."
            )
    return sorted(normalized, key=lambda value: (value[0], str(value[1])))


def _orphan_envelope_ids(
    db: Session,
    *,
    envelope_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    if not envelope_ids:
        return set()
    rows = db.execute(
        select(
            DataAccessEnvelope.id,
            DataAccessEnvelope.resource_type,
            DataAccessEnvelope.resource_id,
        ).where(DataAccessEnvelope.id.in_(envelope_ids))
    ).all()
    orphan_ids: set[uuid.UUID] = set()
    for resource_type, model in _RESOURCE_MODELS.items():
        typed_rows = [row for row in rows if row.resource_type == resource_type]
        if not typed_rows:
            continue
        existing_ids = set(
            db.scalars(
                select(model.id).where(
                    model.id.in_([row.resource_id for row in typed_rows])
                )
            ).all()
        )
        orphan_ids.update(
            row.id for row in typed_rows if row.resource_id not in existing_ids
        )
    return orphan_ids


__all__ = [
    "DataAccessOrphanPruneResult",
    "DataAccessResourceRef",
    "prune_deleted_resource_envelopes",
    "prune_orphan_data_access_envelopes",
]
