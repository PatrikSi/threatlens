from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from sqlalchemy import and_, exists, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.models.data_policy import HandlingLabel, QUARANTINE_HANDLING_LABEL_ID
from app.models.integration import (
    IntegrationDeliveryMetric,
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortFeed,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
)
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
)
from app.services.metric_cohort_provenance import (
    MetricCohortIntegritySummary,
)


@dataclass(frozen=True, slots=True)
class IntegrationMetricWouldDenySummary:
    affected_count: int
    handling_label_ids: frozenset[uuid.UUID]


def taint_integration_delivery_metrics_for_feed(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
) -> int:
    """Retain a feed relabel and return newly added taint-origin rows."""

    cohort_ids = select(IntegrationDeliveryMetricCohortFeed.cohort_id).where(
        IntegrationDeliveryMetricCohortFeed.source_feed_id_snapshot == feed_id
    )
    labels = select(
        IntegrationDeliveryMetricCohort.id,
        literal(handling_label_id),
    ).where(IntegrationDeliveryMetricCohort.id.in_(cohort_ids))
    statement = insert(IntegrationDeliveryMetricCohortTaintLabel).from_select(
        ["cohort_id", "label_id"],
        labels,
    )
    result = db.execute(statement.on_conflict_do_nothing())
    db.execute(
        insert(IntegrationDeliveryMetricCohortLabel)
        .from_select(["cohort_id", "label_id"], labels)
        .on_conflict_do_nothing()
    )
    return int(result.rowcount or 0)


def integration_metric_policy_cohort_key(
    *,
    policy_revision: int,
    provenance_complete: bool,
    source_count: int,
    label_ids: Iterable[uuid.UUID],
    feed_ids: Iterable[uuid.UUID],
) -> str:
    labels = "|".join(sorted({str(value) for value in label_ids}))
    feeds = "|".join(sorted({str(value) for value in feed_ids}))
    canonical = (
        f"{max(1, int(policy_revision))}:"
        f"{int(bool(provenance_complete))}:{max(0, int(source_count))}:"
        f"{labels}:{feeds}"
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def integration_metric_cohort_data_access_predicate(
    data_access: DataAccessContext,
):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    captured_label = aliased(IntegrationDeliveryMetricCohortCapturedLabel)
    captured_handling = aliased(HandlingLabel)
    taint_label = aliased(IntegrationDeliveryMetricCohortTaintLabel)
    taint_handling = aliased(HandlingLabel)
    any_captured = exists(
        select(captured_label.cohort_id).where(
            captured_label.cohort_id == IntegrationDeliveryMetricCohort.id,
        )
    )
    inaccessible_captured = exists(
        select(captured_label.cohort_id)
        .join(captured_handling, captured_handling.id == captured_label.label_id)
        .where(
            captured_label.cohort_id == IntegrationDeliveryMetricCohort.id,
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
            taint_label.cohort_id == IntegrationDeliveryMetricCohort.id,
            or_(
                taint_label.label_id.not_in(data_access.allowed_label_ids),
                taint_handling.is_active.is_(False),
            ),
        )
    )
    incomplete_is_quarantined = or_(
        IntegrationDeliveryMetricCohort.provenance_complete.is_(True),
        exists(
            select(captured_label.cohort_id).where(
                captured_label.cohort_id == IntegrationDeliveryMetricCohort.id,
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


def integration_metric_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    connector_type: str,
) -> IntegrationMetricWouldDenySummary:
    """Summarize rolled metric contributions audit mode serves but enforcement hides."""

    if not data_access.auditing or not data_access.principal_eligible:
        return IntegrationMetricWouldDenySummary(0, frozenset())

    fence_data_access_context(db, data_access)
    enforced_context = replace(data_access, mode="enforced")
    denied = ~integration_metric_cohort_data_access_predicate(enforced_context)
    affected_count = int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        IntegrationDeliveryMetricCohort.succeeded_count
                        + IntegrationDeliveryMetricCohort.failed_count
                        + IntegrationDeliveryMetricCohort.dead_letter_count
                    ),
                    0,
                )
            )
            .select_from(IntegrationDeliveryMetricCohort)
            .join(
                IntegrationDeliveryMetric,
                IntegrationDeliveryMetric.id
                == IntegrationDeliveryMetricCohort.metric_id,
            )
            .where(
                IntegrationDeliveryMetric.connector_type == connector_type,
                denied,
            )
        )
        or 0
    )
    if not affected_count:
        return IntegrationMetricWouldDenySummary(0, frozenset())

    label_ids = frozenset(
        db.scalars(
            select(IntegrationDeliveryMetricCohortLabel.label_id)
            .select_from(IntegrationDeliveryMetricCohort)
            .join(
                IntegrationDeliveryMetric,
                IntegrationDeliveryMetric.id
                == IntegrationDeliveryMetricCohort.metric_id,
            )
            .join(
                IntegrationDeliveryMetricCohortLabel,
                IntegrationDeliveryMetricCohortLabel.cohort_id
                == IntegrationDeliveryMetricCohort.id,
            )
            .join(
                HandlingLabel,
                HandlingLabel.id == IntegrationDeliveryMetricCohortLabel.label_id,
            )
            .where(
                IntegrationDeliveryMetric.connector_type == connector_type,
                denied,
                or_(
                    IntegrationDeliveryMetricCohortLabel.label_id.not_in(
                        enforced_context.allowed_label_ids
                    ),
                    HandlingLabel.is_active.is_(False),
                ),
            )
            .distinct()
        ).all()
    )
    return IntegrationMetricWouldDenySummary(affected_count, label_ids)


def integration_metric_cohort_integrity(
    db: Session,
) -> MetricCohortIntegritySummary:
    """Return blocker-ready identity, label, and aggregate parity counts."""

    cohorts = db.execute(
        select(
            IntegrationDeliveryMetricCohort.id,
            IntegrationDeliveryMetricCohort.policy_cohort_key,
            IntegrationDeliveryMetricCohort.captured_policy_revision,
            IntegrationDeliveryMetricCohort.provenance_complete,
            IntegrationDeliveryMetricCohort.source_count,
        )
    ).all()
    captured = _labels_by_cohort(
        db,
        IntegrationDeliveryMetricCohortCapturedLabel,
    )
    taints = _labels_by_cohort(db, IntegrationDeliveryMetricCohortTaintLabel)
    effective = _labels_by_cohort(db, IntegrationDeliveryMetricCohortLabel)
    feeds = _feeds_by_cohort(db)
    invalid_identity_count = 0
    missing_captured_labels_count = 0
    label_parity_mismatch_count = 0
    incomplete_without_quarantine_count = 0
    for cohort in cohorts:
        captured_labels = captured[cohort.id]
        if not captured_labels:
            missing_captured_labels_count += 1
        expected_key = integration_metric_policy_cohort_key(
            policy_revision=cohort.captured_policy_revision,
            provenance_complete=cohort.provenance_complete,
            source_count=cohort.source_count,
            label_ids=captured_labels,
            feed_ids=feeds[cohort.id],
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
            IntegrationDeliveryMetricCohort.metric_id.label("metric_id"),
            func.sum(IntegrationDeliveryMetricCohort.succeeded_count).label(
                "succeeded_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.failed_count).label(
                "failed_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.dead_letter_count).label(
                "dead_letter_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.attempt_count).label(
                "attempt_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.duration_total_ms).label(
                "duration_total_ms"
            ),
            func.max(IntegrationDeliveryMetricCohort.duration_max_ms).label(
                "duration_max_ms"
            ),
        )
        .group_by(IntegrationDeliveryMetricCohort.metric_id)
        .subquery()
    )
    metric_parity_mismatch_count = int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationDeliveryMetric)
            .outerjoin(totals, totals.c.metric_id == IntegrationDeliveryMetric.id)
            .where(
                or_(
                    IntegrationDeliveryMetric.succeeded_count
                    != func.coalesce(totals.c.succeeded_count, 0),
                    IntegrationDeliveryMetric.failed_count
                    != func.coalesce(totals.c.failed_count, 0),
                    IntegrationDeliveryMetric.dead_letter_count
                    != func.coalesce(totals.c.dead_letter_count, 0),
                    IntegrationDeliveryMetric.attempt_count
                    != func.coalesce(totals.c.attempt_count, 0),
                    IntegrationDeliveryMetric.duration_total_ms
                    != func.coalesce(totals.c.duration_total_ms, 0),
                    IntegrationDeliveryMetric.duration_max_ms
                    != func.coalesce(totals.c.duration_max_ms, 0),
                )
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


def _feeds_by_cohort(db: Session) -> dict[uuid.UUID, set[uuid.UUID]]:
    feeds: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    rows = db.execute(
        select(
            IntegrationDeliveryMetricCohortFeed.cohort_id,
            IntegrationDeliveryMetricCohortFeed.source_feed_id_snapshot,
        )
    )
    for cohort_id, feed_id in rows:
        feeds[cohort_id].add(feed_id)
    return feeds


__all__ = [
    "integration_metric_cohort_integrity",
    "integration_metric_cohort_data_access_predicate",
    "integration_metric_policy_cohort_key",
    "integration_metric_would_deny_summary",
    "IntegrationMetricWouldDenySummary",
    "taint_integration_delivery_metrics_for_feed",
]
