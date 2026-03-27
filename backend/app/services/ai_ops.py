from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.ai_settings import AISettings
from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.user import User
from app.schemas.ai import (
    AIAuditEntryResponse,
    AICacheStatsResponse,
    AICoverageStatsResponse,
    AIDailyBriefSourceItemResponse,
    AIEndpointHealthResponse,
    AIFailureGroupResponse,
    AIFeatureHealthRowResponse,
    AILiveStatusResponse,
    AILiveTaskResponse,
    AIOverviewKpiResponse,
    AIOverviewPerModelResponse,
    AIOpsOverviewResponse,
    AIRelevanceDistributionResponse,
    AIRelevanceFeedResponse,
    AIStorageStatsResponse,
    AITaskEventResponse,
    AITaskRunDetailResponse,
    AITaskRunListResponse,
    AITaskRunResponse,
    AITimeSeriesPointResponse,
    AITokenEfficiencyResponse,
)
from app.tasks.celery_app import celery_app

AI_TASK_TYPE_ITEM_ENRICHMENT = "item_enrichment"
AI_TASK_TYPE_DAILY_BRIEF = "daily_brief"
AI_TASK_TYPE_CONNECTION_TEST = "connection_test"
AI_TASK_TYPE_REPROCESS = "reprocess"

AI_TRIGGER_AUTO = "auto"
AI_TRIGGER_MANUAL = "manual"
AI_TRIGGER_SCHEDULED = "scheduled"

AI_STATUS_QUEUED = "queued"
AI_STATUS_RUNNING = "running"
AI_STATUS_READY = "ready"
AI_STATUS_ERROR = "error"
AI_STATUS_SKIPPED = "skipped"

AI_TASK_NAMES = {
    "app.tasks.feed_tasks.generate_item_ai_enrichment": AI_TASK_TYPE_ITEM_ENRICHMENT,
    "app.tasks.feed_tasks.reprocess_recent_ai_items": AI_TASK_TYPE_REPROCESS,
    "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation": AI_TASK_TYPE_DAILY_BRIEF,
}

STALE_AI_RUN_GRACE_PERIOD = timedelta(minutes=10)

INELIGIBLE_REASONS = {
    "ai_disabled",
    "ai_not_configured",
    "feature_disabled",
    "item_not_found",
    "no_article",
    "no_article_text",
    "not_eligible",
    "not_found",
    "auto_enrich_disabled",
    "invalid_item_id",
}


def queue_ai_task_run(
    db: Session,
    *,
    task_type: str,
    trigger_source: str,
    actor_user_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    daily_brief_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    target_count: int | None = None,
    reason: str | None = None,
) -> AITaskRun:
    run = AITaskRun(
        task_type=task_type,
        trigger_source=trigger_source,
        status=AI_STATUS_QUEUED,
        reason=reason,
        actor_user_id=actor_user_id,
        item_id=item_id,
        daily_brief_id=daily_brief_id,
        parent_run_id=parent_run_id,
        model=model,
        metadata_json=metadata or {},
        target_count=target_count,
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
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return None
    now = datetime.now(timezone.utc)
    run.status = AI_STATUS_RUNNING
    run.started_at = run.started_at or now
    if worker_name:
        run.worker_name = worker_name
    if celery_task_id:
        run.celery_task_id = celery_task_id
    if metadata_updates:
        run.metadata_json = _merge_metadata(run.metadata_json, metadata_updates)
    db.add(run)
    record_ai_task_event(
        db,
        run_id=run.id,
        event_type="started",
        payload={"worker_name": worker_name, "celery_task_id": celery_task_id},
    )
    return run


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
) -> AITaskRun | None:
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return None
    now = datetime.now(timezone.utc)
    if run.started_at is None:
        run.started_at = _coerce_utc(run.queued_at) if run.queued_at is not None else now
    run.finished_at = now
    run.status = status
    run.reason = reason
    run.error = error
    run.worker_name = worker_name or run.worker_name
    run.model = model or run.model
    run.prompt_tokens = prompt_tokens if prompt_tokens is not None else run.prompt_tokens
    run.completion_tokens = completion_tokens if completion_tokens is not None else run.completion_tokens
    run.total_tokens = total_tokens if total_tokens is not None else run.total_tokens
    run.latency_ms = latency_ms if latency_ms is not None else run.latency_ms
    run.prompt_char_count = prompt_char_count if prompt_char_count is not None else run.prompt_char_count
    run.response_char_count = response_char_count if response_char_count is not None else run.response_char_count
    run.input_text_chars = input_text_chars if input_text_chars is not None else run.input_text_chars
    if daily_brief_id is not None:
        run.daily_brief_id = daily_brief_id
    if run.started_at is not None:
        run.duration_ms = max(0, int((now - _coerce_utc(run.started_at)).total_seconds() * 1000))
    if metadata_updates:
        run.metadata_json = _merge_metadata(run.metadata_json, metadata_updates)
    db.add(run)
    event_type = "completed" if status == AI_STATUS_READY else "failed" if status == AI_STATUS_ERROR else "skipped"
    payload: dict[str, Any] = {"status": status}
    if reason:
        payload["reason"] = reason
    if error:
        payload["error"] = error
    record_ai_task_event(db, run_id=run.id, event_type=event_type, message=error or reason, payload=payload)
    if run.parent_run_id:
        _increment_parent_run_progress(db, parent_run_id=run.parent_run_id, child_status=status, child_reason=reason)
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
) -> AITaskRunListResponse:
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
        failure_filter = or_(AITaskRun.status == AI_STATUS_ERROR, AITaskRun.error.is_not(None))
        base_query = base_query.where(failure_filter)
        count_query = count_query.where(failure_filter)

    total = int(db.scalar(count_query) or 0)
    runs = list(
        db.scalars(
            base_query.order_by(AITaskRun.created_at.desc()).offset(offset).limit(limit)
        )
    )
    return AITaskRunListResponse(total=total, limit=limit, offset=offset, items=_map_run_responses(db, runs))


def get_ai_task_run_detail(db: Session, *, run_id: uuid.UUID) -> AITaskRunDetailResponse | None:
    _reconcile_stale_ai_runs(db)
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return None
    events = list(
        db.scalars(
            select(AITaskEvent).where(AITaskEvent.task_run_id == run_id).order_by(AITaskEvent.created_at.asc())
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


def get_ai_live_status(db: Session) -> AILiveStatusResponse:
    workers, active_tasks, reserved_tasks, scheduled_tasks = _load_live_task_snapshot()
    _reconcile_stale_ai_runs(
        db,
        workers=workers,
        active_tasks=active_tasks,
        reserved_tasks=reserved_tasks,
        scheduled_tasks=scheduled_tasks,
    )

    oldest_queued = db.scalar(
        select(AITaskRun.queued_at).where(AITaskRun.status == AI_STATUS_QUEUED).order_by(AITaskRun.queued_at.asc())
    )
    oldest_age = None
    if oldest_queued is not None:
        oldest_age = max(0, int((datetime.now(timezone.utc) - _coerce_utc(oldest_queued)).total_seconds()))

    queued_count = int(db.scalar(select(func.count(AITaskRun.id)).where(AITaskRun.status == AI_STATUS_QUEUED)) or 0)
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


def get_ai_ops_overview(db: Session, *, days: int = 30) -> AIOpsOverviewResponse:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, days))
    usage_events = list(db.scalars(select(AIUsageEvent).where(AIUsageEvent.created_at >= since)))
    live = get_ai_live_status(db)

    successful_events = [event for event in usage_events if event.success]
    failed_events = [event for event in usage_events if not event.success]
    total_requests = len(usage_events)
    success_rate = (len(successful_events) / total_requests * 100.0) if total_requests else 0.0
    latency_values = [float(event.latency_ms) for event in successful_events if event.latency_ms is not None]
    total_tokens = sum(int(event.total_tokens or 0) for event in usage_events)
    last_successful_run_at = db.scalar(
        select(AITaskRun.finished_at)
        .where(AITaskRun.status == AI_STATUS_READY)
        .order_by(AITaskRun.finished_at.desc())
    )

    kpis = AIOverviewKpiResponse(
        total_requests=total_requests,
        success_rate_pct=round(success_rate, 2),
        total_tokens=total_tokens,
        average_latency_ms=round(sum(latency_values) / len(latency_values), 2) if latency_values else 0.0,
        p95_latency_ms=round(_percentile(latency_values, 0.95), 2) if latency_values else 0.0,
        active_runs=live.active_count,
        queued_runs=live.queued_count,
        last_successful_run_at=last_successful_run_at,
    )

    per_model = _build_per_model_usage(usage_events)
    time_series = _build_time_series(usage_events, db, since=since, now=now)
    token_efficiency = _build_token_efficiency(usage_events)
    relevance_distribution = _build_relevance_distribution(db)
    coverage = _build_coverage_stats(db)
    failures = list_ai_failures(db, days=days, limit=10)
    endpoint_health = _build_endpoint_health(usage_events)
    feature_health = _build_feature_health(db)
    storage = _build_storage_stats(db)
    cache = _build_cache_stats(db)

    return AIOpsOverviewResponse(
        kpis=kpis,
        live=live,
        per_model=per_model,
        time_series=time_series,
        token_efficiency=token_efficiency,
        relevance_distribution=relevance_distribution,
        coverage=coverage,
        failures=failures,
        endpoint_health=endpoint_health,
        feature_health=feature_health,
        storage=storage,
        cache=cache,
    )


def list_ai_failures(db: Session, *, days: int = 30, limit: int = 25) -> list[AIFailureGroupResponse]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    groups: dict[tuple[str | None, str | None, str | None, str], dict[str, Any]] = {}

    for event in db.scalars(select(AIUsageEvent).where(AIUsageEvent.created_at >= since, AIUsageEvent.success.is_(False))):
        error = _normalize_error_text(event.error)
        key = (None, event.feature_type, event.model, error)
        entry = groups.setdefault(
            key,
            {"task_type": None, "feature_type": event.feature_type, "model": event.model, "error": error, "count": 0, "last_seen_at": None},
        )
        entry["count"] += 1
        if entry["last_seen_at"] is None or (event.created_at and event.created_at > entry["last_seen_at"]):
            entry["last_seen_at"] = event.created_at

    for run in db.scalars(
        select(AITaskRun).where(
            AITaskRun.created_at >= since,
            or_(AITaskRun.status == AI_STATUS_ERROR, AITaskRun.error.is_not(None)),
        )
    ):
        error = _normalize_error_text(run.error)
        key = (run.task_type, None, run.model, error)
        entry = groups.setdefault(
            key,
            {"task_type": run.task_type, "feature_type": None, "model": run.model, "error": error, "count": 0, "last_seen_at": None},
        )
        entry["count"] += 1
        if entry["last_seen_at"] is None or (run.finished_at and run.finished_at > entry["last_seen_at"]):
            entry["last_seen_at"] = run.finished_at or run.updated_at

    ordered = sorted(groups.values(), key=lambda value: (value["count"], value["last_seen_at"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return [
        AIFailureGroupResponse(
            task_type=row["task_type"],
            feature_type=row["feature_type"],
            model=row["model"],
            error=row["error"],
            count=int(row["count"]),
            last_seen_at=row["last_seen_at"],
        )
        for row in ordered[:limit]
    ]


def list_ai_manual_actions(db: Session, *, limit: int = 50) -> list[AIAuditEntryResponse]:
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action.in_(
                    [
                        "ai.connection.test",
                        "ai.daily_brief.generate",
                        "ai.reprocess.queue",
                    ]
                )
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )
    return _map_audit_entries(db, logs)


def list_ai_prompt_history(db: Session, *, limit: int = 50) -> list[AIAuditEntryResponse]:
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "ai.settings.update")
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )
    return _map_audit_entries(db, logs)


def list_daily_brief_source_items(
    db: Session,
    *,
    daily_brief_id: uuid.UUID,
    included: bool | None = None,
    limit: int = 200,
) -> list[AIDailyBriefSourceItemResponse]:
    query = select(AIDailyBriefSourceItem).where(AIDailyBriefSourceItem.daily_brief_id == daily_brief_id)
    if included is not None:
        query = query.where(AIDailyBriefSourceItem.included.is_(included))
    rows = list(
        db.scalars(query.order_by(AIDailyBriefSourceItem.included.desc(), AIDailyBriefSourceItem.rank.asc()).limit(limit))
    )
    return [
        AIDailyBriefSourceItemResponse(
            id=row.id,
            daily_brief_id=row.daily_brief_id,
            item_id=row.item_id,
            included=bool(row.included),
            rank=int(row.rank or 0),
            exclusion_reason=row.exclusion_reason,
            title_snapshot=row.title_snapshot,
            feed_name_snapshot=row.feed_name_snapshot,
            url_snapshot=row.url_snapshot,
            classification_snapshot=row.classification_snapshot,
            relevance_score_snapshot=float(row.relevance_score_snapshot) if row.relevance_score_snapshot is not None else None,
            relevance_label_snapshot=row.relevance_label_snapshot,
            published_at_snapshot=row.published_at_snapshot,
            first_seen_at_snapshot=row.first_seen_at_snapshot,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _increment_parent_run_progress(db: Session, *, parent_run_id: uuid.UUID, child_status: str, child_reason: str | None) -> None:
    parent = db.scalar(select(AITaskRun).where(AITaskRun.id == parent_run_id))
    if parent is None:
        return
    parent.processed_count = int(parent.processed_count or 0) + 1
    if child_status == AI_STATUS_READY:
        parent.success_count = int(parent.success_count or 0) + 1
    elif child_status == AI_STATUS_ERROR:
        parent.error_count = int(parent.error_count or 0) + 1
    elif child_status == AI_STATUS_SKIPPED:
        parent.skipped_count = int(parent.skipped_count or 0) + 1
        if child_reason in {"unchanged", "source_hash_unchanged"}:
            parent.skipped_unchanged_count = int(parent.skipped_unchanged_count or 0) + 1
        if child_reason in INELIGIBLE_REASONS:
            parent.skipped_ineligible_count = int(parent.skipped_ineligible_count or 0) + 1
    if parent.started_at is None:
        parent.started_at = datetime.now(timezone.utc)
    target_count = int(parent.target_count or 0)
    if target_count > 0 and parent.processed_count >= target_count and parent.finished_at is None:
        parent.finished_at = datetime.now(timezone.utc)
        parent.duration_ms = max(0, int((parent.finished_at - _coerce_utc(parent.started_at)).total_seconds() * 1000))
        if parent.error_count:
            parent.status = AI_STATUS_ERROR
            parent.reason = "partial_failures"
        else:
            parent.status = AI_STATUS_READY
            parent.reason = None
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


def _map_run_responses(db: Session, runs: list[AITaskRun]) -> list[AITaskRunResponse]:
    actor_ids = [run.actor_user_id for run in runs if run.actor_user_id]
    email_map = _load_user_emails(db, actor_ids)
    return [
        AITaskRunResponse(
            id=run.id,
            task_type=run.task_type,
            trigger_source=run.trigger_source,
            status=run.status,
            reason=run.reason,
            celery_task_id=run.celery_task_id,
            worker_name=run.worker_name,
            actor_user_id=run.actor_user_id,
            actor_email=email_map.get(run.actor_user_id),
            item_id=run.item_id,
            daily_brief_id=run.daily_brief_id,
            parent_run_id=run.parent_run_id,
            model=run.model,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            total_tokens=run.total_tokens,
            latency_ms=run.latency_ms,
            duration_ms=run.duration_ms,
            prompt_char_count=run.prompt_char_count,
            response_char_count=run.response_char_count,
            input_text_chars=run.input_text_chars,
            error=run.error,
            metadata=dict(run.metadata_json or {}),
            target_count=run.target_count,
            processed_count=int(run.processed_count or 0),
            success_count=int(run.success_count or 0),
            error_count=int(run.error_count or 0),
            skipped_count=int(run.skipped_count or 0),
            skipped_unchanged_count=int(run.skipped_unchanged_count or 0),
            skipped_ineligible_count=int(run.skipped_ineligible_count or 0),
            queued_at=run.queued_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]


def _map_audit_entries(db: Session, logs: list[AuditLog]) -> list[AIAuditEntryResponse]:
    actor_ids = [log.actor_user_id for log in logs if log.actor_user_id]
    email_map = _load_user_emails(db, actor_ids)
    return [
        AIAuditEntryResponse(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_email=email_map.get(log.actor_user_id),
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            success=bool(log.success),
            metadata=dict(log.metadata_json or {}),
            created_at=log.created_at,
        )
        for log in logs
    ]


def _load_user_emails(db: Session, actor_ids: list[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    unique_actor_ids = [actor_id for actor_id in {value for value in actor_ids if value}]
    if not unique_actor_ids:
        return {}
    rows = db.execute(select(User.id, User.email).where(User.id.in_(unique_actor_ids))).all()
    return {user_id: email for user_id, email in rows}


def _load_live_task_snapshot() -> tuple[list[str], list[AILiveTaskResponse], list[AILiveTaskResponse], list[AILiveTaskResponse]]:
    settings = get_settings()
    workers: list[str] = []
    active_tasks: list[AILiveTaskResponse] = []
    reserved_tasks: list[AILiveTaskResponse] = []
    scheduled_tasks: list[AILiveTaskResponse] = []
    try:
        inspector = celery_app.control.inspect(timeout=settings.health_worker_ping_timeout_seconds)
        ping = inspector.ping() or {}
        workers = sorted(ping.keys())
        active_raw = inspector.active() or {}
        reserved_raw = inspector.reserved() or {}
        scheduled_raw = inspector.scheduled() or {}
        active_tasks = _flatten_live_tasks(active_raw, state="active")
        reserved_tasks = _flatten_live_tasks(reserved_raw, state="reserved")
        scheduled_tasks = _flatten_live_tasks(scheduled_raw, state="scheduled")
    except Exception:
        workers = []
        active_tasks = []
        reserved_tasks = []
        scheduled_tasks = []
    return workers, active_tasks, reserved_tasks, scheduled_tasks


def _reconcile_stale_ai_runs(
    db: Session,
    *,
    workers: list[str] | None = None,
    active_tasks: list[AILiveTaskResponse] | None = None,
    reserved_tasks: list[AILiveTaskResponse] | None = None,
    scheduled_tasks: list[AILiveTaskResponse] | None = None,
) -> None:
    if workers is None or active_tasks is None or reserved_tasks is None or scheduled_tasks is None:
        workers, active_tasks, reserved_tasks, scheduled_tasks = _load_live_task_snapshot()

    _ = workers
    live_task_ids = {
        task.celery_task_id
        for task in [*active_tasks, *reserved_tasks, *scheduled_tasks]
        if task.celery_task_id
    }
    stale_before = datetime.now(timezone.utc) - STALE_AI_RUN_GRACE_PERIOD
    changed = False

    stale_child_runs = list(
        db.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.finished_at.is_(None),
                AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            )
            .order_by(AITaskRun.created_at.asc())
        )
    )
    for run in stale_child_runs:
        if not _is_stale_unfinished_run(run, live_task_ids, stale_before):
            continue
        finish_ai_task_run(
            db,
            run_id=run.id,
            status=AI_STATUS_ERROR,
            reason="stale_task_lost",
            error="Task no longer appears in Celery and did not report completion",
            worker_name=run.worker_name,
            model=run.model,
            metadata_updates={"stale_reconciled": True},
        )
        changed = True

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
            finish_ai_task_run(
                db,
                run_id=run.id,
                status=AI_STATUS_ERROR if int(run.error_count or 0) else AI_STATUS_READY,
                reason="partial_failures" if int(run.error_count or 0) else None,
                error=run.error,
                worker_name=run.worker_name,
                model=run.model,
                metadata_updates={"stale_reconciled": True},
            )
            changed = True
            continue
        if unfinished_child_count > 0 or not _is_stale_unfinished_run(run, live_task_ids, stale_before):
            continue
        finish_ai_task_run(
            db,
            run_id=run.id,
            status=AI_STATUS_ERROR,
            reason="stale_reprocess_tracking",
            error="Reprocess task stopped updating and is no longer active in Celery",
            worker_name=run.worker_name,
            model=run.model,
            metadata_updates={"stale_reconciled": True},
        )
        changed = True

    if changed:
        db.commit()


def _flatten_live_tasks(raw_tasks: dict[str, list[dict[str, Any]]], *, state: str) -> list[AILiveTaskResponse]:
    entries: list[AILiveTaskResponse] = []
    for worker_name, tasks in raw_tasks.items():
        for raw in tasks or []:
            name = raw.get("name")
            if name not in AI_TASK_NAMES:
                continue
            kwargs = raw.get("kwargs") or {}
            request = raw.get("request") or {}
            task_run_id = _extract_uuid(kwargs.get("task_run_id") or request.get("kwargs", {}).get("task_run_id"))
            item_id = _extract_uuid(kwargs.get("item_id"))
            parent_run_id = _extract_uuid(kwargs.get("parent_run_id"))
            eta_value = raw.get("eta")
            received_value = raw.get("time_start") or raw.get("received") or raw.get("acknowledged")
            entries.append(
                AILiveTaskResponse(
                    worker_name=worker_name,
                    celery_task_id=raw.get("id") or request.get("id"),
                    task_name=AI_TASK_NAMES[name],
                    state=state,
                    run_id=task_run_id,
                    item_id=item_id,
                    parent_run_id=parent_run_id,
                    eta=str(eta_value) if eta_value is not None else None,
                    received_at=str(received_value) if received_value is not None else None,
                    raw_name=name,
                )
            )
    return entries


def _is_stale_unfinished_run(run: AITaskRun, live_task_ids: set[str], stale_before: datetime) -> bool:
    if run.celery_task_id and run.celery_task_id in live_task_ids:
        return False
    reference = run.updated_at or run.started_at or run.queued_at or run.created_at
    return _coerce_utc(reference) < stale_before


def _build_per_model_usage(events: list[AIUsageEvent]) -> list[AIOverviewPerModelResponse]:
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        key = event.model or "unknown"
        bucket = buckets.setdefault(
            key,
            {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "total_tokens": 0,
                "latencies": [],
                "last_request_at": None,
            },
        )
        bucket["total_requests"] += 1
        bucket["successful_requests"] += 1 if event.success else 0
        bucket["failed_requests"] += 0 if event.success else 1
        bucket["total_tokens"] += int(event.total_tokens or 0)
        if event.latency_ms is not None:
            bucket["latencies"].append(float(event.latency_ms))
        if bucket["last_request_at"] is None or (event.created_at and event.created_at > bucket["last_request_at"]):
            bucket["last_request_at"] = event.created_at
    results: list[AIOverviewPerModelResponse] = []
    for model, bucket in buckets.items():
        total_requests = int(bucket["total_requests"])
        results.append(
            AIOverviewPerModelResponse(
                model=model,
                total_requests=total_requests,
                successful_requests=int(bucket["successful_requests"]),
                failed_requests=int(bucket["failed_requests"]),
                success_rate_pct=round((bucket["successful_requests"] / total_requests * 100.0) if total_requests else 0.0, 2),
                total_tokens=int(bucket["total_tokens"]),
                average_latency_ms=round(sum(bucket["latencies"]) / len(bucket["latencies"]), 2) if bucket["latencies"] else 0.0,
                last_request_at=bucket["last_request_at"],
            )
        )
    return sorted(results, key=lambda entry: entry.total_tokens, reverse=True)


def _build_time_series(
    events: list[AIUsageEvent],
    db: Session,
    *,
    since: datetime,
    now: datetime,
) -> list[AITimeSeriesPointResponse]:
    buckets: dict[str, dict[str, Any]] = {}
    cursor = since.date()
    while cursor <= now.date():
        key = cursor.isoformat()
        buckets[key] = {
            "requests": 0,
            "failures": 0,
            "total_tokens": 0,
            "latencies": [],
            "daily_brief_successes": 0,
            "daily_brief_failures": 0,
            "daily_brief_skips": 0,
        }
        cursor += timedelta(days=1)

    for event in events:
        bucket_key = _coerce_utc(event.created_at).date().isoformat()
        bucket = buckets.setdefault(
            bucket_key,
            {
                "requests": 0,
                "failures": 0,
                "total_tokens": 0,
                "latencies": [],
                "daily_brief_successes": 0,
                "daily_brief_failures": 0,
                "daily_brief_skips": 0,
            },
        )
        bucket["requests"] += 1
        bucket["failures"] += 0 if event.success else 1
        bucket["total_tokens"] += int(event.total_tokens or 0)
        if event.latency_ms is not None:
            bucket["latencies"].append(float(event.latency_ms))

    daily_runs = list(
        db.scalars(
            select(AITaskRun).where(
                AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
                AITaskRun.created_at >= since,
            )
        )
    )
    for run in daily_runs:
        bucket_key = _coerce_utc(run.created_at).date().isoformat()
        bucket = buckets.setdefault(
            bucket_key,
            {
                "requests": 0,
                "failures": 0,
                "total_tokens": 0,
                "latencies": [],
                "daily_brief_successes": 0,
                "daily_brief_failures": 0,
                "daily_brief_skips": 0,
            },
        )
        if run.status == AI_STATUS_READY:
            bucket["daily_brief_successes"] += 1
        elif run.status == AI_STATUS_ERROR:
            bucket["daily_brief_failures"] += 1
        elif run.status == AI_STATUS_SKIPPED:
            bucket["daily_brief_skips"] += 1

    return [
        AITimeSeriesPointResponse(
            bucket=key,
            requests=int(value["requests"]),
            failures=int(value["failures"]),
            total_tokens=int(value["total_tokens"]),
            average_latency_ms=round(sum(value["latencies"]) / len(value["latencies"]), 2) if value["latencies"] else 0.0,
            p95_latency_ms=round(_percentile(value["latencies"], 0.95), 2) if value["latencies"] else 0.0,
            daily_brief_successes=int(value["daily_brief_successes"]),
            daily_brief_failures=int(value["daily_brief_failures"]),
            daily_brief_skips=int(value["daily_brief_skips"]),
        )
        for key, value in sorted(buckets.items())
    ]


def _build_token_efficiency(events: list[AIUsageEvent]) -> AITokenEfficiencyResponse:
    prompt_tokens = [int(event.prompt_tokens or 0) for event in events if event.prompt_tokens is not None]
    completion_tokens = [int(event.completion_tokens or 0) for event in events if event.completion_tokens is not None]
    total_tokens = [int(event.total_tokens or 0) for event in events if event.total_tokens is not None]
    by_feature: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if event.total_tokens is not None:
            by_feature[event.feature_type].append(int(event.total_tokens))
    top_feature = None
    top_feature_avg = 0.0
    for feature, values in by_feature.items():
        avg = sum(values) / len(values)
        if avg > top_feature_avg:
            top_feature = feature
            top_feature_avg = avg
    avg_prompt = sum(prompt_tokens) / len(prompt_tokens) if prompt_tokens else 0.0
    avg_completion = sum(completion_tokens) / len(completion_tokens) if completion_tokens else 0.0
    return AITokenEfficiencyResponse(
        average_prompt_tokens=round(avg_prompt, 2),
        average_completion_tokens=round(avg_completion, 2),
        average_total_tokens=round(sum(total_tokens) / len(total_tokens), 2) if total_tokens else 0.0,
        prompt_to_completion_ratio=round(avg_prompt / avg_completion, 2) if avg_completion else 0.0,
        top_expensive_feature=top_feature,
        top_expensive_feature_avg_tokens=round(top_feature_avg, 2),
    )


def _build_relevance_distribution(db: Session) -> AIRelevanceDistributionResponse:
    enrichments = list(
        db.execute(
            select(
                ItemAIEnrichment.relevance_label,
                ItemAIEnrichment.relevance_score,
                Feed.name,
            )
            .join(Item, Item.id == ItemAIEnrichment.item_id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(ItemAIEnrichment.status == AI_STATUS_READY, ItemAIEnrichment.relevance_label.is_not(None))
        )
    )
    high_count = medium_count = low_count = 0
    total_score = 0.0
    score_count = 0
    by_feed: dict[str, dict[str, Any]] = {}
    for label, score, feed_name in enrichments:
        if label == "high":
            high_count += 1
        elif label == "medium":
            medium_count += 1
        elif label == "low":
            low_count += 1
        if score is not None:
            total_score += float(score)
            score_count += 1
        bucket = by_feed.setdefault(
            feed_name,
            {"total_items": 0, "high_count": 0, "medium_count": 0, "low_count": 0, "score_total": 0.0, "score_count": 0},
        )
        bucket["total_items"] += 1
        if label == "high":
            bucket["high_count"] += 1
        elif label == "medium":
            bucket["medium_count"] += 1
        elif label == "low":
            bucket["low_count"] += 1
        if score is not None:
            bucket["score_total"] += float(score)
            bucket["score_count"] += 1
    feed_rows = [
        AIRelevanceFeedResponse(
            feed_name=feed_name,
            total_items=int(bucket["total_items"]),
            high_count=int(bucket["high_count"]),
            medium_count=int(bucket["medium_count"]),
            low_count=int(bucket["low_count"]),
            average_score=round(bucket["score_total"] / bucket["score_count"], 3) if bucket["score_count"] else 0.0,
        )
        for feed_name, bucket in sorted(by_feed.items(), key=lambda entry: entry[1]["total_items"], reverse=True)[:10]
    ]
    return AIRelevanceDistributionResponse(
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
        average_score=round(total_score / score_count, 3) if score_count else 0.0,
        by_feed=feed_rows,
    )


def _build_coverage_stats(db: Session) -> AICoverageStatsResponse:
    from app.models.article import Article  # local import to keep service dependencies light

    eligible_items = int(
        db.scalar(
            select(func.count(Item.id))
            .join(Article, Article.item_id == Item.id)
            .where(Article.text.is_not(None))
        )
        or 0
    )
    enriched_items = int(
        db.scalar(select(func.count(ItemAIEnrichment.item_id)).where(ItemAIEnrichment.status == AI_STATUS_READY)) or 0
    )
    pending_items = int(
        db.scalar(select(func.count(ItemAIEnrichment.item_id)).where(ItemAIEnrichment.status == "pending")) or 0
    )
    failed_items = int(
        db.scalar(select(func.count(ItemAIEnrichment.item_id)).where(ItemAIEnrichment.status == AI_STATUS_ERROR)) or 0
    )
    oldest_pending_at = db.scalar(
        select(ItemAIEnrichment.generated_at)
        .where(ItemAIEnrichment.status == "pending")
        .order_by(ItemAIEnrichment.generated_at.asc())
    )
    last_successful_enrichment_at = db.scalar(
        select(ItemAIEnrichment.generated_at)
        .where(ItemAIEnrichment.status == AI_STATUS_READY)
        .order_by(ItemAIEnrichment.generated_at.desc())
    )
    last_successful_daily_brief_at = db.scalar(
        select(AIDailyBrief.generated_at)
        .where(AIDailyBrief.status == AI_STATUS_READY)
        .order_by(AIDailyBrief.generated_at.desc())
    )
    last_ai_run_at = db.scalar(select(AITaskRun.finished_at).order_by(AITaskRun.finished_at.desc()))
    skip_counts = _load_skip_counts(db)
    return AICoverageStatsResponse(
        eligible_items=eligible_items,
        enriched_items=enriched_items,
        pending_items=pending_items,
        failed_items=failed_items,
        skipped_no_article_count=int(skip_counts.get("no_article", 0) + skip_counts.get("no_article_text", 0)),
        skipped_ai_disabled_count=int(skip_counts.get("ai_disabled", 0)),
        skipped_not_configured_count=int(skip_counts.get("ai_not_configured", 0)),
        skipped_auto_enrich_disabled_count=int(skip_counts.get("auto_enrich_disabled", 0)),
        skipped_unchanged_count=int(skip_counts.get("unchanged", 0) + skip_counts.get("source_hash_unchanged", 0)),
        oldest_pending_at=oldest_pending_at,
        last_successful_enrichment_at=last_successful_enrichment_at,
        last_successful_daily_brief_at=last_successful_daily_brief_at,
        last_ai_run_at=last_ai_run_at,
    )


def _load_skip_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(AITaskRun.reason, func.count(AITaskRun.id))
        .where(AITaskRun.status == AI_STATUS_SKIPPED, AITaskRun.reason.is_not(None))
        .group_by(AITaskRun.reason)
    ).all()
    return {reason: int(count) for reason, count in rows if reason}


def _build_endpoint_health(events: list[AIUsageEvent]) -> AIEndpointHealthResponse:
    successful = [event for event in events if event.success]
    failed = [event for event in events if not event.success]
    last_success_at = max((event.created_at for event in successful), default=None)
    last_error_event = max(failed, key=lambda event: event.created_at or datetime.min.replace(tzinfo=timezone.utc), default=None)
    recent_window = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = [event for event in events if _coerce_utc(event.created_at) >= recent_window]
    median_latency_ms = round(median([event.latency_ms for event in successful if event.latency_ms is not None]), 2) if successful else 0.0
    timeout_failures = sum(1 for event in failed if "timeout" in (event.error or "").lower())
    last_auth_error = next(
        (
            event.error
            for event in sorted(failed, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            if _looks_like_auth_error(event.error)
        ),
        None,
    )
    return AIEndpointHealthResponse(
        last_success_at=last_success_at,
        last_error_at=last_error_event.created_at if last_error_event else None,
        rolling_failure_rate_pct=round((sum(1 for event in recent if not event.success) / len(recent) * 100.0), 2) if recent else 0.0,
        median_latency_ms=median_latency_ms,
        timeout_failures=timeout_failures,
        last_auth_error=last_auth_error,
        last_provider_error=last_error_event.error if last_error_event else None,
    )


def _build_feature_health(db: Session) -> list[AIFeatureHealthRowResponse]:
    settings = db.scalar(select(AISettings).limit(1))
    enabled = {
        "summaries": bool(settings.summary_enabled) if settings else False,
        "relevance": bool(settings.relevance_enabled) if settings else False,
        "daily_brief": bool(settings.daily_brief_enabled) if settings else False,
        "auto_enrichment": bool(settings.auto_enrich_new_items) if settings else False,
    }
    feature_to_filters: dict[str, Select[Any]] = {
        "summaries": select(AITaskRun).where(AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT),
        "relevance": select(AITaskRun).where(AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT),
        "daily_brief": select(AITaskRun).where(AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF),
        "auto_enrichment": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.trigger_source == AI_TRIGGER_AUTO,
        ),
    }
    rows: list[AIFeatureHealthRowResponse] = []
    for feature_key, query in feature_to_filters.items():
        last_run = db.scalar(query.order_by(AITaskRun.created_at.desc()))
        last_success = db.scalar(query.where(AITaskRun.status == AI_STATUS_READY).order_by(AITaskRun.finished_at.desc()))
        last_failure = db.scalar(query.where(AITaskRun.status == AI_STATUS_ERROR).order_by(AITaskRun.finished_at.desc()))
        rows.append(
            AIFeatureHealthRowResponse(
                feature_key=feature_key,
                enabled=enabled[feature_key],
                last_run_at=last_run.created_at if last_run else None,
                last_success_at=last_success.finished_at if last_success else None,
                last_failure_at=last_failure.finished_at if last_failure else None,
                last_status=last_run.status if last_run else None,
            )
        )
    return rows


def _build_storage_stats(db: Session) -> AIStorageStatsResponse:
    settings = db.scalar(select(AISettings).limit(1))
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    return AIStorageStatsResponse(
        retained_daily_briefs=int(db.scalar(select(func.count(AIDailyBrief.id))) or 0),
        daily_brief_history_limit=int(settings.daily_brief_history_limit) if settings else 0,
        enrichment_rows=int(db.scalar(select(func.count(ItemAIEnrichment.item_id))) or 0),
        usage_event_rows=int(db.scalar(select(func.count(AIUsageEvent.id))) or 0),
        task_history_rows=int(db.scalar(select(func.count(AITaskRun.id))) or 0),
        growth_last_7d=int(db.scalar(select(func.count(AITaskRun.id)).where(AITaskRun.created_at >= seven_days_ago)) or 0),
        growth_last_30d=int(db.scalar(select(func.count(AITaskRun.id)).where(AITaskRun.created_at >= thirty_days_ago)) or 0),
    )


def _build_cache_stats(db: Session) -> AICacheStatsResponse:
    reused_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status == AI_STATUS_SKIPPED,
                AITaskRun.reason.in_(["unchanged", "source_hash_unchanged"]),
            )
        )
        or 0
    )
    recomputed_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status == AI_STATUS_READY,
            )
        )
        or 0
    )
    denominator = reused_count + recomputed_count
    return AICacheStatsResponse(
        reused_count=reused_count,
        recomputed_count=recomputed_count,
        no_op_rate_pct=round((reused_count / denominator * 100.0), 2) if denominator else 0.0,
    )


def _normalize_error_text(value: str | None) -> str:
    if not value:
        return "unknown_error"
    normalized = value.strip()
    if len(normalized) > 200:
        normalized = normalized[:197] + "..."
    return normalized


def _looks_like_auth_error(value: str | None) -> bool:
    lowered = (value or "").lower()
    return any(fragment in lowered for fragment in ["401", "403", "unauthorized", "forbidden", "auth"])


def _extract_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _merge_metadata(current: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in updates.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio))))
    return float(ordered[index])


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
