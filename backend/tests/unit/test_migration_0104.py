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
    with pytest.raises(RuntimeError, match="awaiting editorial review"):
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


@pytest.mark.parametrize("self_review", [True, False])
def test_editorial_downgrade_preserves_published_revision_history(
    db_session, draft, monkeypatch, self_review
):
    from app.schemas.report_editorial import ReportEditorialTransition
    from app.services.report_editorial import transition_report
    from app.services.report_publication import publish_automatic_report

    draft.delivery_requested = False
    if self_review:
        for version, action in ((1, "submit"), (2, "approve"), (3, "publish")):
            transition_report(
                db_session,
                report=draft,
                actor_user_id=draft.owner_user_id,
                payload=ReportEditorialTransition(
                    expected_version=version,
                    action=action,
                    note="Reviewed source evidence.",
                ),
            )
    else:
        draft.review_required = False
        publish_automatic_report(db_session, draft)
    db_session.flush()
    published_hash = draft.published_revision_hash
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0104_report_editorial_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("editorial_history_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "op", Operations(MigrationContext.configure(db_session.connection()))
    )
    with pytest.raises(RuntimeError, match="editorial history"):
        module.downgrade()
    assert (
        db_session.execute(
            text("SELECT published_revision_hash FROM reports WHERE id=:id"),
            {"id": draft.id},
        ).scalar()
        == published_hash
    )
