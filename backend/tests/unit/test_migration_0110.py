"""Review history lineage survives upgrades without unsafe downgrade weakening."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from app.db.base import Base
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
    HandlingLabel,
)
from app.models.intel_assessment import IndicatorAssessmentLabel
from tests.unit.test_intel_policy_retention import _retained_reference
from tests.unit.test_migration_0107 import _migration


def test_lineage_migration_backfills_legacy_reviews_and_matches_metadata(
    db_session, monkeypatch
):
    row = _retained_reference(
        db_session, label_id=UNRESTRICTED_HANDLING_LABEL_ID, assessment=True
    )
    migration = _migration(db_session, monkeypatch, "0110_indicator_review_lineage")
    migration.downgrade()
    migration.upgrade()
    assert db_session.scalars(
        select(IndicatorAssessmentLabel.handling_label_id).where(
            IndicatorAssessmentLabel.assessment_id == row.id,
        )
    ).all() == [UNRESTRICTED_HANDLING_LABEL_ID]

    def include_object(obj, name, kind, reflected, compare_to):
        return (
            name
            if kind == "table"
            else getattr(getattr(obj, "table", None), "name", None)
        ) == "indicator_assessment_labels"

    context = MigrationContext.configure(
        db_session.connection(), opts={"include_object": include_object}
    )
    assert compare_metadata(context, Base.metadata) == []


def test_lineage_downgrade_refuses_to_drop_a_restricted_history_boundary(
    db_session, monkeypatch
):
    row = _retained_reference(
        db_session, label_id=UNRESTRICTED_HANDLING_LABEL_ID, assessment=True
    )
    db_session.add(
        IndicatorAssessmentLabel(
            assessment_id=row.id, handling_label_id=QUARANTINE_HANDLING_LABEL_ID
        )
    )
    db_session.flush()
    migration = _migration(db_session, monkeypatch, "0110_indicator_review_lineage")
    with pytest.raises(RuntimeError, match="retain multiple handling labels"):
        migration.downgrade()
    assert (
        db_session.get(IndicatorAssessmentLabel, (row.id, QUARANTINE_HANDLING_LABEL_ID))
        is not None
    )
    db_session.delete(row)
    db_session.flush()
    assert (
        db_session.get(IndicatorAssessmentLabel, (row.id, QUARANTINE_HANDLING_LABEL_ID))
        is None
    )


def test_edited_legacy_reviews_capture_every_retained_label(db_session, monkeypatch):
    row = _retained_reference(
        db_session, label_id=UNRESTRICTED_HANDLING_LABEL_ID, assessment=True
    )
    row.version = 2
    db_session.flush()
    migration = _migration(db_session, monkeypatch, "0110_indicator_review_lineage")
    migration.downgrade()
    migration.upgrade()
    captured = set(
        db_session.scalars(
            select(IndicatorAssessmentLabel.handling_label_id).where(
                IndicatorAssessmentLabel.assessment_id == row.id,
            )
        )
    )
    assert captured == set(db_session.scalars(select(HandlingLabel.id)))
    assert QUARANTINE_HANDLING_LABEL_ID in captured
