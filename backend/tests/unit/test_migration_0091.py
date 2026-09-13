"""Populated upgrade/downgrade and durable ledger foreign-key behavior."""

import importlib.util
from pathlib import Path
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_processing_migration_preserves_pending_sources_and_cancelled_history(
    database_engine, monkeypatch
):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0091_processing_recovery.py"
    )
    spec = importlib.util.spec_from_file_location("processing_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"processing_migration_{uuid.uuid4().hex}"
    values = {
        name: uuid.uuid4() for name in ("item", "feed", "run", "owner", "key", "work")
    }
    with database_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(
            text(
                "CREATE TABLE items (id uuid PRIMARY KEY, tagging_pending boolean NOT NULL)"
            )
        )
        connection.execute(text("CREATE TABLE feeds (id uuid PRIMARY KEY)"))
        connection.execute(text("INSERT INTO items VALUES (:item, true)"), values)
        connection.execute(text("INSERT INTO feeds VALUES (:feed)"), values)
        monkeypatch.setattr(
            module, "op", Operations(MigrationContext.configure(connection))
        )
        module.upgrade()
        assert connection.execute(
            text(
                "SELECT classification_required_at IS NOT NULL, tagging_pending_since_at IS NOT NULL FROM items"
            )
        ).one() == (True, True)
        assert (
            connection.scalar(
                text("SELECT count(*) FROM processing_dispatch_state WHERE id=1")
            )
            == 1
        )
        connection.execute(
            text("""INSERT INTO processing_recovery_runs
            (id, principal_type, principal_id, idempotency_key, request_hash, authorization_encrypted, source_encrypted, status, version, total_count)
            VALUES (:run, 'user', :owner, :key, 'hash', '{}', '{}', 'queued', 1, 1)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO processing_work
            (id,item_id,feed_id,stage,source_version,generation,version,status,attempts,recovery_run_id)
            VALUES (:work,:item,:feed,'classification',1,1,1,'waiting',0,:run)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO processing_recovery_items
            (run_id,item_id,stage,work_id,generation,state) VALUES (:run,:item,'classification',:work,1,'queued')"""),
            values,
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("UPDATE processing_recovery_runs SET total_count=101")
            )
        connection.execute(text("DELETE FROM items WHERE id=:item"), values)
        assert connection.scalar(text("SELECT count(*) FROM processing_work")) == 0
        assert connection.scalar(
            text("SELECT work_id IS NULL FROM processing_recovery_items")
        )
        connection.execute(text("DELETE FROM processing_recovery_runs"))
        assert (
            connection.scalar(text("SELECT count(*) FROM processing_recovery_items"))
            == 0
        )
        module.downgrade()
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema AND table_name LIKE 'processing_%'"
                ),
                {"schema": schema},
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema=:schema AND table_name='items' AND column_name='tagging_pending_since_at'"
                ),
                {"schema": schema},
            )
            == 0
        )
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
