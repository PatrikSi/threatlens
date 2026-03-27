import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.ai_task_run import AITaskRun
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_RUNNING,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
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

