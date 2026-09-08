from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import func, select, text

from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.audit_log import AuditLog
from app.models.lifecycle import LifecycleRun
from app.models.lifecycle_scan import LifecycleScanCursor
from app.services import lifecycle_dependencies, lifecycle_execution
from app.services.lifecycle import ensure_lifecycle_policies
from app.services.lifecycle_targets import execute_lifecycle_target_batch


def _batch(db, target, cutoff):
    return execute_lifecycle_target_batch(
        db, target_key=target, cutoff=cutoff, batch_size=1, run_id=uuid.uuid4()
    )


def _audit_prefix(db, monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(days=400)
    ids = [uuid.UUID(int=value) for value in range(1, 7)]
    db.add_all(
        [
            AuditLog(
                id=record_id,
                action="scan_fixture",
                resource_type="test",
                created_at=old,
            )
            for record_id in ids
        ]
    )
    db.flush()
    protected = set(ids[:4])
    monkeypatch.setattr(
        lifecycle_dependencies,
        "lifecycle_dependent_row_counts",
        lambda _db, *, parent_ids, **_kw: {
            record_id: 10_001 if record_id in protected else 0
            for record_id in parent_ids
        },
    )
    return old + timedelta(days=1), ids, protected


def test_real_ai_event_prefix_cannot_starve_later_run(db_session):
    old = datetime.now(timezone.utc) - timedelta(days=400)
    ids = [uuid.UUID(int=value) for value in range(1, 6)]
    db_session.add_all(
        [
            AITaskRun(
                id=record_id,
                task_type="connection_test",
                trigger_source="manual",
                status="succeeded",
                finished_at=old,
            )
            for record_id in ids
        ]
    )
    db_session.flush()
    for record_id in ids[:4]:
        db_session.execute(
            text(
                "INSERT INTO ai_task_events (id, task_run_id, event_type, payload_json) "
                "SELECT gen_random_uuid(), :run_id, 'step', '{}' FROM generate_series(1, 10001)"
            ),
            {"run_id": record_id},
        )
    cutoff = old + timedelta(days=1)
    first = _batch(db_session, "ai_task_history", cutoff)
    assert first.affected_count == 0
    assert first.details["scan_anchors_advanced"] == 4
    db_session.commit()
    db_session.expunge_all()
    second = _batch(db_session, "ai_task_history", cutoff)
    db_session.commit()
    assert second.affected_count == 1
    assert db_session.scalar(select(func.count()).select_from(AITaskEvent)) == 40_004
    assert db_session.get(AITaskRun, ids[-1]) is None
    assert all(
        db_session.get(AITaskRun, record_id) is not None for record_id in ids[:4]
    )


def test_cursor_preserves_budget_skips_and_revisits_protected_records(
    db_session, monkeypatch
):
    cutoff, ids, protected = _audit_prefix(db_session, monkeypatch)
    first = _batch(db_session, "audit_logs", cutoff)
    assert first.affected_count == 0
    assert first.details["scan_anchors_advanced"] == 4
    second = _batch(db_session, "audit_logs", cutoff)
    db_session.flush()
    assert second.affected_count == 1
    assert db_session.get(LifecycleScanCursor, "audit_logs").last_id == ids[4]
    # The sixth record was over the parent budget, so remains ahead of the anchor.
    assert _batch(db_session, "audit_logs", cutoff).affected_count == 1
    assert (
        _batch(db_session, "audit_logs", cutoff).details["scan_cycles_completed"] == 1
    )
    protected.clear()
    assert _batch(db_session, "audit_logs", cutoff).affected_count == 1
    db_session.expire_all()
    assert db_session.get(AuditLog, ids[0]) is None


def test_scan_progress_rolls_back_with_deletion(db_session, monkeypatch):
    cutoff, ids, _protected = _audit_prefix(db_session, monkeypatch)
    _batch(db_session, "audit_logs", cutoff)
    db_session.flush()
    with pytest.raises(RuntimeError), db_session.begin_nested():
        assert _batch(db_session, "audit_logs", cutoff).affected_count == 1
        db_session.flush()
        raise RuntimeError("Worker lost before transaction committed")
    db_session.expire_all()
    assert db_session.get(LifecycleScanCursor, "audit_logs").last_id == ids[3]
    assert db_session.get(AuditLog, ids[4]) is not None
    assert _batch(db_session, "audit_logs", cutoff).affected_count == 1


def test_remainder_budget_cannot_mark_an_affordable_parent_as_oversized(
    db_session, monkeypatch
):
    ids = [uuid.uuid4(), uuid.uuid4()]
    monkeypatch.setattr(
        lifecycle_dependencies,
        "lifecycle_dependent_row_counts",
        lambda *args, **kwargs: {ids[0]: 5, ids[1]: 2},
    )
    selection = lifecycle_dependencies.select_with_dependent_budget(
        db_session,
        model=AuditLog,
        candidate_ids=ids,
        max_dependent_rows=10,
        available_dependent_rows=2,
    )
    assert selection.ids == ids[1:]
    assert selection.oversized_count == 0
    assert selection.budget_exhausted
    assert selection.completed_prefix_length == 0


def test_durable_run_continues_after_zero_deletions_with_scan_progress(
    db_session, monkeypatch
):
    cutoff, _ids, _protected = _audit_prefix(db_session, monkeypatch)
    policy = next(
        p for p in ensure_lifecycle_policies(db_session) if p.target_key == "audit_logs"
    )
    scheduled_for = cutoff + timedelta(days=policy.retention_days)
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json=lifecycle_execution._policy_snapshot(policy),
        cutoff_at=cutoff,
        scheduled_for=scheduled_for,
        max_records=policy.max_records_per_run,
        queued_at=scheduled_for,
    )
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(lifecycle_execution, "EXECUTION_BATCH_SIZE", 1)
    monkeypatch.setattr(lifecycle_execution, "MAX_BATCHES_PER_INVOCATION", 1)
    assert (
        lifecycle_execution.execute_lifecycle_run(db_session, run_id=run.id)["status"]
        == "queued"
    )
    db_session.refresh(run)
    assert run.affected_count == 0
    assert run.details_json["scan_anchors_advanced"] == 4
    assert (
        lifecycle_execution.execute_lifecycle_run(db_session, run_id=run.id)["status"]
        == "queued"
    )
    db_session.refresh(run)
    assert run.affected_count == 1


@pytest.mark.parametrize("all_protected", [False, True])
def test_run_scan_limit_keeps_next_run_anchor(db_session, monkeypatch, all_protected):
    cutoff, ids, protected = _audit_prefix(db_session, monkeypatch)
    if all_protected:
        protected.update(ids)
    policy = next(
        p for p in ensure_lifecycle_policies(db_session) if p.target_key == "audit_logs"
    )
    scheduled_for = cutoff + timedelta(days=policy.retention_days)
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json=lifecycle_execution._policy_snapshot(policy),
        cutoff_at=cutoff,
        scheduled_for=scheduled_for,
        max_records=policy.max_records_per_run,
        queued_at=scheduled_for,
    )
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(lifecycle_execution, "EXECUTION_BATCH_SIZE", 1)
    monkeypatch.setattr(lifecycle_execution, "MAX_SCAN_ADVANCES_PER_RUN", 4)
    lifecycle_execution.execute_lifecycle_run(db_session, run_id=run.id)
    db_session.refresh(run)
    assert run.stop_reason == "scan_limit"
    assert run.status == "partial"
    assert db_session.get(LifecycleScanCursor, "audit_logs").last_id == ids[3]
    protected.difference_update(ids[4:])
    assert _batch(db_session, "audit_logs", cutoff).affected_count == 1
