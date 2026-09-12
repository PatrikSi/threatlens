import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.services.ai_integration import AIDailyBriefGenerationResult
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


def test_busy_daily_brief_delivery_defers_unstarted_run(db_session, monkeypatch):
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

    before_delivery = datetime.now(timezone.utc)
    result = dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(run_id)},
        task_id=str(uuid.uuid4()),
        throw=True,
    ).get()

    db_session.expire_all()
    persisted = db_session.get(AITaskRun, run_id)
    assert persisted.status == "queued"
    assert persisted.reason is None
    assert persisted.finished_at is None
    assert result == {"status": "queued", "reason": "brief_lock_busy", "run_id": str(run_id)}
    dispatch = db_session.get(AIWorkflowDispatch, run_id)
    assert dispatch.state == "pending"
    assert dispatch.error == "brief_lock_busy"
    assert dispatch.next_attempt_at >= before_delivery + timedelta(seconds=30)


def test_duplicate_before_owner_claim_defers_and_owner_can_complete(
    db_session, monkeypatch
):
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"force": True},
    )
    run.celery_task_id = "original-task"
    db_session.commit()
    run_id = run.id
    depth = 0
    duplicate_results = []

    @contextmanager
    def session_override():
        yield db_session

    @contextmanager
    def interleaved_lock():
        nonlocal depth
        depth += 1
        try:
            if depth == 1:
                # Pause the owner after Redis acquisition, before the first
                # database claim, and deliver the same broker message again.
                duplicate_results.append(
                    dispatch_daily_ai_brief_generation.apply(
                        kwargs={"force": True, "task_run_id": str(run_id)},
                        task_id="original-task",
                        throw=True,
                    ).get()
                )
                db_session.expire_all()
                pending = db_session.get(AITaskRun, run_id)
                assert pending.status == "queued"
                assert pending.finished_at is None
                assert db_session.get(AIWorkflowDispatch, run_id).state == "pending"
                yield True
            else:
                yield False
        finally:
            depth -= 1

    monkeypatch.setattr("app.tasks.ai_brief_tasks.db_session", session_override)
    monkeypatch.setattr("app.tasks.ai_brief_tasks.daily_ai_brief_lock", interleaved_lock)
    monkeypatch.setattr(
        "app.tasks.ai_brief_tasks.load_active_ai_settings",
        lambda *_args, **_kwargs: SimpleNamespace(
            ai_enabled=True, ai_configured=True, daily_brief_enabled=True,
            model="synthetic-model",
        ),
    )
    monkeypatch.setattr(
        "app.tasks.ai_brief_tasks.run_daily_brief_generation",
        lambda *_args, **_kwargs: AIDailyBriefGenerationResult(
            brief=None, status="ready", reason=None, items_considered=0,
            items_selected=0,
        ),
    )

    result = dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(run_id)},
        task_id="original-task",
        throw=True,
    ).get()

    db_session.expire_all()
    persisted = db_session.get(AITaskRun, run_id)
    assert duplicate_results == [{"status": "queued", "reason": "brief_lock_busy", "run_id": str(run_id)}]
    assert result == {"status": "ready", "reason": None}
    assert persisted.status == "ready"
    assert persisted.finished_at is not None
    assert persisted.celery_task_id == "original-task"
    assert db_session.get(AIWorkflowDispatch, run_id).state == "complete"


@pytest.mark.parametrize("kind", ["daily_brief", "backfill"])
def test_missing_accepted_brief_history_cannot_create_fresh_work(db_session, monkeypatch, kind):
    from app.tasks.ai_brief_tasks import backfill_daily_ai_briefs
    from sqlalchemy import func, select

    @contextmanager
    def session_override():
        yield db_session

    @contextmanager
    def available_lock():
        yield True

    before = db_session.scalar(select(func.count()).select_from(AITaskRun))
    monkeypatch.setattr("app.tasks.ai_brief_tasks.db_session", session_override)
    monkeypatch.setattr("app.tasks.ai_brief_tasks.daily_ai_brief_lock", available_lock)
    monkeypatch.setattr("app.tasks.ai_brief_tasks.run_daily_brief_generation",
                        lambda *args, **kwargs: pytest.fail("missing accepted history must not call a provider"))
    task = dispatch_daily_ai_brief_generation if kind == "daily_brief" else backfill_daily_ai_briefs
    kwargs = {"task_run_id": str(uuid.uuid4()), **({"force": True} if kind == "daily_brief" else {"days": 1})}
    result = task.apply(kwargs=kwargs, task_id="obsolete-delivery", throw=True).get()
    assert result["reason"] == "task_history_unavailable"
    assert db_session.scalar(select(func.count()).select_from(AITaskRun)) == before
