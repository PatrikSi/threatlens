from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.services.ai_execution_ownership import ai_worker_execution
from app.services.ai_config import load_active_ai_settings
from app.services.ai_workflow_dispatch import AIWorkflowDeferred, defer_ai_workflow_run
from app.services.ai_integration import is_stale_daily_brief_pending, run_daily_brief_generation
from app.services.ai_ops import (
    AI_DAILY_BRIEF_BACKFILL_SCOPE,
    AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY,
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_READY,
    AI_STATUS_RUNNING,
    AI_STATUS_SKIPPED,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
    AI_TRIGGER_SCHEDULED,
    _reconcile_stale_ai_runs,
    ai_task_run_stop_reason,
    finish_ai_task_run,
    queue_ai_task_run,
    reconcile_daily_brief_backfill_parent_progress,
    record_ai_task_event,
    start_ai_task_run,
)
from app.tasks.celery_app import celery_app
from app.tasks.feed_task_coordination import CoordinationUnavailableError, daily_ai_brief_lock
from app.tasks.integration_tasks import enqueue_integration_event_routing
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)
DAILY_BRIEF_STALE_RETRY_WINDOW = timedelta(minutes=15)
DAILY_BRIEF_BACKFILL_REFERENCE_TIME_KEY = "backfill_reference_time"


def _exception_type_name(exc: BaseException) -> str:
    return exc.__class__.__name__


def _task_run_claimed_by_current_worker(run: AITaskRun | None, *, celery_task_id: str | None) -> bool:
    if run is None or run.finished_at is not None or run.status != AI_STATUS_RUNNING:
        return False
    if celery_task_id is None:
        return True
    return run.celery_task_id == celery_task_id


def _scheduled_daily_ai_brief_due(db: Session, *, now: datetime) -> tuple[bool, str | None]:
    active = load_active_ai_settings(db, feature_type="daily_brief")
    if not active.ai_enabled:
        return False, "ai_disabled"
    if not active.ai_configured:
        return False, getattr(active, "configuration_error_code", None) or "ai_not_configured"
    if not active.daily_brief_enabled:
        return False, "daily_brief_disabled"

    scheduled_at = now.replace(
        hour=active.daily_brief_schedule_hour_utc,
        minute=active.daily_brief_schedule_minute_utc,
        second=0,
        microsecond=0,
    )
    if now < scheduled_at:
        return False, "scheduled_time_not_reached"

    existing = db.scalar(select(AIDailyBrief).where(AIDailyBrief.brief_date == now.date()))
    if existing is not None:
        if existing.status == "ready":
            return False, "already_generated"
        if existing.status == "pending" and not is_stale_daily_brief_pending(existing, now=now):
            return False, "already_running"

    in_flight_run = db.scalar(
        select(AITaskRun.id)
        .where(
            AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            AITaskRun.queued_at >= scheduled_at,
            AITaskRun.queued_at < scheduled_at + timedelta(days=1),
        )
        .order_by(AITaskRun.queued_at.desc())
        .limit(1)
    )
    if in_flight_run is not None:
        task_run = db.scalar(select(AITaskRun).where(AITaskRun.id == in_flight_run))
        if task_run is not None and task_run.finished_at is None:
            return False, "already_running"

    return True, None


def _is_stale_daily_brief_task_run(run: AITaskRun, *, now: datetime) -> bool:
    reference = run.updated_at or run.started_at or run.queued_at or run.created_at
    if reference is None:
        return True
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return now - reference >= DAILY_BRIEF_STALE_RETRY_WINDOW


@celery_app.task(
    name="app.tasks.feed_tasks.reconcile_ai_task_runs",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_ai_task_runs():
    with db_session() as db:
        reconciled = _reconcile_stale_ai_runs(db)
        return {"status": "ok", "reconciled": reconciled}


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.dispatch_daily_ai_brief_generation",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
@ai_worker_execution
def dispatch_daily_ai_brief_generation(
    self,
    force: bool = False,
    task_run_id: str | None = None,
    actor_user_id: str | None = None,
):
    with db_session() as db:
        parsed_run_id = None
        parsed_actor_user_id = None
        if task_run_id:
            try:
                parsed_run_id = uuid.UUID(task_run_id)
            except ValueError:
                parsed_run_id = None
        if actor_user_id:
            try:
                parsed_actor_user_id = uuid.UUID(actor_user_id)
            except ValueError:
                parsed_actor_user_id = None
        is_scheduled_dispatch = parsed_run_id is None and parsed_actor_user_id is None
        if is_scheduled_dispatch and not force:
            due, reason = _scheduled_daily_ai_brief_due(db, now=datetime.now(timezone.utc))
            if not due:
                return {"status": "skipped", "reason": reason}
        run: AITaskRun | None = None
        try:
            with daily_ai_brief_lock() as acquired:
                if not acquired:
                    if parsed_run_id is not None:
                        run = db.scalar(
                            select(AITaskRun)
                            .where(AITaskRun.id == parsed_run_id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                        # The lease owner may not have started its run yet.
                        # A queued status does not prove this is unrelated work;
                        # defer this delivery without terminalizing its owner.
                        if (
                            run is not None
                            and run.status == AI_STATUS_QUEUED
                            and ai_task_run_stop_reason(run) is None
                            and run.celery_task_id in (
                                None, getattr(self.request, "id", None)
                            )
                        ):
                            defer_ai_workflow_run(db, run_id=run.id, reason="brief_lock_busy", retry_after_seconds=30)
                            db.commit()
                            return {"status": "queued", "reason": "brief_lock_busy", "run_id": str(run.id)}
                    result = {"status": "skipped", "reason": "already_running"}
                    if parsed_run_id is not None:
                        result["run_id"] = str(parsed_run_id)
                    return result
                if parsed_run_id:
                    run = db.scalar(select(AITaskRun).where(AITaskRun.id == parsed_run_id))
                    if run is None:
                        return {"status": "skipped", "reason": "task_history_unavailable", "run_id": task_run_id}
                else:
                    run = queue_ai_task_run(
                        db,
                        task_type=AI_TASK_TYPE_DAILY_BRIEF,
                        trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                        actor_user_id=parsed_actor_user_id,
                        model=None,
                        metadata={"force": bool(force), "scheduled": parsed_actor_user_id is None},
                    )
                started_run = start_ai_task_run(
                    db,
                    run_id=run.id,
                    worker_name=getattr(self.request, "hostname", None),
                    celery_task_id=getattr(self.request, "id", None),
                    metadata_updates={"force": bool(force)},
                )
                db.commit()
                if not _task_run_claimed_by_current_worker(started_run, celery_task_id=getattr(self.request, "id", None)):
                    return {"status": "skipped", "reason": "already_running", "run_id": task_run_id}
                stop_reason = ai_task_run_stop_reason(started_run)
                if stop_reason is not None:
                    if stop_reason == "canceled":
                        finish_ai_task_run(
                            db,
                            run_id=run.id,
                            status=AI_STATUS_SKIPPED,
                            reason="canceled",
                            worker_name=getattr(self.request, "hostname", None),
                            metadata_updates={"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
                        )
                        db.commit()
                    return {"status": "skipped", "reason": stop_reason}
                active_ai_settings = load_active_ai_settings(db, feature_type="daily_brief", task_run_id=run.id)
                if not active_ai_settings.ai_enabled:
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=AI_STATUS_SKIPPED,
                        reason="ai_disabled",
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "ai_disabled"}
                if not active_ai_settings.ai_configured:
                    reason = getattr(active_ai_settings, "configuration_error_code", None) or "ai_not_configured"
                    status = AI_STATUS_ERROR if reason != "ai_not_configured" else AI_STATUS_SKIPPED
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=status,
                        reason=reason,
                        error=(getattr(active_ai_settings, "configuration_error", None) or reason) if status == AI_STATUS_ERROR else None,
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": status, "reason": reason}
                if not active_ai_settings.daily_brief_enabled:
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=AI_STATUS_SKIPPED,
                        reason="daily_brief_disabled",
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "daily_brief_disabled"}

                result = run_daily_brief_generation(
                    db, force=force, task_run_id=run.id, reference_time=run.created_at
                )
                finish_ai_task_run(
                    db,
                    run_id=run.id,
                    status=AI_STATUS_READY if result.status == "ready" else AI_STATUS_ERROR if result.status == "error" else AI_STATUS_SKIPPED,
                    reason=result.reason,
                    error=(result.brief.error if result.brief is not None else getattr(result, "error", None)) if result.status == "error" else None,
                    worker_name=getattr(self.request, "hostname", None),
                    model=result.brief.model if result.brief is not None else active_ai_settings.model,
                    prompt_tokens=result.brief.prompt_tokens if result.brief is not None else None,
                    completion_tokens=result.brief.completion_tokens if result.brief is not None else None,
                    total_tokens=result.brief.total_tokens if result.brief is not None else None,
                    latency_ms=result.brief.latency_ms if result.brief is not None else None,
                    prompt_char_count=result.prompt_char_count,
                    response_char_count=result.response_char_count,
                    metadata_updates={"items_considered": result.items_considered, "items_selected": result.items_selected},
                    daily_brief_id=result.brief.id if result.brief is not None else None,
                )
                db.commit()
                notification_enqueue_ok = (
                    enqueue_integration_event_routing([result.integration_event_id])
                    if result.integration_event_id is not None
                    else True
                )
                if result.brief is None:
                    return {"status": result.status, "reason": result.reason}
                return {
                    "status": result.status,
                    "reason": result.reason,
                    "brief_date": result.brief.brief_date.isoformat(),
                    "integration_event_id": (
                        str(result.integration_event_id) if result.integration_event_id is not None else None
                    ),
                    "notification_enqueue_failed": not notification_enqueue_ok,
                }
        except AIWorkflowDeferred as exc:
            if run is not None:
                defer_ai_workflow_run(db, run_id=run.id, reason=exc.reason, retry_after_seconds=exc.retry_after_seconds)
                db.commit()
            return {"status": "queued", "reason": exc.reason}
        except CoordinationUnavailableError as exc:
            logger.warning("daily_brief_coordination_unavailable error_type=%s", _exception_type_name(exc))
            deferred_id = run.id if run is not None else parsed_run_id
            if deferred_id is not None:
                defer_ai_workflow_run(db, run_id=deferred_id, reason="coordination_unavailable", retry_after_seconds=30)
                db.commit()
                return {"status": "queued", "reason": "coordination_unavailable"}
            return {"status": "error", "reason": "coordination_unavailable"}


def _daily_brief_backfill_reference_times(days: int, *, now: datetime | None = None) -> list[datetime]:
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)

    references: list[datetime] = []
    for offset in range(max(0, int(days))):
        target_date = reference_now.date() - timedelta(days=offset)
        if offset == 0:
            references.append(reference_now)
        else:
            references.append(
                datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    23,
                    59,
                    59,
                    tzinfo=timezone.utc,
                )
            )
    return references


def _daily_brief_backfill_anchor(db: Session, run: AITaskRun) -> datetime:
    from app.services.ai_execution_ownership import AIExecutionSuperseded
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run.id).with_for_update()
                    .execution_options(populate_existing=True))
    stop_reason = "task_history_unavailable" if run is None else ai_task_run_stop_reason(run)
    if stop_reason is not None:
        raise AIExecutionSuperseded("Brief anchor execution was stopped or superseded.", reason=stop_reason)
    metadata = dict(run.metadata_json or {})

    def parse_reference(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    anchor = parse_reference(metadata.get(DAILY_BRIEF_BACKFILL_REFERENCE_TIME_KEY))
    if anchor is None:
        # Older workers stored the exact reference only on each child. The
        # first attempted day preserves the original backfill's newest date,
        # even if its parent waited in the queue across midnight.
        children = db.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.parent_run_id == run.id,
                AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            )
            .order_by(AITaskRun.created_at.asc(), AITaskRun.id.asc())
        )
        for child in children:
            anchor = parse_reference((child.metadata_json or {}).get("reference_time"))
            if anchor is not None:
                break
    if anchor is None:
        anchor = run.created_at or run.queued_at or datetime.now(timezone.utc)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        anchor = anchor.astimezone(timezone.utc)
    run.metadata_json = {
        **metadata,
        DAILY_BRIEF_BACKFILL_REFERENCE_TIME_KEY: anchor.isoformat(),
    }
    db.add(run)
    return anchor


def _daily_brief_backfill_attempts(
    db: Session,
    *,
    parent_run_id: uuid.UUID,
    brief_date: str,
) -> list[AITaskRun]:
    child_runs = list(
        db.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.parent_run_id == parent_run_id,
                AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            )
            .order_by(AITaskRun.created_at.asc(), AITaskRun.id.asc())
        )
    )
    return [run for run in child_runs if str((run.metadata_json or {}).get("brief_date") or "") == brief_date]


def _daily_brief_backfill_attempt_is_settled(run: AITaskRun) -> bool:
    from app.services.ai_brief_recovery import brief_attempt_is_settled
    return brief_attempt_is_settled(run)


def _daily_brief_backfill_attempt_number(attempts: list[AITaskRun]) -> int:
    attempt_numbers: list[int] = []
    for attempt in attempts:
        try:
            attempt_numbers.append(int((attempt.metadata_json or {}).get("attempt") or 0))
        except (TypeError, ValueError):
            continue
    return max([len(attempts), *attempt_numbers], default=0) + 1


def _supersede_daily_brief_attempts(
    db: Session, *, attempts: list[AITaskRun], attempt_number: int,
    worker_name: str | None, active_model: str | None,
) -> None:
    for attempt in attempts:
        attempt = db.scalar(select(AITaskRun).where(AITaskRun.id == attempt.id).with_for_update()
                            .execution_options(populate_existing=True))
        if attempt is None or attempt.finished_at is not None:
            continue
        from app.services.ai_execution_ownership import AIExecutionSuperseded, ai_execution_stop_reason
        stop_reason = ai_execution_stop_reason(db, attempt, lock_parent=True)
        if stop_reason is not None:
            raise AIExecutionSuperseded("Brief supersession was stopped or superseded.", reason=stop_reason)
        attempt.metadata_json = {
            **dict(attempt.metadata_json or {}),
            AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: False,
            "superseded_by_attempt": attempt_number,
        }
        db.add(attempt)
        finish_ai_task_run(
            db, run_id=attempt.id, status=AI_STATUS_SKIPPED,
            reason="superseded_by_redelivery",
            worker_name=attempt.worker_name or worker_name,
            model=attempt.model or active_model,
        )
        db.commit()  # Release child and parent before handling another attempt.


def _require_backfill_history(run: AITaskRun | None, requested_id: uuid.UUID | None) -> None:
    if requested_id is not None and run is None:
        from app.services.ai_execution_ownership import AIExecutionSuperseded
        raise AIExecutionSuperseded("Accepted AI task history is unavailable.", reason="task_history_unavailable")


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.backfill_daily_ai_briefs",
    acks_late=True,
    reject_on_worker_lost=True,
)
@ai_worker_execution
def backfill_daily_ai_briefs(
    self,
    days: int,
    task_run_id: str | None = None,
    actor_user_id: str | None = None,
):
    worker_name = getattr(self.request, "hostname", None)
    celery_task_id = getattr(self.request, "id", None)
    try:
        effective_days = int(days)
    except (TypeError, ValueError):
        effective_days = 0

    with db_session() as db:
        parsed_run_id = None
        parsed_actor_user_id = None
        if task_run_id:
            try:
                parsed_run_id = uuid.UUID(task_run_id)
            except ValueError:
                parsed_run_id = None
        if actor_user_id:
            try:
                parsed_actor_user_id = uuid.UUID(actor_user_id)
            except ValueError:
                parsed_actor_user_id = None

        run = db.scalar(select(AITaskRun).where(AITaskRun.id == parsed_run_id)) if parsed_run_id else None
        _require_backfill_history(run, parsed_run_id)
        if run is None:
            run = queue_ai_task_run(
                db,
                task_type=AI_TASK_TYPE_REPROCESS,
                trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                actor_user_id=parsed_actor_user_id,
                metadata={"scope": AI_DAILY_BRIEF_BACKFILL_SCOPE, "days": max(0, effective_days), "force": True},
                target_count=max(0, effective_days),
            )

        from app.services.ai_execution_ownership import require_ai_execution
        run = db.scalar(select(AITaskRun).where(AITaskRun.id == run.id).with_for_update()
                        .execution_options(populate_existing=True))
        require_ai_execution(run, allow_unassigned=True)
        parent_run_id = run.id
        run.target_count = max(0, effective_days)
        run.metadata_json = {
            **dict(run.metadata_json or {}),
            "scope": AI_DAILY_BRIEF_BACKFILL_SCOPE,
            "days": max(0, effective_days),
            "force": True,
            "includes_today": True,
        }
        db.add(run)
        run = reconcile_daily_brief_backfill_parent_progress(
            db,
            parent_run_id=parent_run_id,
            reopen_incomplete=True,
        ) or run
        started_run = start_ai_task_run(
            db,
            run_id=parent_run_id,
            worker_name=worker_name,
            celery_task_id=celery_task_id,
            metadata_updates={"scope": AI_DAILY_BRIEF_BACKFILL_SCOPE, "days": max(0, effective_days), "force": True},
        )
        db.commit()

        if not _task_run_claimed_by_current_worker(started_run, celery_task_id=celery_task_id):
            return {"status": "skipped", "reason": "already_running", "run_id": str(parent_run_id)}

        if effective_days < 1:
            finish_ai_task_run(
                db,
                run_id=parent_run_id,
                status=AI_STATUS_SKIPPED,
                reason="invalid_days",
                worker_name=worker_name,
            )
            db.commit()
            return {"status": "skipped", "reason": "invalid_days", "run_id": str(parent_run_id)}

        stop_reason = ai_task_run_stop_reason(started_run)
        if stop_reason is not None:
            if stop_reason == "canceled":
                finish_ai_task_run(
                    db,
                    run_id=parent_run_id,
                    status=AI_STATUS_SKIPPED,
                    reason="canceled",
                    worker_name=worker_name,
                    metadata_updates={"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
                )
                db.commit()
            return {"status": "skipped", "reason": stop_reason, "run_id": str(parent_run_id)}

        active_ai_settings = load_active_ai_settings(db, feature_type="daily_brief", task_run_id=parent_run_id)
        if not active_ai_settings.ai_enabled:
            finish_ai_task_run(db, run_id=parent_run_id, status=AI_STATUS_SKIPPED, reason="ai_disabled", worker_name=worker_name)
            db.commit()
            return {"status": "skipped", "reason": "ai_disabled", "run_id": str(parent_run_id)}
        if not active_ai_settings.ai_configured:
            reason = getattr(active_ai_settings, "configuration_error_code", None) or "ai_not_configured"
            status = AI_STATUS_ERROR if reason != "ai_not_configured" else AI_STATUS_SKIPPED
            finish_ai_task_run(
                db,
                run_id=parent_run_id,
                status=status,
                reason=reason,
                error=(getattr(active_ai_settings, "configuration_error", None) or reason) if status == AI_STATUS_ERROR else None,
                worker_name=worker_name,
            )
            db.commit()
            return {"status": status, "reason": reason, "run_id": str(parent_run_id)}
        if not active_ai_settings.daily_brief_enabled:
            finish_ai_task_run(db, run_id=parent_run_id, status=AI_STATUS_SKIPPED, reason="daily_brief_disabled", worker_name=worker_name)
            db.commit()
            return {"status": "skipped", "reason": "daily_brief_disabled", "run_id": str(parent_run_id)}
        if effective_days > int(active_ai_settings.daily_brief_history_limit or 0):
            finish_ai_task_run(
                db,
                run_id=parent_run_id,
                status=AI_STATUS_ERROR,
                reason="history_limit_too_low",
                error=f"Retained daily briefings is {active_ai_settings.daily_brief_history_limit}, below requested backfill days {effective_days}",
                worker_name=worker_name,
            )
            db.commit()
            return {"status": "error", "reason": "history_limit_too_low", "run_id": str(parent_run_id)}

        active_model = active_ai_settings.model
        backfill_anchor = _daily_brief_backfill_anchor(db, run)
        run.model = active_model
        db.add(run)
        record_ai_task_event(
            db,
            run_id=parent_run_id,
            event_type="backfill_started",
            payload={"days": effective_days, "includes_today": True},
        )
        db.commit()

        try:
            with daily_ai_brief_lock() as acquired:
                if not acquired:
                    defer_ai_workflow_run(db, run_id=parent_run_id, reason="brief_lock_busy", retry_after_seconds=30)
                    db.commit()
                    return {"status": "queued", "reason": "brief_lock_busy", "run_id": str(parent_run_id)}

                processed_dates: list[str] = []
                for reference_time in _daily_brief_backfill_reference_times(
                    effective_days, now=backfill_anchor
                ):
                    brief_date = reference_time.date().isoformat()
                    parent_run = db.scalar(select(AITaskRun).where(AITaskRun.id == parent_run_id)
                                           .execution_options(populate_existing=True))
                    if parent_run is None:
                        return {
                            "status": "error",
                            "reason": "parent_run_missing",
                            "run_id": str(parent_run_id),
                            "processed_dates": processed_dates,
                        }

                    parent_stop_reason = ai_task_run_stop_reason(parent_run)
                    if parent_stop_reason is not None:
                        if parent_stop_reason == "canceled" and parent_run.finished_at is None:
                            finish_ai_task_run(
                                db,
                                run_id=parent_run_id,
                                status=AI_STATUS_SKIPPED,
                                reason="canceled",
                                worker_name=worker_name,
                                metadata_updates={"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
                            )
                            db.commit()
                        return {
                            "status": "skipped",
                            "reason": parent_stop_reason,
                            "run_id": str(parent_run_id),
                            "processed_dates": processed_dates,
                        }

                    attempts = _daily_brief_backfill_attempts(
                        db,
                        parent_run_id=parent_run_id,
                        brief_date=brief_date,
                    )
                    from app.services.ai_brief_recovery import reconcile_interrupted_brief_attempts
                    recovery = reconcile_interrupted_brief_attempts(db, parent=parent_run, attempts=attempts)
                    if recovery is not None:
                        db.commit()
                        if recovery == "ready":
                            processed_dates.append(brief_date)
                            continue
                        return {"status": "error", "reason": "provider_recovery_blocked", "run_id": str(parent_run_id)}

                    attempt_number = _daily_brief_backfill_attempt_number(attempts)
                    _supersede_daily_brief_attempts(
                        db, attempts=attempts, attempt_number=attempt_number,
                        worker_name=worker_name, active_model=active_model,
                    )

                    child_run = queue_ai_task_run(
                        db,
                        task_type=AI_TASK_TYPE_DAILY_BRIEF,
                        trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                        actor_user_id=parsed_actor_user_id,
                        parent_run_id=parent_run_id,
                        model=active_model,
                        metadata={
                            "scope": AI_DAILY_BRIEF_BACKFILL_SCOPE,
                            "force": True,
                            "brief_date": brief_date,
                            "reference_time": reference_time.isoformat(),
                            "attempt": attempt_number,
                            AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: False,
                        },
                    )
                    start_ai_task_run(
                        db,
                        run_id=child_run.id,
                        worker_name=worker_name,
                        celery_task_id=celery_task_id,
                        metadata_updates={"scope": AI_DAILY_BRIEF_BACKFILL_SCOPE, "force": True},
                    )
                    db.commit()
                    child_run_id = child_run.id

                    try:
                        result = run_daily_brief_generation(
                            db,
                            force=True,
                            reference_time=reference_time,
                            task_run_id=child_run_id,
                            emit_notification=False,
                        )
                    except AIWorkflowDeferred as exc:
                        defer_ai_workflow_run(db, run_id=parent_run_id, reason=exc.reason, retry_after_seconds=exc.retry_after_seconds)
                        db.commit()
                        return {"status": "queued", "reason": exc.reason, "run_id": str(parent_run_id), "processed_dates": processed_dates}
                    except Exception as exc:
                        db.rollback()
                        logger.exception("daily_brief_backfill_day_failed brief_date=%s", reference_time.date().isoformat())
                        finish_ai_task_run(
                            db,
                            run_id=child_run_id,
                            status=AI_STATUS_ERROR,
                            reason="unexpected_error",
                            error=str(exc) or _exception_type_name(exc),
                            worker_name=worker_name,
                            model=active_model,
                            metadata_updates={
                                "brief_date": brief_date,
                                AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: True,
                            },
                        )
                        db.commit()
                        processed_dates.append(brief_date)
                        continue

                    finish_ai_task_run(
                        db,
                        run_id=child_run_id,
                        status=AI_STATUS_READY if result.status == "ready" else AI_STATUS_ERROR if result.status == "error" else AI_STATUS_SKIPPED,
                        reason=result.reason,
                        error=(result.brief.error if result.brief is not None else getattr(result, "error", None)) if result.status == "error" else None,
                        worker_name=worker_name,
                        model=result.brief.model if result.brief is not None else active_model,
                        prompt_tokens=result.brief.prompt_tokens if result.brief is not None else None,
                        completion_tokens=result.brief.completion_tokens if result.brief is not None else None,
                        total_tokens=result.brief.total_tokens if result.brief is not None else None,
                        latency_ms=result.brief.latency_ms if result.brief is not None else None,
                        prompt_char_count=result.prompt_char_count,
                        response_char_count=result.response_char_count,
                        metadata_updates={
                            "items_considered": result.items_considered,
                            "items_selected": result.items_selected,
                            "brief_date": brief_date,
                            AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: True,
                        },
                        daily_brief_id=result.brief.id if result.brief is not None else None,
                    )
                    db.commit()
                    processed_dates.append(brief_date)

                refreshed_run = db.scalar(select(AITaskRun).where(AITaskRun.id == parent_run_id))
                return {
                    "status": refreshed_run.status if refreshed_run is not None else "unknown",
                    "reason": refreshed_run.reason if refreshed_run is not None else None,
                    "run_id": str(parent_run_id),
                    "processed_dates": processed_dates,
                }
        except CoordinationUnavailableError as exc:
            logger.warning("daily_brief_backfill_coordination_unavailable error_type=%s", _exception_type_name(exc))
            defer_ai_workflow_run(db, run_id=parent_run_id, reason="coordination_unavailable", retry_after_seconds=30)
            db.commit()
            return {"status": "queued", "reason": "coordination_unavailable", "run_id": str(parent_run_id)}
