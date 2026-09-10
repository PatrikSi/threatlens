import importlib.util
from pathlib import Path
from datetime import datetime, timezone

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import delete, text

from app.models.audit_log import AuditLog


def test_permission_history_downgrade_preserves_hidden_history(db_session, monkeypatch):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0094_permission_history_pruning.py"
    )
    spec = importlib.util.spec_from_file_location("permission_history_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration, "op", Operations(MigrationContext.configure(db_session.connection()))
    )
    row = AuditLog(
        action="test.pruning",
        resource_type="test",
        metadata_json={},
        retention_pruning_started_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.flush()
    with pytest.raises(
        RuntimeError, match="Finish permission-history retention cleanup"
    ):
        migration.downgrade()
    assert db_session.scalar(
        text(
            "SELECT retention_pruning_started_at IS NOT NULL FROM audit_logs WHERE id=:id"
        ),
        {"id": row.id},
    )
    db_session.execute(delete(AuditLog).where(AuditLog.id == row.id))
    migration.downgrade()
    migration.upgrade()
    assert (
        db_session.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name='audit_logs' "
                "AND column_name='retention_pruning_started_at'"
            )
        )
        == 1
    )
