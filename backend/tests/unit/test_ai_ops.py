import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.ai_task_run import AITaskRun
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_SKIPPED,
    AI_STATUS_RUNNING,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
    _flatten_live_tasks,
    finish_ai_task_run,
    list_ai_task_runs,
    queue_ai_task_run,
    start_ai_task_run,
)


def test_list_ai_task_runs_reconciles_stale_reprocess_and_child_runs(db_session, monkeypatch):
    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 1},
        target_count=1,
    )
    child_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        parent_run_id=parent_run.id,
        item_id=uuid.uuid4(),
        metadata={"parent_task": "reprocess"},
    )
    start_ai_task_run(db_session, run_id=parent_run.id, worker_name="celery@test", celery_task_id="parent-task-id")
    start_ai_task_run(db_session, run_id=child_run.id, worker_name="celery@test", celery_task_id="child-task-id")

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    parent_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    child_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == child_run.id))
    assert parent_run is not None
    assert child_run is not None

    parent_run.status = AI_STATUS_RUNNING
    parent_run.queued_at = stale_time
    parent_run.started_at = stale_time
    parent_run.created_at = stale_time
    parent_run.updated_at = stale_time

    child_run.status = AI_STATUS_RUNNING
    child_run.queued_at = stale_time
    child_run.started_at = stale_time
    child_run.created_at = stale_time
    child_run.updated_at = stale_time
    db_session.add_all([parent_run, child_run])
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: ([], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_REPROCESS, limit=10)

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    refreshed_child = db_session.scalar(select(AITaskRun).where(AITaskRun.id == child_run.id))

    assert refreshed_child is not None
    assert refreshed_child.status == AI_STATUS_ERROR
    assert refreshed_child.reason == "stale_task_lost"
    assert refreshed_child.finished_at is not None

    assert refreshed_parent is not None
    assert refreshed_parent.status == AI_STATUS_ERROR
    assert refreshed_parent.reason == "partial_failures"
    assert refreshed_parent.processed_count == 1
    assert refreshed_parent.error_count == 1
    assert refreshed_parent.finished_at is not None

    assert response.items[0].id == parent_run.id
    assert response.items[0].status == AI_STATUS_ERROR


def test_start_and_finish_do_not_overwrite_canceled_runs(db_session):
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=uuid.uuid4(),
    )
    finish_ai_task_run(
        db_session,
        run_id=run.id,
        status=AI_STATUS_SKIPPED,
        reason="canceled",
        worker_name="api",
    )
    db_session.commit()

    started = start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="late-task")
    finished = finish_ai_task_run(db_session, run_id=run.id, status=AI_STATUS_ERROR, reason="unexpected_error")
    db_session.commit()

    assert started is not None
    assert finished is not None

    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_SKIPPED
    assert refreshed.reason == "canceled"
    assert refreshed.worker_name == "api"
    assert refreshed.celery_task_id is None


def test_flatten_live_tasks_extracts_run_ids_from_positional_args():
    raw = {
        "worker@test": [
            {
                "id": "daily-brief-task",
                "name": "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation",
                "args": "(True, '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')",
                "kwargs": {},
            },
            {
                "id": "item-task",
                "name": "app.tasks.feed_tasks.generate_item_ai_enrichment",
                "args": "('33333333-3333-3333-3333-333333333333', False, '44444444-4444-4444-4444-444444444444')",
                "kwargs": {},
            },
        ]
    }

    flattened = _flatten_live_tasks(raw, state="scheduled")

    assert len(flattened) == 2
    assert flattened[0].task_name == "daily_brief"
    assert flattened[0].run_id == uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert flattened[1].task_name == "item_enrichment"
    assert flattened[1].item_id == uuid.UUID("33333333-3333-3333-3333-333333333333")
    assert flattened[1].run_id == uuid.UUID("44444444-4444-4444-4444-444444444444")
