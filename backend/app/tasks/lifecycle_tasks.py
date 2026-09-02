from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.services.lifecycle_execution import (
    dispatch_due_lifecycle_runs,
    execute_lifecycle_run,
    fail_lifecycle_run_after_retries,
    mark_lifecycle_run_published,
    release_lifecycle_run_publication,
    run_lifecycle_housekeeping,
)
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session


logger = logging.getLogger("threatlens.lifecycle")


def enqueue_lifecycle_run(db: Session, *, run_id: uuid.UUID) -> str:
    proposed_task_id = f"{run_id}:{uuid.uuid4().hex}"
    task_id, newly_reserved = mark_lifecycle_run_published(
        db,
        run_id=run_id,
        celery_task_id=proposed_task_id,
    )
    if task_id is None:
        raise RuntimeError("Lifecycle run is no longer queued for publication.")
    try:
        execute_lifecycle_run_task.apply_async(args=[str(run_id)], task_id=task_id)
    except Exception:
        if newly_reserved:
            release_lifecycle_run_publication(
                db,
                run_id=run_id,
                celery_task_id=task_id,
            )
        raise
    return task_id


@celery_app.task(
    bind=True,
    name="app.tasks.lifecycle_tasks.execute_lifecycle_run",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=5,
)
def execute_lifecycle_run_task(self, run_id: str):
    parsed_id = _parse_uuid(run_id)
    if parsed_id is None:
        return {"status": "invalid_run_id"}
    try:
        with db_session() as db:
            result = execute_lifecycle_run(
                db,
                run_id=parsed_id,
                expected_task_id=str(self.request.id),
            )
    except OperationalError as exc:
        if int(self.request.retries) >= int(self.max_retries or 0):
            with db_session() as db:
                fail_lifecycle_run_after_retries(
                    db,
                    run_id=parsed_id,
                    expected_task_id=str(self.request.id),
                )
            return {"status": "failed", "run_id": str(parsed_id)}
        countdown = min(300, 2 ** max(1, int(self.request.retries) + 1))
        raise self.retry(exc=exc, countdown=countdown) from exc
    if result.get("status") == "queued":
        with db_session() as db:
            try:
                enqueue_lifecycle_run(db, run_id=parsed_id)
            except Exception:
                db.rollback()
                logger.warning(
                    "lifecycle_continuation_publish_failed run_id=%s",
                    parsed_id,
                    exc_info=True,
                )
    return result


@celery_app.task(
    name="app.tasks.lifecycle_tasks.dispatch_due_lifecycle_runs",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_due_lifecycle_runs_task():
    with db_session() as db:
        run_ids = dispatch_due_lifecycle_runs(db)
    published = 0
    failed = 0
    for run_id in run_ids:
        with db_session() as db:
            try:
                enqueue_lifecycle_run(db, run_id=run_id)
                published += 1
            except Exception:
                db.rollback()
                failed += 1
                logger.warning(
                    "lifecycle_dispatch_publish_failed run_id=%s",
                    run_id,
                    exc_info=True,
                )
    return {
        "status": "ok",
        "queued": len(run_ids),
        "published": published,
        "publish_failed": failed,
    }


@celery_app.task(
    name="app.tasks.lifecycle_tasks.run_lifecycle_housekeeping",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_lifecycle_housekeeping_task():
    with db_session() as db:
        result = run_lifecycle_housekeeping(db)
    return {"status": "ok", **result}


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "dispatch_due_lifecycle_runs_task",
    "enqueue_lifecycle_run",
    "execute_lifecycle_run_task",
    "run_lifecycle_housekeeping_task",
]
