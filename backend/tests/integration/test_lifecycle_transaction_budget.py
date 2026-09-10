from datetime import datetime, timedelta, timezone
import time
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.budgets import DatabaseDeadlineExceeded
from app.models.audit_log import AuditLog
from app.models.lifecycle import LifecycleRun
from app.services.lifecycle import ensure_lifecycle_policies
from app.services.lifecycle_execution import execute_lifecycle_run


def _create_run(db: Session):
    now = datetime.now(timezone.utc)
    policy = next(
        row for row in ensure_lifecycle_policies(db) if row.target_key == "audit_logs"
    )
    candidate = AuditLog(
        action="lifecycle.budget.fixture",
        resource_type="test_fixture",
        success=True,
        metadata_json={},
        created_at=now - timedelta(days=policy.retention_days + 1),
    )
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json={
            "target_key": policy.target_key,
            "enabled": policy.enabled,
            "retention_days": policy.retention_days,
            "schedule_cadence": policy.schedule_cadence,
            "schedule_hour_utc": policy.schedule_hour_utc,
            "schedule_weekday": policy.schedule_weekday,
            "max_records_per_run": policy.max_records_per_run,
            "options": {},
        },
        cutoff_at=now - timedelta(days=policy.retention_days),
        scheduled_for=now,
        max_records=policy.max_records_per_run,
        queued_at=now,
    )
    db.add_all([candidate, run])
    db.flush()
    result = run.id, candidate.id
    db.commit()
    return result


@pytest.mark.parametrize("delay_kind", ["deferred_constraint", "after_target"])
def test_lifecycle_transaction_deadline_rolls_back_target_and_progress(
    db_session,
    monkeypatch,
    delay_kind,
):
    import app.services.lifecycle_execution as execution
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "database_operation_timeout_seconds", 0.4)
    db = db_session
    run_id, candidate_id = _create_run(db)
    trigger = "lifecycle_budget_" + uuid.uuid4().hex
    try:
        if delay_kind == "deferred_constraint":
            db.execute(
                text("""
                CREATE FUNCTION pg_temp.delay_lifecycle_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF OLD.action = 'lifecycle.budget.fixture' THEN PERFORM pg_sleep(0.6); END IF;
                    RETURN OLD;
                END $$
            """)
            )
            db.execute(
                text(f"""
                CREATE CONSTRAINT TRIGGER {trigger} AFTER DELETE ON audit_logs
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pg_temp.delay_lifecycle_commit()
            """)
            )
            db.commit()
            expected_error = OperationalError
        else:
            original = execution.execute_lifecycle_target_batch

            def delay_after_target(*args, **kwargs):
                result = original(*args, **kwargs)
                time.sleep(0.6)
                return result

            monkeypatch.setattr(
                execution, "execute_lifecycle_target_batch", delay_after_target
            )
            expected_error = DatabaseDeadlineExceeded
        started = time.monotonic()
        with pytest.raises(expected_error) as failure:
            execute_lifecycle_run(db, run_id=run_id)
        assert time.monotonic() - started < 2
        if delay_kind == "deferred_constraint":
            assert failure.value.orig.sqlstate == "57014"
        db.expire_all()
        run = db.get(LifecycleRun, run_id)
        assert run.status == "queued"
        assert run.affected_count == run.batch_count == 0
        assert db.get(AuditLog, candidate_id) is not None
    finally:
        db.rollback()
        db.execute(text(f"DROP TRIGGER IF EXISTS {trigger} ON audit_logs"))
        db.execute(delete(AuditLog).where(AuditLog.id == candidate_id))
        db.execute(delete(LifecycleRun).where(LifecycleRun.id == run_id))
        db.commit()
