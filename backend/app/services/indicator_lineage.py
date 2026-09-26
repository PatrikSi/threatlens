"""Accumulate source access boundaries without broadening historical reviews."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.intel_assessment import IndicatorAssessment, IndicatorAssessmentLabel
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)


def assessment_access_predicate(access: DataAccessContext):
    """Missing or inaccessible retained lineage never grants review access."""
    labels = (
        select(IndicatorAssessmentLabel.assessment_id)
        .where(
            IndicatorAssessmentLabel.assessment_id == IndicatorAssessment.id,
        )
        .correlate(IndicatorAssessment)
    )
    return and_(
        handling_label_access_predicate(IndicatorAssessment.handling_label_id, access),
        labels.where(
            IndicatorAssessmentLabel.handling_label_id
            == IndicatorAssessment.handling_label_id
        ).exists(),
        ~labels.where(
            ~handling_label_access_predicate(
                IndicatorAssessmentLabel.handling_label_id, access
            )
        ).exists(),
    )


def capture_assessment_labels(
    db: Session,
    row: IndicatorAssessment,
    *,
    current_label_id: uuid.UUID,
) -> None:
    """Caller holds policy, team and assessment locks; labels only accumulate."""
    db.execute(
        insert(IndicatorAssessmentLabel)
        .values(
            [
                {"assessment_id": row.id, "handling_label_id": label}
                for label in sorted({row.handling_label_id, current_label_id})
            ]
        )
        .on_conflict_do_nothing()
    )


def assessment_labels_by_id(
    db: Session,
    assessment_ids: list[uuid.UUID],
) -> dict[uuid.UUID, set[uuid.UUID]]:
    result: dict[uuid.UUID, set[uuid.UUID]] = {}
    if not assessment_ids:
        return result
    for row in db.execute(
        select(
            IndicatorAssessmentLabel.assessment_id,
            IndicatorAssessmentLabel.handling_label_id,
        ).where(IndicatorAssessmentLabel.assessment_id.in_(assessment_ids))
    ):
        result.setdefault(row.assessment_id, set()).add(row.handling_label_id)
    return result
