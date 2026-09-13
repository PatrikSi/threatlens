import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, text

from app.models.ai_task_run import AITaskRun
from app.models.lifecycle_pruning import LifecyclePruningRecord


@pytest.fixture
def migration(db_session):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0099_ai_workflow_history_pruning.py"
    spec = importlib.util.spec_from_file_location("workflow_pruning_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(db_session.connection()))
    return migration


def test_workflow_pruning_guards_upgrade_and_downgrade(db_session, migration):
    names = [f"trg_pruning_{name}" for name, _, _ in migration.REFERENCES]

    def installed():
        return set(db_session.scalars(text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(:names)"
        ), {"names": names}))

    assert installed() == set(names)
    migration.downgrade()
    assert installed() == set()
    migration.upgrade()
    assert installed() == set(names)


def test_downgrade_preserves_reference_guards_while_ai_history_pruning_is_active(db_session, migration):
    now = datetime.now(timezone.utc)
    run = AITaskRun(task_type="reprocess", trigger_source="manual", status="ready", finished_at=now)
    db_session.add(run)
    db_session.flush()
    db_session.add(LifecyclePruningRecord(dataset="ai_task_runs", parent_id=run.id, cutoff_at=now))
    db_session.flush()
    with pytest.raises(RuntimeError, match="Complete AI task history pruning"):
        migration.downgrade()
    for name, _, _ in migration.REFERENCES:
        assert db_session.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname = :name)"),
                                 {"name": f"trg_pruning_{name}"})
    # Normal parent deletion removes its durable pruning claim.
    db_session.execute(delete(AITaskRun).where(AITaskRun.id == run.id))
    migration.downgrade()
    migration.upgrade()
