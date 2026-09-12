import uuid
from contextlib import contextmanager

import pytest

from app.models.ai_task_run import AITaskRun
from app.services.ai_ops import (
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TRIGGER_MANUAL,
    finish_ai_task_run,
    queue_ai_task_run,
    start_ai_task_run,
)
from app.tasks.ai_brief_tasks import dispatch_daily_ai_brief_generation


@pytest.mark.parametrize("delivery_task_id", ["original-task", "duplicate-task"])
def test_busy_daily_brief_delivery_preserves_running_owner(
    db_session, monkeypatch, delivery_task_id
):
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"force": True},
    )
    start_ai_task_run(
        db_session,
        run_id=run.id,
        worker_name="celery@original-worker",
        celery_task_id="original-task",
    )
    db_session.commit()
    run_id = run.id
    started_at = run.started_at

    @contextmanager
    def session_override():
        yield db_session

    @contextmanager
    def busy_lock():
        yield False

    def unexpected_generation(*_args, **_kwargs):
        pytest.fail("A delivery without the daily-brief lock must not call the provider")

    monkeypatch.setattr("app.tasks.ai_brief_tasks.db_session", session_override)
    monkeypatch.setattr("app.tasks.ai_brief_tasks.daily_ai_brief_lock", busy_lock)
    monkeypatch.setattr(
        "app.tasks.ai_brief_tasks.run_daily_brief_generation", unexpected_generation
    )

    result = dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(run_id)},
        task_id=delivery_task_id,
    ).get()

    db_session.expire_all()
    persisted = db_session.get(AITaskRun, run_id)
    assert result == {
        "status": "skipped",
        "reason": "already_running",
        "run_id": str(run_id),
    }
    assert persisted.status == "running"
    assert persisted.finished_at is None
    assert persisted.reason is None
    assert persisted.worker_name == "celery@original-worker"
    assert persisted.celery_task_id == "original-task"
    assert persisted.started_at == started_at

    finish_ai_task_run(
        db_session,
        run_id=run_id,
        status="ready",
        worker_name="celery@original-worker",
    )
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(AITaskRun, run_id).status == "ready"


def test_busy_daily_brief_delivery_still_settles_unstarted_run(db_session, monkeypatch):
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"force": True},
    )
    db_session.commit()
    run_id = run.id

    @contextmanager
    def session_override():
        yield db_session

    @contextmanager
    def busy_lock():
        yield False

    monkeypatch.setattr("app.tasks.ai_brief_tasks.db_session", session_override)
    monkeypatch.setattr("app.tasks.ai_brief_tasks.daily_ai_brief_lock", busy_lock)

    dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(run_id)},
        task_id=str(uuid.uuid4()),
    ).get()

    db_session.expire_all()
    persisted = db_session.get(AITaskRun, run_id)
    assert persisted.status == "skipped"
    assert persisted.reason == "already_running"
    assert persisted.finished_at is not None
