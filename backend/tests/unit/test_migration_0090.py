import importlib.util
from pathlib import Path
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_retention_cursor_migration_pair_constraint_and_downgrade(database_engine, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0090_lifecycle_scan_cursors.py"
    spec = importlib.util.spec_from_file_location("retention_cursor_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"retention_migration_{uuid.uuid4().hex}"
    with database_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        connection.execute(text("INSERT INTO lifecycle_scan_cursors (dataset) VALUES ('audit_logs')"))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("UPDATE lifecycle_scan_cursors SET last_timestamp=now()"))
        connection.execute(text(
            "UPDATE lifecycle_scan_cursors SET last_timestamp=now(), last_id=gen_random_uuid()"
        ))
        module.downgrade()
        assert connection.scalar(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema=:schema AND table_name='lifecycle_scan_cursors'"
        ), {"schema": schema}) == 0
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
