import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_async_export_migration_upgrade_and_downgrade(database_engine, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0087_async_exports.py"
    spec = importlib.util.spec_from_file_location("async_export_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"export_migration_{uuid.uuid4().hex}"
    with database_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        tables = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema=:schema"), {"schema": schema}).scalars().all()
        assert set(tables) == {"export_jobs", "export_job_chunks"}
        module.downgrade()
        assert connection.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema"), {"schema": schema}) == 0
        connection.execute(text(f'DROP SCHEMA "{schema}"'))
