import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import Base
from tests.integration.test_export_jobs import _accept, export_env as export_env


def test_export_dispatch_migration_preserves_queued_work(export_env, monkeypatch):
    from sqlalchemy.orm import Session

    env = export_env
    job_id, _ = _accept(env)
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0101_export_dispatch_progress.py"
    spec = importlib.util.spec_from_file_location("export_dispatch_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Session(env.engine) as db:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
        module.downgrade()
        before = db.execute(text("SELECT status, attempts, expires_at FROM export_jobs WHERE id=:id"), {"id": job_id}).one()
        module.upgrade()
        after = db.execute(text("SELECT status, attempts, expires_at, published_at, published_canary_at FROM export_jobs WHERE id=:id"), {"id": job_id}).one()
        assert tuple(after[:3]) == tuple(before)
        assert tuple(after[3:]) == (None, None)

        def include_object(obj, name, type_, reflected, compare_to):
            return name == "export_jobs" if type_ == "table" else getattr(getattr(obj, "table", None), "name", None) == "export_jobs"

        context = MigrationContext.configure(db.connection(), opts={"include_object": include_object})
        assert compare_metadata(context, Base.metadata) == []
        db.rollback()
