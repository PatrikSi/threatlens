from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.schemas.ai import (
    AILiveStatusResponse,
    AILiveTaskResponse,
    AIOpsOverviewResponse,
    AITaskEventResponse,
    AITaskRunDetailResponse,
    AITaskRunListResponse,
)
from app.services.ai_ops_common import (
    AI_CONNECTION_TEST_BLOCKING_TASK_TYPES,
    AI_DAILY_BRIEF_BACKFILL_SCOPE,
    AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY,
    AI_PROVIDER_CLAIM_DAILY_BRIEF as AI_PROVIDER_CLAIM_DAILY_BRIEF,
    AI_PROVIDER_CLAIM_ITEM_ENRICHMENT as AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
    AI_PROVIDER_CLAIM_METADATA_KEY as AI_PROVIDER_CLAIM_METADATA_KEY,
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_READY,
    AI_STATUS_RUNNING,
    AI_STATUS_SKIPPED,
    AI_TASK_NAMES as AI_TASK_NAMES,
    AI_TASK_TYPE_CONNECTION_TEST as AI_TASK_TYPE_CONNECTION_TEST,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPORT,
    AI_TASK_TYPE_REPROCESS,
    AI_TERMINAL_STATUSES,
    AI_TRIGGER_AUTO as AI_TRIGGER_AUTO,
    AI_TRIGGER_MANUAL as AI_TRIGGER_MANUAL,
    AI_TRIGGER_SCHEDULED as AI_TRIGGER_SCHEDULED,
    INELIGIBLE_REASONS,
    STALE_AI_RUN_FALLBACK_GRACE_PERIOD,
    STALE_AI_RUN_GRACE_PERIOD,
    AIConnectionTestWorkload,
    _coerce_utc as _coerce_utc,
    _extract_uuid as _extract_uuid,
    _merge_metadata as _merge_metadata,
    _percentile as _percentile,
)
from app.services.ai_ops_metrics import (
    _build_cache_stats as _build_cache_stats,
    _build_coverage_stats as _build_coverage_stats,
    _build_endpoint_health as _build_endpoint_health,
    _build_feature_health as _build_feature_health,
    _build_per_model_usage as _build_per_model_usage,
    _build_relevance_distribution as _build_relevance_distribution,
    _build_storage_stats as _build_storage_stats,
    _build_time_series as _build_time_series,
    _build_token_efficiency as _build_token_efficiency,
    _load_skip_counts as _load_skip_counts,
    _looks_like_auth_error as _looks_like_auth_error,
    _normalize_error_text as _normalize_error_text,
    build_ai_ops_overview,
    list_ai_failures as list_ai_failures,
)
from app.services.ai_task_projection import (
    list_ai_manual_actions as list_ai_manual_actions,
    list_ai_prompt_history as list_ai_prompt_history,
    list_daily_brief_source_items as list_daily_brief_source_items,
    _load_run_item_context as _load_run_item_context,
    _load_user_emails as _load_user_emails,
    _map_audit_entries as _map_audit_entries,
    _map_run_responses as _map_run_responses,
)
from app.services.ai_task_runtime import (
    _coerce_live_args as _coerce_live_args,
    _extract_positional_item_id as _extract_positional_item_id,
    _extract_positional_task_run_id as _extract_positional_task_run_id,
    _flatten_live_tasks as _flatten_live_tasks,
    _load_live_task_snapshot as _load_live_task_snapshot,
    _normalize_live_task_snapshot as _normalize_live_task_snapshot,
    get_ai_db_live_status as get_ai_db_live_status,
)
from app.services.ai_task_settlement import settle_pending_ai_resource
from app.services.report_execution import invalidate_stale_report_generation
from app.tasks.celery_app import celery_app


def queue_ai_task_run(
    db: Session,
    *,
    task_type: str,
    trigger_source: str,
    actor_user_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    daily_brief_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    target_count: int | None = None,
    reason: str | None = None,
) -> AITaskRun:
    queued_at = datetime.now(timezone.utc)
    run = AITaskRun(
        task_type=task_type,
        trigger_source=trigger_source,
        status=AI_STATUS_QUEUED,
        reason=reason,
        actor_user_id=actor_user_id,
        item_id=item_id,
        daily_brief_id=daily_brief_id,
        report_id=report_id,
        parent_run_id=parent_run_id,
        model=model,
        metadata_json=metadata or {},
        target_count=target_count,
        queued_at=queued_at,
        created_at=queued_at,
        updated_at=queued_at,
    )
    db.add(run)
    db.flush()
    record_ai_task_event(db, run_id=run.id, event_type="queued", payload=metadata or {})
    return run


def record_ai_task_event(
    db: Session,
    *,
    run_id: uuid.UUID,
    event_type: str,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AITaskEvent:
    event = AITaskEvent(
        task_run_id=run_id,
        event_type=event_type,
        message=message,
        payload_json=payload or {},
    )
    db.add(event)
    db.flush()
    return event


def update_ai_task_run_celery(
    db: Session,
    *,
    run_id: uuid.UUID,
    celery_task_id: str | None,
    worker_name: str | None = None,
) -> AITaskRun | None:
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return None
    run.celery_task_id = celery_task_id
    if worker_name:
        run.worker_name = worker_name
    db.add(run)
    return run


def start_ai_task_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    worker_name: str | None = None,
    celery_task_id: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
) -> AITaskRun | None:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    if run.finished_at is not None or run.status in AI_TERMINAL_STATUSES:
        return run

    if celery_task_id and run.celery_task_id not in (None, celery_task_id):
        return run

    now = datetime.now(timezone.utc)
    started_at_was_missing = run.started_at is None
    run.status = AI_STATUS_RUNNING
    run.started_at = run.started_at or now
    if worker_name:
        run.worker_name = worker_name
    if celery_task_id:
        run.celery_task_id = celery_task_id
    if metadata_updates:
        run.metadata_json = _merge_metadata(run.metadata_json, metadata_updates)
    db.add(run)
    if started_at_was_missing:
        record_ai_task_event(
            db,
            run_id=run.id,
            event_type="started",
            payload={"worker_name": worker_name, "celery_task_id": celery_task_id},
        )
    return run


def ai_task_run_stop_reason(run: AITaskRun | None) -> str | None:
    if run is None:
        return None
    if _is_cancel_requested_run(run):
        return "canceled"
    if run.finished_at is not None or run.status in AI_TERMINAL_STATUSES:
        return run.reason or f"already_{run.status}"
    return None


def get_ai_task_run_stop_reason(db: Session, *, run_id: uuid.UUID | None) -> str | None:
    if run_id is None:
        return None
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    return ai_task_run_stop_reason(run)


def finish_ai_task_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    status: str,
    reason: str | None = None,
    error: str | None = None,
    worker_name: str | None = None,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    latency_ms: int | None = None,
    prompt_char_count: int | None = None,
    response_char_count: int | None = None,
    input_text_chars: int | None = None,
    metadata_updates: dict[str, Any] | None = None,
    daily_brief_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
) -> AITaskRun | None:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    if run.finished_at is not None or run.status in AI_TERMINAL_STATUSES:
        return run
    if _is_cancel_requested_run(run):
        status = AI_STATUS_SKIPPED
        reason = "canceled"
        error = None
        metadata_updates = _merge_metadata(
            metadata_updates,
            {"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
        )
    now = datetime.now(timezone.utc)
    if run.started_at is None:
        run.started_at = (
            _coerce_utc(run.queued_at) if run.queued_at is not None else now
        )
    run.finished_at = now
    run.status = status
    run.reason = reason
    run.error = error
    run.worker_name = worker_name or run.worker_name
    run.model = model or run.model
    run.prompt_tokens = (
        prompt_tokens if prompt_tokens is not None else run.prompt_tokens
    )
    run.completion_tokens = (
        completion_tokens if completion_tokens is not None else run.completion_tokens
    )
    run.total_tokens = total_tokens if total_tokens is not None else run.total_tokens
    run.latency_ms = latency_ms if latency_ms is not None else run.latency_ms
    run.prompt_char_count = (
        prompt_char_count if prompt_char_count is not None else run.prompt_char_count
    )
    run.response_char_count = (
        response_char_count
        if response_char_count is not None
        else run.response_char_count
    )
    run.input_text_chars = (
        input_text_chars if input_text_chars is not None else run.input_text_chars
    )
    if daily_brief_id is not None:
        run.daily_brief_id = daily_brief_id
    if report_id is not None:
        run.report_id = report_id
    if run.started_at is not None:
        run.duration_ms = _duration_ms_between(run.started_at, now)
    if metadata_updates:
        run.metadata_json = _merge_metadata(run.metadata_json, metadata_updates)
    settle_pending_ai_resource(
        db,
        run=run,
        status=status,
        reason=reason,
        error=error,
        settled_at=now,
    )
    db.add(run)
    event_type = (
        "completed"
        if status == AI_STATUS_READY
        else "failed"
        if status == AI_STATUS_ERROR
        else "skipped"
    )
    payload: dict[str, Any] = {"status": status}
    if reason:
        payload["reason"] = reason
    if error:
        payload["error"] = error
    record_ai_task_event(
        db,
        run_id=run.id,
        event_type=event_type,
        message=error or reason,
        payload=payload,
    )
    if run.parent_run_id:
        _increment_parent_run_progress(db, child_run=run)
    return run


def list_ai_task_runs(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    task_type: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
    model: str | None = None,
    since: datetime | None = None,
    parent_run_id: uuid.UUID | None = None,
    only_failures: bool = False,
    reconcile_stale: bool = True,
) -> AITaskRunListResponse:
    if reconcile_stale:
        _reconcile_stale_ai_runs(db)
    base_query = select(AITaskRun)
    count_query = select(func.count(AITaskRun.id))
    if task_type:
        base_query = base_query.where(AITaskRun.task_type == task_type)
        count_query = count_query.where(AITaskRun.task_type == task_type)
    if status:
        base_query = base_query.where(AITaskRun.status == status)
        count_query = count_query.where(AITaskRun.status == status)
    if trigger_source:
        base_query = base_query.where(AITaskRun.trigger_source == trigger_source)
        count_query = count_query.where(AITaskRun.trigger_source == trigger_source)
    if model:
        base_query = base_query.where(AITaskRun.model == model)
        count_query = count_query.where(AITaskRun.model == model)
    if since:
        base_query = base_query.where(AITaskRun.created_at >= since)
        count_query = count_query.where(AITaskRun.created_at >= since)
    if parent_run_id:
        base_query = base_query.where(AITaskRun.parent_run_id == parent_run_id)
        count_query = count_query.where(AITaskRun.parent_run_id == parent_run_id)
    if only_failures:
        failure_filter = or_(
            AITaskRun.status == AI_STATUS_ERROR, AITaskRun.error.is_not(None)
        )
        base_query = base_query.where(failure_filter)
        count_query = count_query.where(failure_filter)

    total = int(db.scalar(count_query) or 0)
    runs = list(
        db.scalars(
            base_query.order_by(AITaskRun.created_at.desc()).offset(offset).limit(limit)
        )
    )
    return AITaskRunListResponse(
        total=total, limit=limit, offset=offset, items=_map_run_responses(db, runs)
    )


def get_ai_task_run_detail(
    db: Session, *, run_id: uuid.UUID
) -> AITaskRunDetailResponse | None:
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return None
    if run.status in {AI_STATUS_QUEUED, AI_STATUS_RUNNING}:
        _reconcile_stale_ai_runs(db)
        run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
        if run is None:
            return None
    events = list(
        db.scalars(
            select(AITaskEvent)
            .where(AITaskEvent.task_run_id == run_id)
            .order_by(AITaskEvent.created_at.asc())
        )
    )
    run_response = _map_run_responses(db, [run])[0]
    event_responses = [
        AITaskEventResponse(
            id=event.id,
            task_run_id=event.task_run_id,
            event_type=event.event_type,
            message=event.message,
            payload=dict(event.payload_json or {}),
            created_at=event.created_at,
        )
        for event in events
    ]
    return AITaskRunDetailResponse(run=run_response, events=event_responses)


def cancel_ai_task_run(
    db: Session, *, run_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> AITaskRun | None:
    snapshot_available, workers, active_tasks, reserved_tasks, scheduled_tasks = (
        _normalize_live_task_snapshot(_load_live_task_snapshot())
    )
    _reconcile_stale_ai_runs(
        db,
        snapshot_available=snapshot_available,
        workers=workers,
        active_tasks=active_tasks,
        reserved_tasks=reserved_tasks,
        scheduled_tasks=scheduled_tasks,
    )
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return None

    unfinished_states = {AI_STATUS_QUEUED, AI_STATUS_RUNNING}
    if run.finished_at is not None or run.status not in unfinished_states:
        return run

    active_task_ids = {
        task.celery_task_id for task in active_tasks if task.celery_task_id
    }
    pending_task_ids = {
        task.celery_task_id
        for task in [*reserved_tasks, *scheduled_tasks]
        if task.celery_task_id
    }
    runs_to_cancel = [run]
    if run.task_type == AI_TASK_TYPE_REPROCESS:
        child_runs = list(
            db.scalars(
                select(AITaskRun).where(
                    AITaskRun.parent_run_id == run.id,
                    AITaskRun.finished_at.is_(None),
                    AITaskRun.status.in_(unfinished_states),
                )
            )
        )
        runs_to_cancel = [*child_runs, run]

    for target in runs_to_cancel:
        terminate_running_task = bool(
            target.celery_task_id and target.celery_task_id in active_task_ids
        )
        removed_from_queue = bool(
            target.status == AI_STATUS_QUEUED
            and not terminate_running_task
            and (
                target.celery_task_id is None
                or target.celery_task_id in pending_task_ids
                or (snapshot_available and target.celery_task_id not in active_task_ids)
            )
        )
        revoke_failed = False
        if target.celery_task_id:
            try:
                celery_app.control.revoke(
                    target.celery_task_id,
                    terminate=terminate_running_task,
                    signal="SIGTERM",
                )
            except Exception:
                revoke_failed = True
                record_ai_task_event(
                    db,
                    run_id=target.id,
                    event_type="cancel_revoke_failed",
                    payload={"celery_task_id": target.celery_task_id},
                )
        _mark_ai_task_run_cancel_requested(
            db,
            run_id=target.id,
            actor_user_id=actor_user_id,
            removed_from_queue=removed_from_queue,
            terminated_running_task=terminate_running_task,
            revoke_failed=revoke_failed,
        )
        if removed_from_queue:
            finish_ai_task_run(
                db,
                run_id=target.id,
                status=AI_STATUS_SKIPPED,
                reason="canceled",
                worker_name=target.worker_name,
                model=target.model,
                metadata_updates={
                    "cancel_observed_at": datetime.now(timezone.utc).isoformat(),
                    "cancel_completed_without_worker": True,
                },
            )

    db.commit()
    return db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))


def get_ai_live_status(db: Session) -> AILiveStatusResponse:
    snapshot_available, workers, active_tasks, reserved_tasks, scheduled_tasks = (
        _normalize_live_task_snapshot(_load_live_task_snapshot())
    )
    _reconcile_stale_ai_runs(
        db,
        snapshot_available=snapshot_available,
        workers=workers,
        active_tasks=active_tasks,
        reserved_tasks=reserved_tasks,
        scheduled_tasks=scheduled_tasks,
    )

    oldest_queued = db.scalar(
        select(AITaskRun.queued_at)
        .where(AITaskRun.status == AI_STATUS_QUEUED)
        .order_by(AITaskRun.queued_at.asc())
    )
    oldest_age = None
    if oldest_queued is not None:
        oldest_age = max(
            0,
            int(
                (
                    datetime.now(timezone.utc) - _coerce_utc(oldest_queued)
                ).total_seconds()
            ),
        )

    queued_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(AITaskRun.status == AI_STATUS_QUEUED)
        )
        or 0
    )
    return AILiveStatusResponse(
        worker_count=len(workers),
        workers=workers,
        active_tasks=active_tasks,
        reserved_tasks=reserved_tasks,
        scheduled_tasks=scheduled_tasks,
        active_count=len(active_tasks),
        reserved_count=len(reserved_tasks),
        scheduled_count=len(scheduled_tasks),
        queued_count=queued_count,
        oldest_queued_age_seconds=oldest_age,
    )


def get_ai_connection_test_workload(db: Session) -> AIConnectionTestWorkload:
    _reconcile_stale_ai_runs(db)
    status_counts = {
        status: int(count)
        for status, count in db.execute(
            select(AITaskRun.status, func.count(AITaskRun.id))
            .where(
                AITaskRun.task_type.in_(AI_CONNECTION_TEST_BLOCKING_TASK_TYPES),
                AITaskRun.finished_at.is_(None),
                AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            )
            .group_by(AITaskRun.status)
        ).all()
    }
    return AIConnectionTestWorkload(
        running_task_count=status_counts.get(AI_STATUS_RUNNING, 0),
        queued_task_count=status_counts.get(AI_STATUS_QUEUED, 0),
    )


def get_ai_ops_overview(db: Session, *, days: int = 30) -> AIOpsOverviewResponse:
    return build_ai_ops_overview(
        db, days=days, live_status_loader=get_ai_db_live_status
    )


def _increment_parent_run_progress(db: Session, *, child_run: AITaskRun) -> None:
    if child_run.parent_run_id is None:
        return
    parent = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == child_run.parent_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if parent is None:
        return

    if _is_daily_brief_backfill_parent(parent):
        _recalculate_daily_brief_backfill_parent_progress(db, parent=parent)
        return

    parent.processed_count = int(parent.processed_count or 0) + 1
    if child_run.status == AI_STATUS_READY:
        parent.success_count = int(parent.success_count or 0) + 1
    elif child_run.status == AI_STATUS_ERROR:
        parent.error_count = int(parent.error_count or 0) + 1
    elif child_run.status == AI_STATUS_SKIPPED:
        parent.skipped_count = int(parent.skipped_count or 0) + 1
        if child_run.reason in {"unchanged", "source_hash_unchanged"}:
            parent.skipped_unchanged_count = (
                int(parent.skipped_unchanged_count or 0) + 1
            )
        if child_run.reason in INELIGIBLE_REASONS:
            parent.skipped_ineligible_count = (
                int(parent.skipped_ineligible_count or 0) + 1
            )
        if child_run.reason == "canceled":
            parent.metadata_json = _merge_metadata(
                parent.metadata_json, {"was_canceled": True}
            )
    if parent.started_at is None:
        parent.started_at = datetime.now(timezone.utc)
    target_count = int(parent.target_count or 0)
    if (
        target_count > 0
        and parent.processed_count >= target_count
        and parent.finished_at is None
    ):
        parent.finished_at = datetime.now(timezone.utc)
        parent.duration_ms = _duration_ms_between(parent.started_at, parent.finished_at)
        parent.status, parent.reason = _resolve_parent_terminal_state(parent)
        record_ai_task_event(
            db,
            run_id=parent.id,
            event_type="completed",
            payload={
                "status": parent.status,
                "processed_count": parent.processed_count,
                "success_count": parent.success_count,
                "error_count": parent.error_count,
                "skipped_count": parent.skipped_count,
            },
        )
    db.add(parent)


def reconcile_daily_brief_backfill_parent_progress(
    db: Session,
    *,
    parent_run_id: uuid.UUID,
    reopen_incomplete: bool = False,
) -> AITaskRun | None:
    parent = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == parent_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if parent is None or not _is_daily_brief_backfill_parent(parent):
        return parent
    _recalculate_daily_brief_backfill_parent_progress(
        db,
        parent=parent,
        reopen_incomplete=reopen_incomplete,
    )
    return parent


def _is_daily_brief_backfill_parent(run: AITaskRun) -> bool:
    return (
        run.task_type == AI_TASK_TYPE_REPROCESS
        and (run.metadata_json or {}).get("scope") == AI_DAILY_BRIEF_BACKFILL_SCOPE
    )


def _recalculate_daily_brief_backfill_parent_progress(
    db: Session,
    *,
    parent: AITaskRun,
    reopen_incomplete: bool = False,
) -> None:
    child_runs = list(
        db.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.parent_run_id == parent.id,
                AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            )
            .order_by(AITaskRun.created_at.asc(), AITaskRun.id.asc())
        )
    )
    outcomes_by_date: dict[str, AITaskRun] = {}
    for child in child_runs:
        if child.finished_at is None or child.status not in AI_TERMINAL_STATUSES:
            continue
        metadata = child.metadata_json or {}
        if metadata.get(AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY) is False:
            continue
        if child.reason and child.reason.startswith("stale_"):
            continue
        brief_date = metadata.get("brief_date")
        outcome_key = str(brief_date) if brief_date else f"legacy:{child.id}"
        outcomes_by_date[outcome_key] = child

    outcomes = list(outcomes_by_date.values())
    previous_processed_count = int(parent.processed_count or 0)
    parent.processed_count = len(outcomes)
    parent.success_count = sum(child.status == AI_STATUS_READY for child in outcomes)
    parent.error_count = sum(child.status == AI_STATUS_ERROR for child in outcomes)
    parent.skipped_count = sum(child.status == AI_STATUS_SKIPPED for child in outcomes)
    parent.skipped_unchanged_count = sum(
        child.status == AI_STATUS_SKIPPED
        and child.reason in {"unchanged", "source_hash_unchanged"}
        for child in outcomes
    )
    parent.skipped_ineligible_count = sum(
        child.status == AI_STATUS_SKIPPED and child.reason in INELIGIBLE_REASONS
        for child in outcomes
    )
    if any(child.reason == "canceled" for child in outcomes):
        parent.metadata_json = _merge_metadata(
            parent.metadata_json, {"was_canceled": True}
        )

    target_count = int(parent.target_count or 0)
    progress_terminal_reason = parent.reason in {
        None,
        "partial_failures",
        "partial_skips",
        "stale_reprocess_tracking",
    }
    progress_terminal = (
        parent.status in AI_TERMINAL_STATUSES and progress_terminal_reason
    )
    if target_count > 0 and parent.processed_count >= target_count:
        terminal_status, terminal_reason = _resolve_parent_terminal_state(parent)
        if parent.finished_at is None:
            parent.finished_at = datetime.now(timezone.utc)
            if parent.started_at is None:
                parent.started_at = parent.finished_at
            parent.duration_ms = _duration_ms_between(
                parent.started_at, parent.finished_at
            )
            parent.status = terminal_status
            parent.reason = terminal_reason
            record_ai_task_event(
                db,
                run_id=parent.id,
                event_type="completed",
                payload={
                    "status": parent.status,
                    "processed_count": parent.processed_count,
                    "success_count": parent.success_count,
                    "error_count": parent.error_count,
                    "skipped_count": parent.skipped_count,
                },
            )
        elif progress_terminal:
            parent.status = terminal_status
            parent.reason = terminal_reason
    elif reopen_incomplete and parent.finished_at is not None and progress_terminal:
        parent.status = AI_STATUS_RUNNING
        parent.reason = None
        parent.finished_at = None
        parent.duration_ms = None
        parent.metadata_json = _merge_metadata(
            parent.metadata_json, {"progress_repaired": True}
        )
        record_ai_task_event(
            db,
            run_id=parent.id,
            event_type="progress_repaired",
            payload={
                "previous_processed_count": previous_processed_count,
                "processed_count": parent.processed_count,
                "target_count": target_count,
            },
        )
    db.add(parent)


def _resolve_parent_terminal_state(run: AITaskRun) -> tuple[str, str | None]:
    if int(run.error_count or 0):
        return AI_STATUS_ERROR, "partial_failures"
    if bool((run.metadata_json or {}).get("was_canceled")):
        return AI_STATUS_SKIPPED, "canceled"
    if int(run.skipped_count or 0):
        return AI_STATUS_SKIPPED, "partial_skips"
    return AI_STATUS_READY, None


def _reconcile_stale_ai_runs(
    db: Session,
    *,
    snapshot_available: bool | None = None,
    workers: list[str] | None = None,
    active_tasks: list[AILiveTaskResponse] | None = None,
    reserved_tasks: list[AILiveTaskResponse] | None = None,
    scheduled_tasks: list[AILiveTaskResponse] | None = None,
) -> int:
    if (
        workers is None
        or active_tasks is None
        or reserved_tasks is None
        or scheduled_tasks is None
    ):
        snapshot_available, workers, active_tasks, reserved_tasks, scheduled_tasks = (
            _normalize_live_task_snapshot(_load_live_task_snapshot())
        )
    elif snapshot_available is None:
        snapshot_available = True

    _ = workers
    can_reconcile_missing_live_tasks = bool(snapshot_available)
    live_task_ids = {
        task.celery_task_id
        for task in [*active_tasks, *reserved_tasks, *scheduled_tasks]
        if task.celery_task_id
    }
    live_run_ids = {
        task.run_id
        for task in [*active_tasks, *reserved_tasks, *scheduled_tasks]
        if task.run_id
    }
    now = datetime.now(timezone.utc)
    stale_before = now - STALE_AI_RUN_GRACE_PERIOD
    fallback_stale_before = now - STALE_AI_RUN_FALLBACK_GRACE_PERIOD
    changed = False
    reconciled_count = 0

    unfinished_leaf_runs = list(
        db.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.task_type.in_(
                    [
                        AI_TASK_TYPE_ITEM_ENRICHMENT,
                        AI_TASK_TYPE_DAILY_BRIEF,
                        AI_TASK_TYPE_REPORT,
                    ]
                ),
                AITaskRun.finished_at.is_(None),
                AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            )
            .order_by(AITaskRun.created_at.asc())
        )
    )
    for run in unfinished_leaf_runs:
        # Queued report runs are owned by the durable report dispatcher. Celery
        # inspection cannot see messages waiting in the broker, so absence from
        # active/reserved/scheduled snapshots is not evidence that queued work
        # was lost.
        if run.task_type == AI_TASK_TYPE_REPORT and run.status == AI_STATUS_QUEUED:
            continue
        if can_reconcile_missing_live_tasks:
            if not _is_stale_unfinished_run(
                run,
                live_task_ids,
                live_run_ids,
                running_stale_before=stale_before,
                queued_stale_before=fallback_stale_before,
            ):
                continue
            if run.status == AI_STATUS_QUEUED:
                stale_reason = "stale_queued_task_unstarted"
                stale_error = "Task remained queued beyond the stale-run grace period and no longer appears in Celery"
            else:
                stale_reason = "stale_task_lost"
                stale_error = (
                    "Task no longer appears in Celery and did not report completion"
                )
        else:
            if (
                run.celery_task_id and run.celery_task_id in live_task_ids
            ) or run.id in live_run_ids:
                continue
            if not _is_unfinished_run_past_stale_grace(run, fallback_stale_before):
                continue
            stale_reason = "stale_task_snapshot_unavailable"
            stale_error = "Task exceeded the fallback stale-run grace period while Celery inspection was unavailable"
        if not _finish_reconciled_stale_run(
            db,
            run=run,
            snapshot_available=can_reconcile_missing_live_tasks,
            stale_reason=stale_reason,
            stale_error=stale_error,
        ):
            continue
        changed = True
        reconciled_count += 1

    stale_parent_runs = list(
        db.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.task_type == AI_TASK_TYPE_REPROCESS,
                AITaskRun.finished_at.is_(None),
                AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            )
            .order_by(AITaskRun.created_at.asc())
        )
    )
    for run in stale_parent_runs:
        unfinished_child_count = int(
            db.scalar(
                select(func.count(AITaskRun.id)).where(
                    AITaskRun.parent_run_id == run.id,
                    AITaskRun.finished_at.is_(None),
                    AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
                )
            )
            or 0
        )
        target_count = int(run.target_count or 0)
        processed_count = int(run.processed_count or 0)
        if target_count > 0 and processed_count >= target_count:
            terminal_status, terminal_reason = _resolve_parent_terminal_state(run)
            finish_ai_task_run(
                db,
                run_id=run.id,
                status=terminal_status,
                reason=terminal_reason,
                error=run.error,
                worker_name=run.worker_name,
                model=run.model,
                metadata_updates={"stale_reconciled": True},
            )
            changed = True
            reconciled_count += 1
            continue
        if not can_reconcile_missing_live_tasks:
            continue
        if unfinished_child_count > 0 or not _is_stale_unfinished_run(
            run,
            live_task_ids,
            live_run_ids,
            running_stale_before=stale_before,
            queued_stale_before=fallback_stale_before,
        ):
            continue
        if not _finish_reconciled_stale_run(
            db,
            run=run,
            snapshot_available=can_reconcile_missing_live_tasks,
            stale_reason="stale_reprocess_tracking",
            stale_error="Reprocess task stopped updating and is no longer active in Celery",
        ):
            continue
        changed = True
        reconciled_count += 1

    if changed:
        db.commit()
    return reconciled_count


def _is_stale_unfinished_run(
    run: AITaskRun,
    live_task_ids: set[str],
    live_run_ids: set[uuid.UUID],
    *,
    running_stale_before: datetime,
    queued_stale_before: datetime,
) -> bool:
    if run.celery_task_id and run.celery_task_id in live_task_ids:
        return False
    if run.id in live_run_ids:
        return False
    stale_before = (
        queued_stale_before if run.status == AI_STATUS_QUEUED else running_stale_before
    )
    return _is_unfinished_run_past_stale_grace(run, stale_before)


def _is_unfinished_run_past_stale_grace(run: AITaskRun, stale_before: datetime) -> bool:
    reference = run.updated_at or run.started_at or run.queued_at or run.created_at
    return _coerce_utc(reference) < stale_before


def _duration_ms_between(started_at: datetime, finished_at: datetime) -> int:
    return max(
        0,
        int(
            (_coerce_utc(finished_at) - _coerce_utc(started_at)).total_seconds() * 1000
        ),
    )


def is_ai_task_run_cancel_requested(db: Session, *, run_id: uuid.UUID | None) -> bool:
    if run_id is None:
        return False
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return False
    return _is_cancel_requested_run(run)


def _mark_ai_task_run_cancel_requested(
    db: Session,
    *,
    run_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    removed_from_queue: bool,
    terminated_running_task: bool,
    revoke_failed: bool,
) -> AITaskRun | None:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    if run.finished_at is not None or run.status in AI_TERMINAL_STATUSES:
        return run

    already_requested = _is_cancel_requested_run(run)
    run.reason = "cancel_requested" if run.finished_at is None else run.reason
    run.metadata_json = _merge_metadata(
        run.metadata_json,
        {
            "cancel_requested_at": datetime.now(timezone.utc).isoformat(),
            "canceled_by_user_id": str(actor_user_id) if actor_user_id else None,
            "removed_from_queue": removed_from_queue,
            "terminated_running_task": terminated_running_task,
            "cancel_revoke_failed": revoke_failed,
            "was_canceled": True if run.task_type == AI_TASK_TYPE_REPROCESS else None,
        },
    )
    db.add(run)
    if not already_requested:
        record_ai_task_event(
            db,
            run_id=run.id,
            event_type="cancel_requested",
            payload={
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "removed_from_queue": removed_from_queue,
                "terminated_running_task": terminated_running_task,
                "cancel_revoke_failed": revoke_failed,
            },
        )
    return run


def _finish_reconciled_stale_run(
    db: Session,
    *,
    run: AITaskRun,
    snapshot_available: bool,
    stale_reason: str,
    stale_error: str,
) -> bool:
    locked_run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        locked_run is None
        or locked_run.finished_at is not None
        or locked_run.status in AI_TERMINAL_STATUSES
    ):
        return False
    run = locked_run
    if run.task_type == AI_TASK_TYPE_REPORT and run.report_id is not None:
        if not invalidate_stale_report_generation(
            db,
            report_id=run.report_id,
        ):
            return False
        report = db.get(Report, run.report_id)
        if report is not None and report.status in {"ready", "error", "skipped"}:
            status = {
                "ready": AI_STATUS_READY,
                "error": AI_STATUS_ERROR,
                "skipped": AI_STATUS_SKIPPED,
            }[report.status]
            finish_ai_task_run(
                db,
                run_id=run.id,
                status=status,
                reason=report.error_code,
                error=report.error if status == AI_STATUS_ERROR else None,
                worker_name=run.worker_name,
                model=report.model,
                prompt_tokens=report.prompt_tokens,
                completion_tokens=report.completion_tokens,
                total_tokens=report.total_tokens,
                report_id=report.id,
                metadata_updates={
                    "stale_reconciled": True,
                    "stale_snapshot_available": snapshot_available,
                    "terminal_report_recovered": True,
                },
            )
            return True

    if _is_cancel_requested_run(run):
        finish_ai_task_run(
            db,
            run_id=run.id,
            status=AI_STATUS_SKIPPED,
            reason="canceled",
            worker_name=run.worker_name,
            model=run.model,
            metadata_updates={
                "stale_reconciled": True,
                "stale_snapshot_available": snapshot_available,
                "cancel_observed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return True

    finish_ai_task_run(
        db,
        run_id=run.id,
        status=AI_STATUS_ERROR,
        reason=stale_reason,
        error=stale_error,
        worker_name=run.worker_name,
        model=run.model,
        metadata_updates={
            "stale_reconciled": True,
            "stale_snapshot_available": snapshot_available,
        },
    )
    return True


def _is_cancel_requested_run(run: AITaskRun) -> bool:
    metadata = run.metadata_json or {}
    return (
        bool(metadata.get("cancel_requested_at"))
        or run.reason == "cancel_requested"
        or run.reason == "canceled"
    )
