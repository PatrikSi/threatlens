from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceActivity,
    AlertOccurrenceMetric,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    DataAccessEnvelopeSnapshot,
    DataAccessSourceSnapshot,
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


@dataclass(frozen=True)
class _AlertMetricProvenance:
    captured_policy_revision: int
    provenance_complete: bool
    captured_label_ids: frozenset[uuid.UUID]
    taint_label_ids: frozenset[uuid.UUID]
    source_feed_ids: frozenset[uuid.UUID]


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
    captured_labels_by_key: dict[
        tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str],
        set[uuid.UUID],
    ] = {}
    taint_labels_by_key: dict[
        tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str],
        set[uuid.UUID],
    ] = {}
    captured_revision_by_key: dict[
        tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str], int
    ] = {}
    provenance_complete_by_key: dict[
        tuple[datetime, uuid.UUID, str, str, bool, uuid.UUID, str], bool
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
        provenance = _alert_metric_provenance(
            envelope,
            sources,
            policy_revision=policy_revision,
        )
        source_feed_ids = provenance.source_feed_ids
        source_feed_id = (
            next(iter(source_feed_ids))
            if len(source_feed_ids) == 1
            else UNRESOLVED_ALERT_METRIC_FEED_ID
        )
        policy_cohort_key = alert_metric_policy_cohort_key(
            policy_revision=provenance.captured_policy_revision,
            label_ids=provenance.captured_label_ids,
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
        previous_labels = captured_labels_by_key.setdefault(
            cohort_key,
            set(provenance.captured_label_ids),
        )
        previous_revision = captured_revision_by_key.setdefault(
            cohort_key,
            provenance.captured_policy_revision,
        )
        if (
            previous_labels != set(provenance.captured_label_ids)
            or previous_revision != provenance.captured_policy_revision
        ):
            raise RuntimeError("Alert metric cohort identity collision detected.")
        taint_labels_by_key.setdefault(cohort_key, set()).update(
            provenance.taint_label_ids
        )
        provenance_complete_by_key[cohort_key] = (
            provenance_complete_by_key.get(cohort_key, True)
            and provenance.provenance_complete
        )

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
            captured_policy_revision=captured_revision_by_key[key],
            provenance_complete=provenance_complete_by_key[key],
            occurrence_count=count,
        )
        cohort_row = db.execute(
            statement.on_conflict_do_update(
                constraint="uq_alert_occurrence_metric_cohorts_dimensions",
                set_={
                    "occurrence_count": AlertOccurrenceMetricCohort.occurrence_count
                    + statement.excluded.occurrence_count,
                    "provenance_complete": and_(
                        AlertOccurrenceMetricCohort.provenance_complete,
                        statement.excluded.provenance_complete,
                    ),
                    "updated_at": current_time,
                },
            ).returning(
                AlertOccurrenceMetricCohort.id,
                AlertOccurrenceMetricCohort.captured_policy_revision,
            )
        ).one()
        cohort_id = cohort_row.id
        if cohort_row.captured_policy_revision != captured_revision_by_key[key]:
            raise RuntimeError(
                "Alert metric cohort revision conflicts with its immutable identity."
            )
        for label_id in sorted(captured_labels_by_key[key], key=str):
            db.execute(
                insert(AlertOccurrenceMetricCohortCapturedLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
        for label_id in sorted(taint_labels_by_key[key], key=str):
            db.execute(
                insert(AlertOccurrenceMetricCohortTaintLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
        for label_id in sorted(
            captured_labels_by_key[key] | taint_labels_by_key[key],
            key=str,
        ):
            db.execute(
                insert(AlertOccurrenceMetricCohortLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
        retained_captured = set(
            db.scalars(
                select(AlertOccurrenceMetricCohortCapturedLabel.label_id)
                .where(AlertOccurrenceMetricCohortCapturedLabel.cohort_id == cohort_id)
                .with_for_update()
            ).all()
        )
        retained_taints = set(
            db.scalars(
                select(AlertOccurrenceMetricCohortTaintLabel.label_id)
                .where(AlertOccurrenceMetricCohortTaintLabel.cohort_id == cohort_id)
                .with_for_update()
            ).all()
        )
        retained_effective = set(
            db.scalars(
                select(AlertOccurrenceMetricCohortLabel.label_id)
                .where(AlertOccurrenceMetricCohortLabel.cohort_id == cohort_id)
                .with_for_update()
            ).all()
        )
        if (
            retained_captured != captured_labels_by_key[key]
            or not taint_labels_by_key[key].issubset(retained_taints)
            or retained_effective != retained_captured | retained_taints
        ):
            raise RuntimeError("Alert metric cohort provenance is inconsistent.")
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


def _alert_metric_provenance(
    envelope: DataAccessEnvelopeSnapshot,
    sources: tuple[DataAccessSourceSnapshot, ...],
    *,
    policy_revision: int,
) -> _AlertMetricProvenance:
    captured_sources = tuple(
        source for source in sources if source.source_type != "feed_taint"
    )
    taint_sources = tuple(
        source for source in sources if source.source_type == "feed_taint"
    )
    captured_labels = {source.handling_label_id for source in captured_sources}
    taint_labels = {source.handling_label_id for source in taint_sources}
    source_feed_ids = {
        source.source_feed_id for source in sources if source.source_feed_id is not None
    }
    captured_policy_revision = max(
        (source.captured_policy_revision for source in captured_sources),
        default=policy_revision,
    )
    provenance_complete = (
        bool(captured_sources)
        and len(source_feed_ids) == 1
        and envelope.source_count == len(sources)
        and envelope.policy_revision <= policy_revision
        and set(envelope.label_ids) == captured_labels | taint_labels
        and all(
            source.source_type in {"item", "feed_taint"}
            and source.source_feed_id is not None
            and source.captured_policy_revision <= policy_revision
            for source in sources
        )
    )
    if not provenance_complete:
        captured_labels.add(QUARANTINE_HANDLING_LABEL_ID)
    return _AlertMetricProvenance(
        captured_policy_revision=captured_policy_revision,
        provenance_complete=provenance_complete,
        captured_label_ids=frozenset(captured_labels),
        taint_label_ids=frozenset(taint_labels),
        source_feed_ids=frozenset(source_feed_ids),
    )


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
