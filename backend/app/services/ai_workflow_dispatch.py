"""Transactional publication and execution state for accepted AI work."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch

WORKFLOW_BATCH_SIZE = 50
WORKFLOW_CLAIM_SECONDS = 60
WORKFLOW_REPUBLISH_SECONDS = 3600
WORKFLOW_MAX_OUTSTANDING_PUBLICATIONS = 100
TERMINAL = {"ready", "error", "skipped"}
TASK_PREFIX = "app.tasks.feed_tasks."


class AIWorkflowDeferred(RuntimeError):
    def __init__(self, reason: str, retry_after_seconds: float):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def register_ai_workflow(db: Session, run: AITaskRun) -> AIWorkflowDispatch | None:
    existing = db.get(AIWorkflowDispatch, run.id)
    if existing is not None:
        return existing
    metadata = run.metadata_json or {}
    payload = {"task_run_id": str(run.id)}
    if run.task_type == "item_enrichment" and run.item_id is not None:
        task_name = "generate_item_ai_enrichment"
        payload.update(item_id=str(run.item_id), force=bool(metadata.get("force")))
    elif run.task_type == "daily_brief" and run.parent_run_id is None:
        task_name = "dispatch_daily_ai_brief_generation"
        payload["force"] = bool(metadata.get("force"))
    elif run.task_type == "reprocess":
        if metadata.get("scope") == "daily_brief_backfill":
            task_name = "backfill_daily_ai_briefs"
            payload["days"] = int(metadata.get("days") or 0)
        else:
            from app.services.ai_reprocess import freeze_reprocess_selection
            freeze_reprocess_selection(db, run)
            task_name = "reprocess_recent_ai_items"
            payload.update({name: metadata.get(name) for name in (
                "days", "start_time", "end_time", "feed_ids", "item_ids"
            )})
            payload["limit"] = int(metadata.get("effective_limit") or metadata.get("limit") or 100)
    else:
        return None
    if run.task_type != "item_enrichment":
        payload["actor_user_id"] = str(run.actor_user_id) if run.actor_user_id else None
    dispatch = AIWorkflowDispatch(
        run_id=run.id, task_name=TASK_PREFIX + task_name, payload_json=payload,
        state="complete" if run.status in TERMINAL else "running" if run.status == "running" else "pending",
        delivery_id=run.celery_task_id,
        next_attempt_at=datetime.now(timezone.utc), attempt_count=0,
    )
    db.add(dispatch)
    db.flush()
    return dispatch


def claim_workflow_execution(db: Session, run: AITaskRun, celery_task_id: str | None) -> bool:
    dispatch = db.scalar(select(AIWorkflowDispatch).where(
        AIWorkflowDispatch.run_id == run.id
    ).with_for_update().execution_options(populate_existing=True))
    if dispatch is None:
        return True
    if dispatch.state in {"running", "complete"}:
        return False
    if dispatch.delivery_id is not None and dispatch.delivery_id != celery_task_id:
        return False
    dispatch.state = "running"
    dispatch.delivery_id = celery_task_id
    dispatch.claim_token = None
    dispatch.claim_expires_at = None
    dispatch.error = None
    db.add(dispatch)
    return True


def complete_workflow_dispatch(db: Session, run_id: uuid.UUID) -> None:
    dispatch = db.get(AIWorkflowDispatch, run_id)
    if dispatch is not None:
        dispatch.state = "complete"
        dispatch.claim_token = None
        dispatch.claim_expires_at = None
        db.add(dispatch)


def defer_ai_workflow_run(
    db: Session, *, run_id: uuid.UUID, reason: str, retry_after_seconds: float
) -> bool:
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run is None or run.status in TERMINAL or run.finished_at is not None:
        return False
    if (run.metadata_json or {}).get("cancel_requested_at"):
        return False
    dispatch = db.get(AIWorkflowDispatch, run_id)
    if dispatch is None:
        dispatch = register_ai_workflow(db, run)
    if dispatch is None:
        return False
    run.status = "queued"
    run.reason = None
    run.worker_name = None
    run.metadata_json = {**dict(run.metadata_json or {}), "deferred_reason": reason}
    dispatch.state = "pending"
    dispatch.claim_token = None
    dispatch.claim_expires_at = None
    dispatch.next_attempt_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(1, min(3600, retry_after_seconds))
    )
    dispatch.error = reason[:128]
    db.add(run)
    db.add(dispatch)
    return True
