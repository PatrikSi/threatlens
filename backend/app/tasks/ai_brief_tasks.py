from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.services.ai_config import load_active_ai_settings
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


def _exception_type_name(exc: BaseException) -> str:
    return exc.__class__.__name__


def _task_run_claimed_by_current_worker(run: AITaskRun | None, *, celery_task_id: str | None) -> bool:
    if run is None or run.finished_at is not None or run.status != AI_STATUS_RUNNING:
        return False
    if celery_task_id is None:
        return True
    return run.celery_task_id == celery_task_id


def _scheduled_daily_ai_brief_due(db: Session, *, now: datetime) -> tuple[bool, str | None]:
    active = load_active_ai_settings(db)
    if not active.ai_enabled:
        return False, "ai_disabled"
    if not active.ai_configured:
        return False, "ai_not_configured"
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
        if task_run is not None and not _is_stale_daily_brief_task_run(task_run, now=now):
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
)
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
                        run = db.scalar(select(AITaskRun).where(AITaskRun.id == parsed_run_id))
                        if run is not None:
                            finish_ai_task_run(
                                db,
                                run_id=run.id,
                                status=AI_STATUS_SKIPPED,
                                reason="already_running",
                                worker_name=getattr(self.request, "hostname", None),
                                metadata_updates={
                                    "force": bool(force),
                                    "lock_observed_at": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            db.commit()
                    result = {"status": "skipped", "reason": "already_running"}
                    if parsed_run_id is not None:
                        result["run_id"] = str(parsed_run_id)
                    return result
                if parsed_run_id:
                    run = db.scalar(select(AITaskRun).where(AITaskRun.id == parsed_run_id))
                    if run is None:
                        run = queue_ai_task_run(
                            db,
                            task_type=AI_TASK_TYPE_DAILY_BRIEF,
                            trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                            actor_user_id=parsed_actor_user_id,
                            model=None,
                            metadata={"force": bool(force), "scheduled": parsed_actor_user_id is None},
                        )
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
                active_ai_settings = load_active_ai_settings(db)
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
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=AI_STATUS_SKIPPED,
                        reason="ai_not_configured",
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "ai_not_configured"}
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

                result = run_daily_brief_generation(db, force=force, task_run_id=run.id)
                finish_ai_task_run(
                    db,
                    run_id=run.id,
                    status=AI_STATUS_READY if result.status == "ready" else AI_STATUS_ERROR if result.status == "error" else AI_STATUS_SKIPPED,
                    reason=result.reason,
                    error=result.brief.error if result.brief is not None and result.status == "error" else None,
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
        except CoordinationUnavailableError as exc:
            logger.warning("daily_brief_coordination_unavailable error_type=%s", _exception_type_name(exc))
            if run is not None:
                finish_ai_task_run(
                    db,
                    run_id=run.id,
                    status=AI_STATUS_ERROR,
                    reason="coordination_unavailable",
                    error="coordination_unavailable",
                    worker_name=getattr(self.request, "hostname", None),
                )
                db.commit()
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
    if run.finished_at is None or run.status not in {AI_STATUS_READY, AI_STATUS_ERROR, AI_STATUS_SKIPPED}:
        return False
    metadata = run.metadata_json or {}
    if metadata.get(AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY) is False:
        return False
    return not (run.reason and run.reason.startswith("stale_"))


def _daily_brief_backfill_attempt_number(attempts: list[AITaskRun]) -> int:
    attempt_numbers: list[int] = []
    for attempt in attempts:
        try:
            attempt_numbers.append(int((attempt.metadata_json or {}).get("attempt") or 0))
        except (TypeError, ValueError):
            continue
    return max([len(attempts), *attempt_numbers], default=0) + 1


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.backfill_daily_ai_briefs",
    acks_late=True,
    reject_on_worker_lost=True,
)
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
        parent_was_running = bool(
            run is not None
            and run.status == AI_STATUS_RUNNING
            and run.finished_at is None
        )
        if run is None:
            run = queue_ai_task_run(
                db,
                task_type=AI_TASK_TYPE_REPROCESS,
                trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                actor_user_id=parsed_actor_user_id,
                metadata={"scope": AI_DAILY_BRIEF_BACKFILL_SCOPE, "days": max(0, effective_days), "force": True},
                target_count=max(0, effective_days),
            )

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

        active_ai_settings = load_active_ai_settings(db)
        if not active_ai_settings.ai_enabled:
            finish_ai_task_run(db, run_id=parent_run_id, status=AI_STATUS_SKIPPED, reason="ai_disabled", worker_name=worker_name)
            db.commit()
            return {"status": "skipped", "reason": "ai_disabled", "run_id": str(parent_run_id)}
        if not active_ai_settings.ai_configured:
            finish_ai_task_run(db, run_id=parent_run_id, status=AI_STATUS_SKIPPED, reason="ai_not_configured", worker_name=worker_name)
            db.commit()
            return {"status": "skipped", "reason": "ai_not_configured", "run_id": str(parent_run_id)}
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
                    active_parent = db.scalar(select(AITaskRun).where(AITaskRun.id == parent_run_id))
                    if parent_was_running and active_parent is not None and active_parent.finished_at is None:
                        active_parent.metadata_json = {
                            **dict(active_parent.metadata_json or {}),
                            "duplicate_lock_observed_at": datetime.now(timezone.utc).isoformat(),
                        }
                        db.add(active_parent)
                        record_ai_task_event(
                            db,
                            run_id=parent_run_id,
                            event_type="duplicate_delivery_deferred",
                            payload={"celery_task_id": celery_task_id, "worker_name": worker_name},
                        )
                        db.commit()
                        return {"status": "skipped", "reason": "already_running", "run_id": str(parent_run_id)}
                    finish_ai_task_run(
                        db,
                        run_id=parent_run_id,
                        status=AI_STATUS_SKIPPED,
                        reason="already_running",
                        worker_name=worker_name,
                        metadata_updates={"lock_observed_at": datetime.now(timezone.utc).isoformat()},
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "already_running", "run_id": str(parent_run_id)}

                processed_dates: list[str] = []
                for reference_time in _daily_brief_backfill_reference_times(effective_days):
                    brief_date = reference_time.date().isoformat()
                    parent_run = db.scalar(select(AITaskRun).where(AITaskRun.id == parent_run_id))
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
                    if any(_daily_brief_backfill_attempt_is_settled(attempt) for attempt in attempts):
                        processed_dates.append(brief_date)
                        continue

                    attempt_number = _daily_brief_backfill_attempt_number(attempts)
                    for interrupted_attempt in [attempt for attempt in attempts if attempt.finished_at is None]:
                        interrupted_attempt.metadata_json = {
                            **dict(interrupted_attempt.metadata_json or {}),
                            AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: False,
                            "superseded_by_attempt": attempt_number,
                        }
                        db.add(interrupted_attempt)
                        finish_ai_task_run(
                            db,
                            run_id=interrupted_attempt.id,
                            status=AI_STATUS_SKIPPED,
                            reason="superseded_by_redelivery",
                            worker_name=interrupted_attempt.worker_name or worker_name,
                            model=interrupted_attempt.model or active_model,
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
                        error=result.brief.error if result.brief is not None and result.status == "error" else None,
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
            finish_ai_task_run(
                db,
                run_id=parent_run_id,
                status=AI_STATUS_ERROR,
                reason="coordination_unavailable",
                error="coordination_unavailable",
                worker_name=worker_name,
            )
            db.commit()
            return {"status": "error", "reason": "coordination_unavailable", "run_id": str(parent_run_id)}
