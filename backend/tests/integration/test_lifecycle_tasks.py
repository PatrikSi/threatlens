"""Lifecycle task retries and continuations preserve committed PostgreSQL progress."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import uuid

from celery.exceptions import Retry
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.db.budgets import DatabaseDeadlineExceeded
from app.models.audit_log import AuditLog
from app.models.lifecycle import LifecyclePreview, LifecycleRun
from app.services import lifecycle_execution as execution
from app.services.lifecycle import ensure_lifecycle_policies
from app.tasks import lifecycle_tasks as tasks
from tests.integration.test_lifecycle_transaction_budget import _create_run


@pytest.fixture
def task_db(db_session, monkeypatch):
    @contextmanager
    def sessions():
        yield db_session

    monkeypatch.setattr(tasks, "db_session", sessions)
    # Deadline telemetry is independent of the task's durable retry contract.
    monkeypatch.setattr("app.db.budgets.record_runtime_event", lambda _event: None)
    # Only the explicitly seeded queued runs are due during these tests.
    for policy in ensure_lifecycle_policies(db_session):
        if policy.enabled:
            policy.next_run_at = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.commit()
    return db_session


def _invoke(run_id, *, task_id, retries=0):
    task = tasks.execute_lifecycle_run_task
    task.push_request(id=task_id, retries=retries)
    try:
        return task.run(str(run_id))
    finally:
        task.pop_request()


@pytest.mark.parametrize("run_id", ["invalid", "", None, [], {}])
def test_invalid_lifecycle_run_does_not_open_database(monkeypatch, run_id):
    def unexpected():
        pytest.fail("Invalid lifecycle identity opened a database session")

    monkeypatch.setattr(tasks, "db_session", unexpected)
    assert tasks.execute_lifecycle_run_task.run(run_id) == {"status": "invalid_run_id"}


def test_lifecycle_publication_reuses_durable_identity_and_rejects_finished_run(task_db, monkeypatch):
    db = task_db
    run_id, candidate_id = _create_run(db)
    publications = []
    monkeypatch.setattr(tasks.execute_lifecycle_run_task, "apply_async", lambda **kwargs: publications.append(kwargs))
    task_id = tasks.enqueue_lifecycle_run(db, run_id=run_id)
    assert tasks.enqueue_lifecycle_run(db, run_id=run_id) == task_id
    assert publications == [
        {"args": [str(run_id)], "task_id": task_id},
        {"args": [str(run_id)], "task_id": task_id},
    ]
    assert _invoke(run_id, task_id="stale-task-id")["status"] == "ignored"
    assert db.get(AuditLog, candidate_id) is not None
    assert _invoke(run_id, task_id=task_id)["status"] == "succeeded"
    db.expire_all()
    assert db.get(AuditLog, candidate_id) is None
    assert db.get(LifecycleRun, run_id).affected_count == 1
    assert _invoke(run_id, task_id=task_id)["status"] == "ignored"
    with pytest.raises(RuntimeError, match="no longer queued"):
        tasks.enqueue_lifecycle_run(db, run_id=run_id)


@pytest.mark.parametrize("already_reserved", [False, True])
def test_lifecycle_publication_failure_preserves_prior_reservation(task_db, monkeypatch, already_reserved):
    db = task_db
    run_id, _ = _create_run(db)
    original_id = f"{run_id}:accepted" if already_reserved else None
    if original_id:
        execution.mark_lifecycle_run_published(db, run_id=run_id, celery_task_id=original_id)

    def disconnected(**_kwargs):
        raise ConnectionError("Synthetic publication unavailable")

    monkeypatch.setattr(tasks.execute_lifecycle_run_task, "apply_async", disconnected)
    with pytest.raises(ConnectionError):
        tasks.enqueue_lifecycle_run(db, run_id=run_id)
    db.expire_all()
    run = db.get(LifecycleRun, run_id)
    assert run.status == "queued" and run.celery_task_id == original_id


@pytest.mark.parametrize("error", [
    DatabaseDeadlineExceeded("Synthetic database deadline"),
    OperationalError("synthetic operation", {}, Exception("Synthetic database disconnect")),
])
def test_lifecycle_task_retries_transient_failure_then_settles_exhaustion(task_db, monkeypatch, error):
    db = task_db
    run_id, candidate_id = _create_run(db)
    task_id = f"{run_id}:retry"
    execution.mark_lifecycle_run_published(db, run_id=run_id, celery_task_id=task_id)

    def fail_batch(*_args, **_kwargs):
        raise error

    retries = []

    def retry(**kwargs):
        retries.append(kwargs)
        return Retry()

    monkeypatch.setattr(execution, "execute_lifecycle_target_batch", fail_batch)
    monkeypatch.setattr(tasks.execute_lifecycle_run_task, "retry", retry)
    for attempt, countdown in ((0, 2), (4, 32)):
        with pytest.raises(Retry):
            _invoke(run_id, task_id=task_id, retries=attempt)
        db.expire_all()
        run = db.get(LifecycleRun, run_id)
        assert run.status == "queued" and run.celery_task_id == task_id
        assert run.affected_count == 0 and run.lease_token is None
        assert db.get(AuditLog, candidate_id) is not None
        assert retries[-1] == {"exc": error, "countdown": countdown}

    result = _invoke(run_id, task_id=task_id, retries=5)
    assert result == {"status": "failed", "run_id": str(run_id)}
    db.expire_all()
    run = db.get(LifecycleRun, run_id)
    assert run.status == "failed" and run.error_code == "transient_retries_exhausted"
    assert run.stop_reason == "retry_limit" and run.affected_count == 0
    assert db.get(AuditLog, candidate_id) is not None
    assert len(retries) == 2


def test_lifecycle_continuation_publish_failure_keeps_progress_for_dispatch(task_db, monkeypatch):
    db = task_db
    run_id, first_id = _create_run(db)
    created_at = db.get(AuditLog, first_id).created_at
    candidates = [AuditLog(
        action="lifecycle.budget.fixture", resource_type="test_fixture",
        success=True, metadata_json={}, created_at=created_at,
    ) for _ in range(2)]
    db.add_all(candidates)
    db.flush()
    candidate_ids = [first_id, *(entry.id for entry in candidates)]
    db.commit()
    task_id = f"{run_id}:first"
    execution.mark_lifecycle_run_published(db, run_id=run_id, celery_task_id=task_id)
    monkeypatch.setattr(execution, "EXECUTION_BATCH_SIZE", 1)
    monkeypatch.setattr(execution, "MAX_BATCHES_PER_INVOCATION", 1)

    def disconnected(**_kwargs):
        raise ConnectionError("Synthetic continuation unavailable")

    monkeypatch.setattr(tasks.execute_lifecycle_run_task, "apply_async", disconnected)
    assert _invoke(run_id, task_id=task_id)["status"] == "queued"
    db.expire_all()
    run = db.get(LifecycleRun, run_id)
    assert run.status == "queued" and run.celery_task_id is None
    assert run.affected_count == run.batch_count == 1
    remaining = list(db.scalars(select(AuditLog.id).where(AuditLog.id.in_(candidate_ids))))
    assert len(remaining) == 2

    failed = tasks.dispatch_due_lifecycle_runs_task()
    assert failed == {"status": "ok", "queued": 1, "published": 0, "publish_failed": 1}
    db.expire_all()
    assert db.get(LifecycleRun, run_id).affected_count == 1
    assert db.get(LifecycleRun, run_id).celery_task_id is None

    published = []
    monkeypatch.setattr(tasks.execute_lifecycle_run_task, "apply_async", lambda **kwargs: published.append(kwargs))
    assert tasks.dispatch_due_lifecycle_runs_task() == {
        "status": "ok", "queued": 1, "published": 1, "publish_failed": 0,
    }
    monkeypatch.setattr(execution, "MAX_BATCHES_PER_INVOCATION", 3)
    assert _invoke(run_id, task_id=published[0]["task_id"])["status"] == "succeeded"
    db.expire_all()
    assert db.get(LifecycleRun, run_id).affected_count == 3
    assert list(db.scalars(select(AuditLog.id).where(AuditLog.id.in_(candidate_ids)))) == []


def test_lifecycle_housekeeping_task_deletes_expired_preview_and_keeps_live_preview(task_db):
    db = task_db
    now = datetime.now(timezone.utc)
    previews = [LifecyclePreview(
        target_key="audit_logs", policy_revision=1, policy_snapshot_json={},
        request_fingerprint=uuid.uuid4().hex * 2, cutoff_at=now - timedelta(days=90),
        generated_at=now - timedelta(hours=2), expires_at=expiry,
    ) for expiry in (now - timedelta(hours=1), now + timedelta(hours=1))]
    db.add_all(previews)
    db.flush()
    expired_id, live_id = (entry.id for entry in previews)
    db.commit()
    result = tasks.run_lifecycle_housekeeping_task()
    assert result["status"] == "ok" and result["lifecycle_previews_deleted"] == 1
    db.expire_all()
    assert db.get(LifecyclePreview, expired_id) is None
    assert db.get(LifecyclePreview, live_id) is not None
