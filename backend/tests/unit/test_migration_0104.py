import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import Base
from tests.integration.test_report_editorial import draft as draft


def test_editorial_migration_preserves_legacy_publication_and_guards_downgrade(
    db_session, draft, monkeypatch
):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0104_report_editorial_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("editorial_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "op", Operations(MigrationContext.configure(db_session.connection()))
    )
    with pytest.raises(RuntimeError, match="Publish or remove"):
        module.downgrade()
    draft.review_required = False
    db_session.flush()
    module.downgrade()
    module.upgrade()
    row = db_session.execute(
        text(
            "SELECT review_required, publication_status, editorial_contract_version, published_revision_hash FROM reports WHERE id=:id"
        ),
        {"id": draft.id},
    ).one()
    assert tuple(row) == (False, "published", 0, None)

    def include_object(obj, name, type_, reflected, compare_to):
        return (
            name in {"reports", "report_schedules"}
            if type_ == "table"
            else getattr(getattr(obj, "table", None), "name", None)
            in {"reports", "report_schedules"}
        )

    context = MigrationContext.configure(
        db_session.connection(), opts={"include_object": include_object}
    )
    assert compare_metadata(context, Base.metadata) == []
    db_session.rollback()
