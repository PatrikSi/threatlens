import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import Base
from app.models.item_ai_enrichment import ItemAIEnrichment
from tests.integration.test_ai_feature_output_validation import (
    configured_item as configured_item,
)


def test_provenance_upgrade_preserves_history_without_certifying_old_results(
    db_session,
    configured_item,
    monkeypatch,
):
    item, _settings = configured_item
    db_session.add(
        ItemAIEnrichment(
            item_id=item.id,
            status="error",
            source_hash="b" * 64,
            summary_text="Retained historical summary.",
        )
    )
    db_session.flush()
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0100_ai_evidence_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("ai_evidence_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "op", Operations(MigrationContext.configure(db_session.connection()))
    )
    module.downgrade()
    module.upgrade()
    row = db_session.execute(
        text(
            "SELECT status, summary_text, result_provenance_json FROM item_ai_enrichments WHERE item_id=:id"
        ),
        {"id": item.id},
    ).one()
    assert tuple(row) == ("error", "Retained historical summary.", None)
    names = {"item_ai_enrichments", "ai_daily_briefs"}

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return name in names
        return getattr(getattr(obj, "table", None), "name", None) in names

    context = MigrationContext.configure(
        db_session.connection(), opts={"include_object": include_object}
    )
    assert compare_metadata(context, Base.metadata) == []
