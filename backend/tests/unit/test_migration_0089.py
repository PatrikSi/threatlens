import importlib.util
from pathlib import Path
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pytest


def test_tagging_recovery_migration_defaults_constraints_and_downgrade(database_engine, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0089_tagging_recovery.py"
    spec = importlib.util.spec_from_file_location("tagging_recovery_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"tagging_migration_{uuid.uuid4().hex}"
    with database_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(text("CREATE TABLE items (id bigint PRIMARY KEY)"))
        connection.execute(text("INSERT INTO items VALUES (1)"))
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        assert connection.execute(text("SELECT tagging_pending, tagging_attempts, tagging_retry_at, tagging_error_code FROM items")).one() == (False, 0, None, None)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("UPDATE items SET tagging_attempts=6"))
        definitions = connection.scalars(text("SELECT indexdef FROM pg_indexes WHERE schemaname=:schema"), {"schema": schema}).all()
        assert any("WHERE tagging_pending" in definition for definition in definitions)
        module.downgrade()
        assert connection.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=:schema AND column_name LIKE 'tagging_%'"), {"schema": schema}) == 0
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
