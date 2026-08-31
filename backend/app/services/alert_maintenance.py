from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceActivity,
    AlertOccurrenceMetric,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortLabel,
)
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    get_data_access_envelope_sources,
)
from app.services.alert_metric_data_policy import alert_metric_policy_cohort_key
from app.services.data_access_retention import prune_deleted_resource_envelopes
from app.services.data_access_runtime import (
    ensure_alert_occurrence_data_access_envelope,
    lock_data_policy_revision_for_derivation,
)


ALERT_OCCURRENCE_RETENTION_DAYS = 180
ALERT_ACTIVITY_RETENTION_DAYS = 365
ALERT_EVALUATION_RETENTION_DAYS = 30
ALERT_METRIC_RETENTION_DAYS = 730
ALERT_MAINTENANCE_BATCH_SIZE = 1_000
ALERT_MAINTENANCE_MAX_BATCHES = 20
ALERT_MAINTENANCE_MAX_RUNTIME_SECONDS = 30.0
UNRESOLVED_ALERT_METRIC_FEED_ID = uuid.UUID(int=0)


@dataclass(frozen=True)
class AlertHistoryMaintenanceResult:
    previews_deleted: int
    occurrences_aggregated: int
    occurrences_deleted: int
    activities_deleted: int
    evaluations_deleted: int
    metrics_deleted: int
    batches_processed: int
    elapsed_ms: int
    stop_reason: str
    backlog_remaining: bool
    backlog_categories: tuple[str, ...]


@dataclass(frozen=True)
class _AlertHistoryMaintenanceBatch:
    previews_deleted: int = 0
    occurrences_aggregated: int = 0
    occurrences_deleted: int = 0
    activities_deleted: int = 0
    evaluations_deleted: int = 0
    metrics_deleted: int = 0

    @property
    def work_items(self) -> int:
        return (
            self.previews_deleted
            + self.occurrences_aggregated
            + self.occurrences_deleted
            + self.activities_deleted
            + self.evaluations_deleted
            + self.metrics_deleted
        )


def maintain_alert_history(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = ALERT_MAINTENANCE_BATCH_SIZE,
    occurrence_retention_days: int = ALERT_OCCURRENCE_RETENTION_DAYS,
    activity_retention_days: int = ALERT_ACTIVITY_RETENTION_DAYS,
    evaluation_retention_days: int = ALERT_EVALUATION_RETENTION_DAYS,
    metric_retention_days: int = ALERT_METRIC_RETENTION_DAYS,
    max_batches: int = ALERT_MAINTENANCE_MAX_BATCHES,
    max_runtime_seconds: float = ALERT_MAINTENANCE_MAX_RUNTIME_SECONDS,
    _clock: Callable[[], float] = time.monotonic,
) -> AlertHistoryMaintenanceResult:
    current_time = now or datetime.now(timezone.utc)
    bounded_batch = max(1, min(int(batch_size), 10_000))
    bounded_max_batches = max(1, min(int(max_batches), 100))
    bounded_runtime = max(0.01, min(float(max_runtime_seconds), 300.0))
    occurrence_cutoff = current_time - timedelta(
        days=max(1, int(occurrence_retention_days))
    )
    activity_cutoff = current_time - timedelta(
        days=max(1, int(activity_retention_days))
    )
    evaluation_cutoff = current_time - timedelta(
        days=max(1, int(evaluation_retention_days))
    )
    metric_cutoff = current_time - timedelta(days=max(1, int(metric_retention_days)))

    started_at = _clock()
    totals: Counter[str] = Counter()
    batches_processed = 0
    stop_reason = "drained"
    while batches_processed < bounded_max_batches:
        if batches_processed > 0 and _clock() - started_at >= bounded_runtime:
            stop_reason = "runtime_limit"
            break
        batch = _maintain_alert_history_batch(
            db,
            current_time=current_time,
            batch_size=bounded_batch,
            occurrence_cutoff=occurrence_cutoff,
            activity_cutoff=activity_cutoff,
            evaluation_cutoff=evaluation_cutoff,
            metric_cutoff=metric_cutoff,
        )
        batches_processed += 1
        for field_name in (
            "previews_deleted",
            "occurrences_aggregated",
            "occurrences_deleted",
            "activities_deleted",
            "evaluations_deleted",
            "metrics_deleted",
        ):
            totals[field_name] += int(getattr(batch, field_name))
        if batch.work_items == 0:
            break
    else:
        stop_reason = "batch_limit"

    backlog_categories = _alert_history_backlog_categories(
        db,
        current_time=current_time,
        occurrence_cutoff=occurrence_cutoff,
        activity_cutoff=activity_cutoff,
        evaluation_cutoff=evaluation_cutoff,
        metric_cutoff=metric_cutoff,
    )
    if not backlog_categories:
        stop_reason = "drained"
    elapsed_ms = max(0, int((_clock() - started_at) * 1_000))
    return AlertHistoryMaintenanceResult(
        previews_deleted=totals["previews_deleted"],
        occurrences_aggregated=totals["occurrences_aggregated"],
        occurrences_deleted=totals["occurrences_deleted"],
        activities_deleted=totals["activities_deleted"],
        evaluations_deleted=totals["evaluations_deleted"],
        metrics_deleted=totals["metrics_deleted"],
        batches_processed=batches_processed,
        elapsed_ms=elapsed_ms,
        stop_reason=stop_reason,
        backlog_remaining=bool(backlog_categories),
        backlog_categories=backlog_categories,
    )


def _maintain_alert_history_batch(
    db: Session,
    *,
    current_time: datetime,
    batch_size: int,
    occurrence_cutoff: datetime,
    activity_cutoff: datetime,
    evaluation_cutoff: datetime,
    metric_cutoff: datetime,
) -> _AlertHistoryMaintenanceBatch:

    preview_ids = list(
        db.scalars(
            select(AlertBackfillPreview.id)
            .where(AlertBackfillPreview.expires_at <= current_time)
            .order_by(AlertBackfillPreview.expires_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    previews_deleted = _delete_ids(db, AlertBackfillPreview, preview_ids)

    policy_revision = lock_data_policy_revision_for_derivation(db)
    aggregate_rows = list(
        db.scalars(
            select(AlertOccurrence)
            .where(
                AlertOccurrence.lifecycle_state == "closed",
                AlertOccurrence.closed_at.is_not(None),
                AlertOccurrence.closed_at < occurrence_cutoff,
                AlertOccurrence.metrics_aggregated_at.is_(None),
            )
            .order_by(AlertOccurrence.closed_at.asc(), AlertOccurrence.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    public_counts: Counter[tuple[datetime, uuid.UUID, str, str, bool]] = Counter()
    cohort_counts: Counter[
        tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str]
    ] = Counter()
    labels_by_key: dict[
        tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str],
        set[uuid.UUID],
    ] = {}
    for occurrence in aggregate_rows:
        envelope = ensure_alert_occurrence_data_access_envelope(
            db,
            occurrence_id=occurrence.id,
            expected_policy_revision=policy_revision,
        )
        sources = get_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
            resource_id=occurrence.id,
        )
        source_feed_ids = {
            source.source_feed_id
            for source in sources
            if source.source_feed_id is not None
        }
        source_feed_id = (
            next(iter(source_feed_ids))
            if len(source_feed_ids) == 1
            else UNRESOLVED_ALERT_METRIC_FEED_ID
        )
        label_ids = set(envelope.label_ids)
        if source_feed_id == UNRESOLVED_ALERT_METRIC_FEED_ID:
            label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
        policy_cohort_key = alert_metric_policy_cohort_key(
            policy_revision=envelope.policy_revision,
            label_ids=label_ids,
        )
        created_at = _as_utc(occurrence.created_at)
        bucket = created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        public_key = (
            bucket,
            occurrence.owner_user_id,
            occurrence.severity_snapshot,
            occurrence.lifecycle_state,
            occurrence.suppressed_at is not None,
        )
        cohort_key = (*public_key, source_feed_id, policy_cohort_key)
        public_counts[public_key] += 1
        cohort_counts[cohort_key] += 1
        labels_by_key.setdefault(cohort_key, set()).update(label_ids)

    metric_ids: dict[tuple[datetime, uuid.UUID, str, str, bool], uuid.UUID] = {}
    if public_counts:
        db.execute(
            text(
                "SELECT set_config('threatlens.alert_metric_cohort_write', 'on', true)"
            )
        )
    for key in sorted(public_counts, key=_alert_metric_public_key_sort):
        bucket, owner_id, severity, state, suppressed = key
        count = public_counts[key]
        statement = insert(AlertOccurrenceMetric).values(
            id=uuid.uuid4(),
            bucket_start=bucket,
            owner_user_id=owner_id,
            severity=severity,
            lifecycle_state=state,
            suppressed=suppressed,
            occurrence_count=count,
        )
        metric_id = db.scalar(
            statement.on_conflict_do_update(
                constraint="uq_alert_occurrence_metrics_bucket_dimensions",
                set_={
                    "occurrence_count": AlertOccurrenceMetric.occurrence_count
                    + statement.excluded.occurrence_count,
                    "updated_at": current_time,
                },
            ).returning(AlertOccurrenceMetric.id)
        )
        if metric_id is None:
            raise RuntimeError("Alert occurrence metric rollup did not return a row.")
        metric_ids[key] = metric_id

    for key in sorted(cohort_counts, key=_alert_metric_cohort_key_sort):
        (
            bucket,
            owner_id,
            severity,
            state,
            suppressed,
            source_feed_id,
            policy_cohort_key,
        ) = key
        count = cohort_counts[key]
        metric_id = metric_ids[(bucket, owner_id, severity, state, suppressed)]
        statement = insert(AlertOccurrenceMetricCohort).values(
            id=uuid.uuid4(),
            metric_id=metric_id,
            source_feed_id_snapshot=source_feed_id,
            policy_cohort_key=policy_cohort_key,
            occurrence_count=count,
        )
        cohort_id = db.scalar(
            statement.on_conflict_do_update(
                constraint="uq_alert_occurrence_metric_cohorts_dimensions",
                set_={
                    "occurrence_count": AlertOccurrenceMetricCohort.occurrence_count
                    + statement.excluded.occurrence_count,
                    "updated_at": current_time,
                },
            ).returning(AlertOccurrenceMetricCohort.id)
        )
        if cohort_id is None:
            raise RuntimeError("Alert occurrence metric cohort did not return a row.")
        for label_id in sorted(labels_by_key[key], key=str):
            db.execute(
                insert(AlertOccurrenceMetricCohortLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
    for occurrence in aggregate_rows:
        occurrence.metrics_aggregated_at = current_time
        db.add(occurrence)
    db.flush()

    occurrence_ids = list(
        db.scalars(
            select(AlertOccurrence.id)
            .where(
                AlertOccurrence.lifecycle_state == "closed",
                AlertOccurrence.closed_at.is_not(None),
                AlertOccurrence.closed_at < occurrence_cutoff,
                AlertOccurrence.metrics_aggregated_at.is_not(None),
            )
            .order_by(AlertOccurrence.closed_at.asc(), AlertOccurrence.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    occurrences_deleted = _delete_ids(db, AlertOccurrence, occurrence_ids)
    if occurrences_deleted:
        prune_deleted_resource_envelopes(
            db,
            resources=(
                (DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE, occurrence_id)
                for occurrence_id in occurrence_ids
            ),
        )

    activity_ids = list(
        db.scalars(
            select(AlertOccurrenceActivity.id)
            .where(
                AlertOccurrenceActivity.created_at < activity_cutoff,
                AlertOccurrenceActivity.action != "created",
            )
            .order_by(
                AlertOccurrenceActivity.created_at.asc(),
                AlertOccurrenceActivity.id.asc(),
            )
            .limit(batch_size)
        ).all()
    )
    activities_deleted = _delete_ids(db, AlertOccurrenceActivity, activity_ids)

    evaluation_ids = list(
        db.scalars(
            select(AlertEvaluationRequest.id)
            .where(
                AlertEvaluationRequest.state.in_(["succeeded", "dead_letter"]),
                AlertEvaluationRequest.completed_at.is_not(None),
                AlertEvaluationRequest.completed_at < evaluation_cutoff,
            )
            .order_by(
                AlertEvaluationRequest.completed_at.asc(),
                AlertEvaluationRequest.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    evaluations_deleted = _delete_terminal_evaluation_ids(
        db,
        evaluation_ids,
        cutoff=evaluation_cutoff,
    )

    metric_ids = list(
        db.scalars(
            select(AlertOccurrenceMetric.id)
            .where(AlertOccurrenceMetric.bucket_start < metric_cutoff)
            .order_by(
                AlertOccurrenceMetric.bucket_start.asc(), AlertOccurrenceMetric.id.asc()
            )
            .limit(batch_size)
        ).all()
    )
    metrics_deleted = _delete_ids(db, AlertOccurrenceMetric, metric_ids)
    db.commit()
    return _AlertHistoryMaintenanceBatch(
        previews_deleted=previews_deleted,
        occurrences_aggregated=len(aggregate_rows),
        occurrences_deleted=occurrences_deleted,
        activities_deleted=activities_deleted,
        evaluations_deleted=evaluations_deleted,
        metrics_deleted=metrics_deleted,
    )


def _alert_history_backlog_categories(
    db: Session,
    *,
    current_time: datetime,
    occurrence_cutoff: datetime,
    activity_cutoff: datetime,
    evaluation_cutoff: datetime,
    metric_cutoff: datetime,
) -> tuple[str, ...]:
    checks = (
        (
            "expired_previews",
            exists().where(AlertBackfillPreview.expires_at <= current_time),
        ),
        (
            "occurrences_to_aggregate",
            exists().where(
                AlertOccurrence.lifecycle_state == "closed",
                AlertOccurrence.closed_at.is_not(None),
                AlertOccurrence.closed_at < occurrence_cutoff,
                AlertOccurrence.metrics_aggregated_at.is_(None),
            ),
        ),
        (
            "occurrences_to_delete",
            exists().where(
                AlertOccurrence.lifecycle_state == "closed",
                AlertOccurrence.closed_at.is_not(None),
                AlertOccurrence.closed_at < occurrence_cutoff,
                AlertOccurrence.metrics_aggregated_at.is_not(None),
            ),
        ),
        (
            "activities_to_delete",
            exists().where(
                AlertOccurrenceActivity.created_at < activity_cutoff,
                AlertOccurrenceActivity.action != "created",
            ),
        ),
        (
            "evaluations_to_delete",
            exists().where(
                AlertEvaluationRequest.state.in_(["succeeded", "dead_letter"]),
                AlertEvaluationRequest.completed_at.is_not(None),
                AlertEvaluationRequest.completed_at < evaluation_cutoff,
            ),
        ),
        (
            "metrics_to_delete",
            exists().where(AlertOccurrenceMetric.bucket_start < metric_cutoff),
        ),
    )
    return tuple(
        name for name, predicate in checks if bool(db.scalar(select(predicate)))
    )


def _delete_ids(db: Session, model, ids: list[uuid.UUID]) -> int:
    if not ids:
        return 0
    result = db.execute(
        delete(model)
        .where(model.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _delete_terminal_evaluation_ids(
    db: Session,
    ids: list[uuid.UUID],
    *,
    cutoff: datetime,
) -> int:
    if not ids:
        return 0
    result = db.execute(
        delete(AlertEvaluationRequest)
        .where(
            AlertEvaluationRequest.id.in_(ids),
            AlertEvaluationRequest.state.in_(["succeeded", "dead_letter"]),
            AlertEvaluationRequest.completed_at.is_not(None),
            AlertEvaluationRequest.completed_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _alert_metric_public_key_sort(
    key: tuple[datetime, uuid.UUID, str, str, bool],
) -> tuple[datetime, str, str, str, bool]:
    bucket, owner_id, severity, state, suppressed = key
    return bucket, str(owner_id), severity, state, suppressed


def _alert_metric_cohort_key_sort(
    key: tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str],
) -> tuple[datetime, str, str, str, bool, str, str]:
    bucket, owner_id, severity, state, suppressed, source_feed_id, cohort_key = key
    return (
        bucket,
        str(owner_id),
        severity,
        state,
        suppressed,
        str(source_feed_id),
        cohort_key,
    )
