"""Hide expired permission-bearing history before draining its provenance.

The parent claim and every child deletion commit together. Database triggers
reject new references and preserve ordinary, retained cohort immutability.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import and_, delete, exists, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app.models.alert_occurrence import (
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.audit_log import AuditLogDataAccessFeed, AuditLogDataAccessLabel
from app.models.integration import (
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortFeed,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
)
from app.models.lifecycle_pruning import LifecyclePruningRecord
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
from app.services.lifecycle_pruning_contracts import PruningContext, PruningResult


_METRIC_CHILDREN = {
    "alert_occurrence_metrics": (
        AlertOccurrenceMetricCohort,
        (
            AlertOccurrenceMetricCohortLabel,
            AlertOccurrenceMetricCohortCapturedLabel,
            AlertOccurrenceMetricCohortTaintLabel,
        ),
    ),
    "integration_delivery_metrics": (
        IntegrationDeliveryMetricCohort,
        (
            IntegrationDeliveryMetricCohortLabel,
            IntegrationDeliveryMetricCohortCapturedLabel,
            IntegrationDeliveryMetricCohortTaintLabel,
            IntegrationDeliveryMetricCohortFeed,
        ),
    ),
}


def supports_permission_pruning(model: type) -> bool:
    return model.__table__.name in {"audit_logs", *_METRIC_CHILDREN}


def permission_pruning_candidates(
    db: Session,
    *,
    model: type,
    parent_ids: list[uuid.UUID],
    max_dependent_rows: int,
) -> set[uuid.UUID]:
    if (
        not parent_ids
        or max_dependent_rows <= 0
        or not supports_permission_pruning(model)
    ):
        return set()
    dataset = model.__table__.name
    if dataset == "audit_logs":
        has_children = or_(
            exists().where(AuditLogDataAccessFeed.audit_log_id == model.id),
            exists().where(AuditLogDataAccessLabel.audit_log_id == model.id),
        )
    else:
        cohort, _ = _METRIC_CHILDREN[dataset]
        has_children = exists().where(cohort.metric_id == model.id)
    return set(
        db.scalars(select(model.id).where(model.id.in_(parent_ids), has_children))
    )


def prune_permission_history_parent(
    db: Session,
    *,
    model: type,
    parent_id: uuid.UUID,
    context: PruningContext,
    limit: int,
) -> PruningResult:
    if not supports_permission_pruning(model) or limit <= 0:
        return PruningResult()
    # Feed relabels and audit lineage triggers take the policy fence before
    # source parents. Keep that order even for direct helper callers.
    lock_data_policy_revision_for_derivation(db)
    if (
        db.scalar(select(model.id).where(model.id == parent_id).with_for_update())
        is None
    ):
        return PruningResult()
    # Eligibility may have changed while waiting for a reader or reference writer.
    if (
        db.scalar(select(model.id).where(model.id == parent_id, context.eligibility))
        is None
    ):
        return PruningResult()
    dataset = model.__table__.name
    record = db.get(LifecyclePruningRecord, (dataset, parent_id))
    started = record is None
    now = datetime.now(timezone.utc)
    if record is None:
        db.execute(
            update(model)
            .where(model.id == parent_id)
            .values(
                retention_pruning_started_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        record = LifecyclePruningRecord(
            dataset=dataset, parent_id=parent_id, cutoff_at=context.cutoff
        )
        db.add(record)
        db.flush([record])
    if dataset == "audit_logs":
        pruned = 0
        for child in (AuditLogDataAccessFeed, AuditLogDataAccessLabel):
            pruned += _delete_rows(
                db, child, child.audit_log_id == parent_id, limit - pruned
            )
    else:
        cohort, children = _METRIC_CHILDREN[dataset]
        cohort_ids = select(cohort.id).where(cohort.metric_id == parent_id)
        pruned = 0
        for child in children:
            pruned += _delete_rows(
                db, child, child.cohort_id.in_(cohort_ids), limit - pruned
            )
        # Delete only empty cohorts: their CASCADE must never bypass the budget,
        # including when a concurrent child lock made SKIP LOCKED leave a row.
        empty = [~exists().where(child.cohort_id == cohort.id) for child in children]
        pruned += _delete_rows(
            db, cohort, and_(cohort.metric_id == parent_id, *empty), limit - pruned
        )
    record.children_pruned += pruned
    record.updated_at = now
    db.flush([record])
    return PruningResult(children_pruned=pruned, parents_started=int(started))


def _delete_rows(db: Session, model: type, predicate, limit: int) -> int:
    if limit <= 0:
        return 0
    keys = tuple(model.__table__.primary_key.columns)
    selected = (
        select(*keys)
        .where(predicate)
        .order_by(*keys)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    identity = keys[0] if len(keys) == 1 else tuple_(*keys)
    result = db.execute(
        delete(model)
        .where(identity.in_(selected))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def lock_permission_history_dependants(
    db: Session,
    *,
    model: type,
    parent_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Skip final deletion when a writer already owns a dependent row.

    UPDATE takes its child lock before a BEFORE trigger can fence the parent.
    Waiting for that child while holding its parent would invert the order.
    The selected parent batch already passed the dependent-row budget; this
    additional cap protects callers from concurrently changed or invalid costs.
    """
    if not parent_ids or not supports_permission_pruning(model):
        return parent_ids
    dataset = model.__table__.name
    if dataset == "audit_logs":
        checks = [
            (child, child.audit_log_id, child.audit_log_id.in_(parent_ids))
            for child in (AuditLogDataAccessFeed, AuditLogDataAccessLabel)
        ]
    else:
        cohort, children = _METRIC_CHILDREN[dataset]
        parents = dict(
            db.execute(
                select(cohort.id, cohort.metric_id)
                .where(cohort.metric_id.in_(parent_ids))
                .limit(10_001)
            ).all()
        )
        if len(parents) > 10_000:
            return []
        blocked = _locked_parent_ids(
            db, cohort, cohort.metric_id, cohort.id.in_(parents)
        )
        if blocked is None:
            return []
        checks = [
            (child, child.cohort_id, child.cohort_id.in_(parents)) for child in children
        ]
        for child, parent_column, predicate in checks:
            blocked_cohorts = _locked_parent_ids(db, child, parent_column, predicate)
            if blocked_cohorts is None:
                return []
            blocked.update(parents[cohort_id] for cohort_id in blocked_cohorts)
        return [parent_id for parent_id in parent_ids if parent_id not in blocked]
    blocked = set()
    for child, parent_column, predicate in checks:
        blocked_parents = _locked_parent_ids(db, child, parent_column, predicate)
        if blocked_parents is None:
            return []
        blocked.update(blocked_parents)
    return [parent_id for parent_id in parent_ids if parent_id not in blocked]


def _locked_parent_ids(db: Session, model: type, parent_column, predicate):
    keys = tuple(model.__table__.primary_key.columns)
    rows = list(db.execute(select(*keys, parent_column).where(predicate).limit(10_001)))
    if len(rows) > 10_000:
        return None
    locked = {
        tuple(row)
        for row in db.execute(
            select(*keys)
            .where(predicate)
            .limit(10_001)
            .with_for_update(skip_locked=True)
        )
    }
    return {row[-1] for row in rows if tuple(row[:-1]) not in locked}
