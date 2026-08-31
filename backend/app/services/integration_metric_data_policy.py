from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace

from sqlalchemy import and_, exists, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.models.data_policy import HandlingLabel
from app.models.integration import (
    IntegrationDeliveryMetric,
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortFeed,
    IntegrationDeliveryMetricCohortLabel,
)
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
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
    """Retain a feed's new label on every matching historical metric cohort."""

    cohort_ids = select(IntegrationDeliveryMetricCohortFeed.cohort_id).where(
        IntegrationDeliveryMetricCohortFeed.source_feed_id_snapshot == feed_id
    )
    statement = insert(IntegrationDeliveryMetricCohortLabel).from_select(
        ["cohort_id", "label_id"],
        select(
            IntegrationDeliveryMetricCohort.id,
            literal(handling_label_id),
        ).where(IntegrationDeliveryMetricCohort.id.in_(cohort_ids)),
    )
    result = db.execute(statement.on_conflict_do_nothing())
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
    cohort_label = aliased(IntegrationDeliveryMetricCohortLabel)
    handling_label = aliased(HandlingLabel)
    any_label = exists(
        select(cohort_label.cohort_id).where(
            cohort_label.cohort_id == IntegrationDeliveryMetricCohort.id,
        )
    )
    inaccessible_label = exists(
        select(cohort_label.cohort_id)
        .join(handling_label, handling_label.id == cohort_label.label_id)
        .where(
            cohort_label.cohort_id == IntegrationDeliveryMetricCohort.id,
            or_(
                cohort_label.label_id.not_in(data_access.allowed_label_ids),
                handling_label.is_active.is_(False),
            ),
        )
    )
    return and_(any_label, ~inaccessible_label)


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


__all__ = [
    "integration_metric_cohort_data_access_predicate",
    "integration_metric_policy_cohort_key",
    "integration_metric_would_deny_summary",
    "IntegrationMetricWouldDenySummary",
    "taint_integration_delivery_metrics_for_feed",
]
