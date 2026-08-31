from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import and_, exists, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.models.alert_occurrence import (
    AlertOccurrenceMetric,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.data_policy import HandlingLabel, QUARANTINE_HANDLING_LABEL_ID
from app.services.data_access_policy import DataAccessContext
from app.services.metric_cohort_provenance import (
    MetricCohortIntegritySummary,
    captured_label_revision_cohort_key,
)


def taint_alert_occurrence_metrics_for_feed(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
) -> int:
    """Retain a feed relabel and return newly added taint-origin rows."""

    labels = select(
        AlertOccurrenceMetricCohort.id,
        literal(handling_label_id),
    ).where(AlertOccurrenceMetricCohort.source_feed_id_snapshot == feed_id)
    statement = insert(AlertOccurrenceMetricCohortTaintLabel).from_select(
        ["cohort_id", "label_id"],
        labels,
    )
    result = db.execute(statement.on_conflict_do_nothing())
    db.execute(
        insert(AlertOccurrenceMetricCohortLabel)
        .from_select(["cohort_id", "label_id"], labels)
        .on_conflict_do_nothing()
    )
    return int(result.rowcount or 0)


def alert_metric_policy_cohort_key(
    *,
    policy_revision: int,
    label_ids: Iterable[uuid.UUID],
) -> str:
    return captured_label_revision_cohort_key(
        captured_policy_revision=policy_revision,
        captured_label_ids=label_ids,
    )


def alert_metric_cohort_data_access_predicate(
    data_access: DataAccessContext,
):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    captured_label = aliased(AlertOccurrenceMetricCohortCapturedLabel)
    captured_handling = aliased(HandlingLabel)
    taint_label = aliased(AlertOccurrenceMetricCohortTaintLabel)
    taint_handling = aliased(HandlingLabel)
    any_captured = exists(
        select(captured_label.cohort_id).where(
            captured_label.cohort_id == AlertOccurrenceMetricCohort.id,
        )
    )
    inaccessible_captured = exists(
        select(captured_label.cohort_id)
        .join(captured_handling, captured_handling.id == captured_label.label_id)
        .where(
            captured_label.cohort_id == AlertOccurrenceMetricCohort.id,
            or_(
                captured_label.label_id.not_in(data_access.allowed_label_ids),
                captured_handling.is_active.is_(False),
            ),
        )
    )
    inaccessible_taint = exists(
        select(taint_label.cohort_id)
        .join(taint_handling, taint_handling.id == taint_label.label_id)
        .where(
            taint_label.cohort_id == AlertOccurrenceMetricCohort.id,
            or_(
                taint_label.label_id.not_in(data_access.allowed_label_ids),
                taint_handling.is_active.is_(False),
            ),
        )
    )
    incomplete_is_quarantined = or_(
        AlertOccurrenceMetricCohort.provenance_complete.is_(True),
        exists(
            select(captured_label.cohort_id).where(
                captured_label.cohort_id == AlertOccurrenceMetricCohort.id,
                captured_label.label_id == QUARANTINE_HANDLING_LABEL_ID,
            )
        ),
    )
    return and_(
        any_captured,
        incomplete_is_quarantined,
        ~inaccessible_captured,
        ~inaccessible_taint,
    )


def alert_metric_cohort_integrity(
    db: Session,
) -> MetricCohortIntegritySummary:
    """Return blocker-ready identity, label, and aggregate parity counts."""

    cohorts = db.execute(
        select(
            AlertOccurrenceMetricCohort.id,
            AlertOccurrenceMetricCohort.policy_cohort_key,
            AlertOccurrenceMetricCohort.captured_policy_revision,
            AlertOccurrenceMetricCohort.provenance_complete,
        )
    ).all()
    captured = _labels_by_cohort(
        db,
        AlertOccurrenceMetricCohortCapturedLabel,
    )
    taints = _labels_by_cohort(db, AlertOccurrenceMetricCohortTaintLabel)
    effective = _labels_by_cohort(db, AlertOccurrenceMetricCohortLabel)
    invalid_identity_count = 0
    missing_captured_labels_count = 0
    label_parity_mismatch_count = 0
    incomplete_without_quarantine_count = 0
    for cohort in cohorts:
        captured_labels = captured[cohort.id]
        if not captured_labels:
            missing_captured_labels_count += 1
        expected_key = captured_label_revision_cohort_key(
            captured_policy_revision=cohort.captured_policy_revision,
            captured_label_ids=captured_labels,
        )
        if expected_key != cohort.policy_cohort_key:
            invalid_identity_count += 1
        if effective[cohort.id] != captured_labels | taints[cohort.id]:
            label_parity_mismatch_count += 1
        if (
            not cohort.provenance_complete
            and QUARANTINE_HANDLING_LABEL_ID not in captured_labels
        ):
            incomplete_without_quarantine_count += 1

    totals = (
        select(
            AlertOccurrenceMetricCohort.metric_id.label("metric_id"),
            func.sum(AlertOccurrenceMetricCohort.occurrence_count).label(
                "occurrence_count"
            ),
        )
        .group_by(AlertOccurrenceMetricCohort.metric_id)
        .subquery()
    )
    metric_parity_mismatch_count = int(
        db.scalar(
            select(func.count())
            .select_from(AlertOccurrenceMetric)
            .outerjoin(totals, totals.c.metric_id == AlertOccurrenceMetric.id)
            .where(
                AlertOccurrenceMetric.occurrence_count
                != func.coalesce(totals.c.occurrence_count, 0)
            )
        )
        or 0
    )
    return MetricCohortIntegritySummary(
        cohort_count=len(cohorts),
        invalid_identity_count=invalid_identity_count,
        missing_captured_labels_count=missing_captured_labels_count,
        label_parity_mismatch_count=label_parity_mismatch_count,
        metric_parity_mismatch_count=metric_parity_mismatch_count,
        incomplete_without_quarantine_count=incomplete_without_quarantine_count,
    )


def _labels_by_cohort(db: Session, model) -> dict[uuid.UUID, set[uuid.UUID]]:
    labels: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for cohort_id, label_id in db.execute(select(model.cohort_id, model.label_id)):
        labels[cohort_id].add(label_id)
    return labels


__all__ = [
    "alert_metric_cohort_data_access_predicate",
    "alert_metric_cohort_integrity",
    "alert_metric_policy_cohort_key",
    "taint_alert_occurrence_metrics_for_feed",
]
