from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.alert_occurrence import (
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortLabel,
)


def taint_alert_occurrence_metrics_for_feed(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
) -> int:
    """Add a feed's new label to every retained historical metric bucket."""

    statement = insert(AlertOccurrenceMetricCohortLabel).from_select(
        ["cohort_id", "label_id"],
        select(
            AlertOccurrenceMetricCohort.id,
            literal(handling_label_id),
        ).where(AlertOccurrenceMetricCohort.source_feed_id_snapshot == feed_id),
    )
    result = db.execute(statement.on_conflict_do_nothing())
    return int(result.rowcount or 0)


def alert_metric_policy_cohort_key(
    *,
    policy_revision: int,
    label_ids: Iterable[uuid.UUID],
) -> str:
    canonical_labels = "|".join(sorted(str(label_id) for label_id in set(label_ids)))
    canonical = f"{max(0, int(policy_revision))}:{canonical_labels}"
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


__all__ = [
    "alert_metric_policy_cohort_key",
    "taint_alert_occurrence_metrics_for_feed",
]
