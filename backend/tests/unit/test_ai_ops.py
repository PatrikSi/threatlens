import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_READY,
    AI_STATUS_RUNNING,
    AI_STATUS_SKIPPED,
    AI_TASK_TYPE_CONNECTION_TEST,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
    _flatten_live_tasks,
    _load_live_task_snapshot,
    cancel_ai_task_run,
    finish_ai_task_run,
    get_ai_connection_test_workload,
    get_ai_ops_overview,
    get_ai_task_run_detail,
    list_ai_task_runs,
    queue_ai_task_run,
    start_ai_task_run,
)
from app.schemas.ai import AILiveTaskResponse


def _create_item(db_session: Session, *, source_guid: str) -> Item:
    feed = Feed(
        id=uuid.uuid4(),
        name=f"Feed {source_guid}",
        url=f"https://example.com/{source_guid}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=source_guid,
        url=f"https://example.com/articles/{source_guid}",
        canonical_url=f"https://example.com/articles/{source_guid}",
        title=f"Item {source_guid}",
        dedupe_key=source_guid,
        content_hash=source_guid[-1] * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.commit()
    return item


def test_list_ai_task_runs_reconciles_stale_reprocess_and_child_runs(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="stale-child")

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
        item_id=item.id,
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

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

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


def test_list_ai_task_runs_can_skip_stale_reconciliation_for_plain_history(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="plain-history")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="plain-history-task")

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_RUNNING
    run.queued_at = stale_time
    run.started_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    def fail_live_snapshot():
        raise AssertionError("plain history reads should not inspect live workers")

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", fail_live_snapshot)

    response = list_ai_task_runs(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        limit=10,
        reconcile_stale=False,
    )

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_RUNNING
    assert refreshed.finished_at is None
    assert response.items[0].status == AI_STATUS_RUNNING


def test_list_ai_task_runs_preserves_very_old_stale_run_durations(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="very-old-stale-run")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="very-old-task")

    stale_time = datetime.now(timezone.utc) - timedelta(days=60)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_RUNNING
    run.queued_at = stale_time
    run.started_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_ERROR
    assert refreshed.reason == "stale_task_lost"
    assert refreshed.duration_ms is not None
    assert refreshed.duration_ms > 2_147_483_647
    assert response.items[0].duration_ms == refreshed.duration_ms


def test_ai_ops_overview_uses_database_queue_snapshot_without_live_inspection(db_session, monkeypatch):
    queued_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 10},
    )
    running_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    start_ai_task_run(db_session, run_id=running_run.id, worker_name="worker@test", celery_task_id="running-task-id")
    db_session.commit()

    def fail_live_snapshot():
        raise AssertionError("status overview should not inspect live workers")

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", fail_live_snapshot)

    overview = get_ai_ops_overview(db_session, days=30)

    assert overview.live.queued_count == 1
    assert overview.live.active_count == 1
    assert overview.live.worker_count == 1
    assert overview.live.workers == ["worker@test"]
    assert overview.live.active_tasks[0].run_id == running_run.id
    assert overview.live.active_tasks[0].celery_task_id == "running-task-id"
    assert overview.failures == []


def test_ai_connection_workload_counts_generation_tasks(db_session, monkeypatch):
    running_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_CONNECTION_TEST,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    start_ai_task_run(db_session, run_id=running_run.id, worker_name="celery@test", celery_task_id="running-task")
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    workload = get_ai_connection_test_workload(db_session)

    assert workload.running_task_count == 1
    assert workload.queued_task_count == 1
    assert workload.has_active_work is True


def test_get_ai_task_run_detail_skips_stale_reconciliation_for_finished_runs(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="finished-detail")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    finish_ai_task_run(db_session, run_id=run.id, status=AI_STATUS_READY)
    db_session.commit()

    def fail_live_snapshot():
        raise AssertionError("finished run detail reads should not inspect live workers")

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", fail_live_snapshot)

    detail = get_ai_task_run_detail(db_session, run_id=run.id)

    assert detail is not None
    assert detail.run.id == run.id
    assert detail.run.status == AI_STATUS_READY


def test_list_ai_task_runs_does_not_mark_recent_queued_backlog_lost(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="queued-backlog")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_QUEUED
    run.queued_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_QUEUED
    assert refreshed.finished_at is None
    assert response.items[0].status == AI_STATUS_QUEUED


def test_list_ai_task_runs_marks_queued_backlog_lost_after_fallback_grace(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="queued-backlog-stale")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_QUEUED
    run.queued_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_ERROR
    assert refreshed.reason == "stale_queued_task_unstarted"
    assert refreshed.finished_at is not None
    assert response.items[0].status == AI_STATUS_ERROR


def test_start_and_finish_do_not_overwrite_canceled_runs(db_session):
    item = _create_item(db_session, source_guid="canceled-run")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
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


def test_cancel_ai_task_run_marks_running_runs_cancel_requested_until_worker_observes_it(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="cancel-running")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="task-id")
    db_session.commit()

    monkeypatch.setattr(
        "app.services.ai_ops._load_live_task_snapshot",
        lambda: (
            True,
            ["worker@test"],
            [
                AILiveTaskResponse(
                    worker_name="worker@test",
                    celery_task_id="task-id",
                    task_name="item_enrichment",
                    state="active",
                    run_id=run.id,
                    item_id=item.id,
                )
            ],
            [],
            [],
        ),
    )

    canceled = cancel_ai_task_run(db_session, run_id=run.id)

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))

    assert canceled is not None
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_RUNNING
    assert refreshed.finished_at is None
    assert refreshed.reason == "cancel_requested"
    assert refreshed.metadata_json["cancel_requested_at"]
    assert refreshed.metadata_json["terminated_running_task"] is True


def test_load_live_task_snapshot_reports_unavailable_when_no_workers_respond(monkeypatch):
    class _EmptyInspector:
        def ping(self):
            return {}

        def active(self):
            return {}

        def reserved(self):
            return {}

        def scheduled(self):
            return {}

    monkeypatch.setattr("app.services.ai_ops.celery_app.control.inspect", lambda timeout: _EmptyInspector())

    snapshot_available, workers, active_tasks, reserved_tasks, scheduled_tasks = _load_live_task_snapshot()

    assert snapshot_available is False
    assert workers == []
    assert active_tasks == []
    assert reserved_tasks == []
    assert scheduled_tasks == []


def test_list_ai_task_runs_reconciles_stale_runs_when_live_snapshot_unavailable(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="snapshot-unavailable")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="task-id")

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_RUNNING
    run.queued_at = stale_time
    run.started_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (False, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_ERROR
    assert refreshed.reason == "stale_task_snapshot_unavailable"
    assert refreshed.finished_at is not None
    assert (refreshed.metadata_json or {})["stale_snapshot_available"] is False

    assert response.items[0].status == AI_STATUS_ERROR


def test_list_ai_task_runs_still_finishes_accounted_reprocess_runs_when_live_snapshot_unavailable(db_session, monkeypatch):
    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 1},
        target_count=1,
    )
    start_ai_task_run(db_session, run_id=parent_run.id, worker_name="celery@test", celery_task_id="parent-task-id")

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    parent_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert parent_run is not None
    parent_run.status = AI_STATUS_RUNNING
    parent_run.processed_count = 1
    parent_run.success_count = 1
    parent_run.queued_at = stale_time
    parent_run.started_at = stale_time
    parent_run.created_at = stale_time
    parent_run.updated_at = stale_time
    db_session.add(parent_run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (False, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_REPROCESS, limit=10)

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert refreshed_parent is not None
    assert refreshed_parent.status == AI_STATUS_READY
    assert refreshed_parent.reason is None
    assert refreshed_parent.finished_at is not None

    assert response.items[0].status == AI_STATUS_READY


def test_list_ai_task_runs_keeps_recent_runs_when_live_snapshot_unavailable(db_session, monkeypatch):
    item = _create_item(db_session, source_guid="snapshot-unavailable-recent")

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="task-id")

    stale_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_RUNNING
    run.queued_at = stale_time
    run.started_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (False, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert refreshed is not None
    assert refreshed.status == AI_STATUS_RUNNING
    assert refreshed.finished_at is None

    assert response.items[0].status == AI_STATUS_RUNNING


def test_list_ai_task_runs_marks_stale_pending_enrichment_rows_as_error(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="stale-item",
        url="https://example.com/articles/stale-item",
        title="Stale AI task",
        dedupe_key="stale-item",
        content_hash="a" * 64,
        status="content_fetched",
    )
    enrichment = ItemAIEnrichment(
        item_id=item.id,
        status="pending",
        source_hash="hash",
        relevance_reasons_json=[],
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(enrichment)

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="stale-task")

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_RUNNING
    run.queued_at = stale_time
    run.started_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    refreshed_enrichment = db_session.scalar(
        select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id)
    )

    assert refreshed_run is not None
    assert refreshed_run.status == AI_STATUS_ERROR
    assert refreshed_run.reason == "stale_task_lost"

    assert refreshed_enrichment is not None
    assert refreshed_enrichment.status == AI_STATUS_ERROR
    assert refreshed_enrichment.error == "Task no longer appears in Celery and did not report completion"
    assert refreshed_enrichment.generated_at is not None


def test_list_ai_task_runs_marks_snapshot_unavailable_pending_enrichment_rows_as_error(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="snapshot-unavailable-stale-item",
        url="https://example.com/articles/snapshot-unavailable-stale-item",
        title="Stale AI task",
        dedupe_key="snapshot-unavailable-stale-item",
        content_hash="b" * 64,
        status="content_fetched",
    )
    enrichment = ItemAIEnrichment(
        item_id=item.id,
        status="pending",
        source_hash="hash",
        relevance_reasons_json=[],
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(enrichment)

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="celery@test", celery_task_id="stale-task")

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert run is not None
    run.status = AI_STATUS_RUNNING
    run.queued_at = stale_time
    run.started_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (False, [], [], [], []))

    list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_ITEM_ENRICHMENT, limit=10)

    db_session.expire_all()
    refreshed_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    refreshed_enrichment = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))

    assert refreshed_run is not None
    assert refreshed_run.status == AI_STATUS_ERROR
    assert refreshed_run.reason == "stale_task_snapshot_unavailable"

    assert refreshed_enrichment is not None
    assert refreshed_enrichment.status == AI_STATUS_ERROR
    assert refreshed_enrichment.error == "Task exceeded the fallback stale-run grace period while Celery inspection was unavailable"


def test_list_ai_task_runs_reconciles_partial_skip_parents_consistently(db_session, monkeypatch):
    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 1},
        target_count=1,
    )
    parent_run.status = AI_STATUS_RUNNING
    parent_run.processed_count = 1
    parent_run.skipped_count = 1

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    parent_run.queued_at = stale_time
    parent_run.started_at = stale_time
    parent_run.created_at = stale_time
    parent_run.updated_at = stale_time
    db_session.add(parent_run)
    db_session.commit()

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    response = list_ai_task_runs(db_session, task_type=AI_TASK_TYPE_REPROCESS, limit=10)

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))

    assert refreshed_parent is not None
    assert refreshed_parent.status == AI_STATUS_SKIPPED
    assert refreshed_parent.reason == "partial_skips"
    assert refreshed_parent.finished_at is not None

    assert response.items[0].status == AI_STATUS_SKIPPED
    assert response.items[0].reason == "partial_skips"


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
