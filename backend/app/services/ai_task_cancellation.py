"""Durable cancellation with child-before-parent transitions and bounded work."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.ai_task_run import AITaskRun
from app.services.data_access_policy import DataAccessContext, fence_data_access_context
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation

CANCELLATION_BATCH_SIZE = 50
_UNFINISHED = {"queued", "running"}
AuthorizationCheckpoint = Callable[[Session], None]


def _fence(
    db: Session,
    data_access: DataAccessContext | None,
    checkpoint: AuthorizationCheckpoint | None,
) -> None:
    if checkpoint is not None:
        checkpoint(db)
    if data_access is not None:
        fence_data_access_context(db, data_access)
    else:
        lock_data_policy_revision_for_derivation(db)


def _access(data_access: DataAccessContext | None):
    from app.services.ai_telemetry_data_policy import ai_task_run_access_predicate

    return True if data_access is None else ai_task_run_access_predicate(data_access)


def cancel_ai_task(
    db: Session,
    *,
    run_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_access: DataAccessContext | None = None,
    authorization_checkpoint: AuthorizationCheckpoint | None = None,
) -> AITaskRun | None:
    """Accept an authorized cancellation, then advance at most one child batch.

    Authorization intent commits before child locks. Every subsequent HTTP batch
    re-fences IAM/data policy; maintenance can finish the already accepted intent.
    Provider receipts are retained unchanged, including ambiguous attempts.
    """
    from app.services import ai_ops
    from app.services.report_task_lineage import resolve_report_task_run

    _fence(db, data_access, authorization_checkpoint)
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id, _access(data_access))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    if run.task_type == "report":
        run = resolve_report_task_run(db, run, lock=True)
        if (
            db.scalar(
                select(AITaskRun.id).where(AITaskRun.id == run.id, _access(data_access))
            )
            is None
        ):
            return None
    if run.finished_at is not None or run.status not in _UNFINISHED:
        return run
    if run.task_type == "reprocess" and data_access is not None:
        denied = db.scalar(
            select(AITaskRun.id)
            .where(
                AITaskRun.parent_run_id == run.id,
                AITaskRun.finished_at.is_(None),
                AITaskRun.status.in_(_UNFINISHED),
                ~_access(data_access),
            )
            .limit(1)
        )
        if denied is not None:
            return None
    run_id = run.id
    is_parent = run.task_type == "reprocess"
    ai_ops._mark_ai_task_run_cancel_requested(
        db,
        run_id=run_id,
        actor_user_id=actor_user_id,
        removed_from_queue=False,
        terminated_running_task=False,
        revoke_failed=False,
    )
    db.commit()

    _fence(db, data_access, authorization_checkpoint)
    _available, _workers, active, _reserved, _scheduled = (
        ai_ops._normalize_live_task_snapshot(ai_ops._load_live_task_snapshot())
    )
    active_ids = {task.celery_task_id for task in active if task.celery_task_id}
    db.commit()
    if is_parent:
        for _ in range(CANCELLATION_BATCH_SIZE):
            _fence(db, data_access, authorization_checkpoint)
            child = db.scalar(
                select(AITaskRun)
                .where(
                    AITaskRun.parent_run_id == run_id,
                    AITaskRun.finished_at.is_(None),
                    AITaskRun.status.in_(_UNFINISHED),
                    _access(data_access),
                    AITaskRun.metadata_json["cancel_requested_at"]
                    .as_string()
                    .is_(None),
                )
                .order_by(AITaskRun.id)
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if child is None:
                db.commit()
                break
            _cancel_locked(
                db,
                child,
                actor_user_id=actor_user_id,
                active_ids=active_ids,
                revoke=True,
            )
            db.commit()  # Release both the child and its progress-parent lock.
    _fence(db, data_access, authorization_checkpoint)
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id, _access(data_access))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is not None:
        _cancel_locked(
            db, run, actor_user_id=actor_user_id, active_ids=active_ids, revoke=True
        )
    db.flush()
    return run


def _cancel_locked(
    db: Session,
    run: AITaskRun,
    *,
    actor_user_id: uuid.UUID | None,
    active_ids: set[str],
    revoke: bool,
) -> None:
    from app.services import ai_ops

    if run.finished_at is not None or run.status not in _UNFINISHED:
        return
    terminate = bool(run.celery_task_id and run.celery_task_id in active_ids)
    revoke_failed = False
    if revoke and run.celery_task_id:
        try:
            ai_ops.celery_app.control.revoke(
                run.celery_task_id, terminate=terminate, signal="SIGTERM"
            )
        except Exception:
            revoke_failed = True
            ai_ops.record_ai_task_event(
                db,
                run_id=run.id,
                event_type="cancel_revoke_failed",
                payload={"celery_task_id": run.celery_task_id},
            )
    # Queued state is fenced by the task row: a worker must claim running before
    # reserving provider I/O. Queue inspection is neither necessary nor proof of
    # absence of a prior paid attempt, whose receipt must remain intact.
    queued = run.status == "queued"
    ai_ops._mark_ai_task_run_cancel_requested(
        db,
        run_id=run.id,
        actor_user_id=actor_user_id,
        removed_from_queue=queued,
        terminated_running_task=terminate,
        revoke_failed=revoke_failed,
    )
    if queued:
        ai_ops.finish_ai_task_run(
            db,
            run_id=run.id,
            status="skipped",
            reason="canceled",
            metadata_updates={
                "cancel_observed_at": datetime.now(timezone.utc).isoformat(),
                "cancel_completed_without_worker": True,
            },
        )


def reconcile_canceled_ai_tasks(
    db: Session, *, limit: int = CANCELLATION_BATCH_SIZE
) -> int:
    """Advance accepted cancellation even when broker inspection cannot recover."""
    parent = aliased(AITaskRun)
    inherited = exists(
        select(parent.id).where(
            parent.id == AITaskRun.parent_run_id,
            parent.metadata_json["cancel_requested_at"].as_string().is_not(None),
        )
    )
    requested = AITaskRun.metadata_json["cancel_requested_at"].as_string().is_not(None)
    ids = list(
        db.scalars(
            select(AITaskRun.id)
            .where(
                AITaskRun.finished_at.is_(None),
                AITaskRun.status.in_(_UNFINISHED),
                or_((AITaskRun.status == "queued") & requested, inherited & ~requested),
            )
            .order_by(AITaskRun.updated_at, AITaskRun.id)
            .limit(max(1, min(limit, CANCELLATION_BATCH_SIZE)))
        )
    )
    changed = 0
    for run_id in ids:
        lock_data_policy_revision_for_derivation(db)
        run = db.scalar(
            select(AITaskRun)
            .where(AITaskRun.id == run_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if run is not None:
            _cancel_locked(db, run, actor_user_id=None, active_ids=set(), revoke=False)
            changed += 1
        db.commit()
    return changed
