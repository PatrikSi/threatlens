import uuid
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_current_user, require_token_scopes
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_AI, SCOPE_READ_ITEMS, SCOPE_WRITE_AI
from app.db.session import get_db
from app.models.user import User
from app.schemas.ai import (
    AIAuditEntryResponse,
    AIDailyBriefResponse,
    AIDailyBriefSourceItemResponse,
    AILiveStatusResponse,
    AIOpsOverviewResponse,
    AIQueuedTaskResponse,
    AIReprocessRequest,
    AIReprocessResponse,
    AISettingsResponse,
    AISettingsUpdate,
    AITaskRunResponse,
    AITaskRunDetailResponse,
    AITaskRunListResponse,
    AITestConnectionResponse,
    AIUsageSummaryResponse,
)
from app.services.ai_config import (
    ai_settings_response_from_model,
    apply_ai_settings_update,
    get_or_create_ai_settings,
    load_active_ai_settings,
)
from app.services.ai_integration import (
    AIIntegrationError,
    daily_brief_response_from_model,
    get_ai_usage_summary,
    get_latest_daily_brief,
    get_recent_daily_briefs,
    prune_daily_brief_history,
    run_daily_brief_generation,
    test_ai_connection,
)
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_READY,
    AI_STATUS_SKIPPED,
    AI_TASK_TYPE_CONNECTION_TEST,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
    cancel_ai_task_run,
    finish_ai_task_run,
    get_ai_live_status,
    get_ai_ops_overview,
    get_ai_task_run_detail,
    list_ai_manual_actions,
    list_ai_prompt_history,
    list_ai_task_runs,
    list_daily_brief_source_items,
    queue_ai_task_run,
    start_ai_task_run,
    update_ai_task_run_celery,
)
from app.services.audit import record_audit
from app.tasks.feed_tasks import CoordinationUnavailableError, daily_ai_brief_lock
from app.tasks.feed_tasks import dispatch_daily_ai_brief_generation, reprocess_recent_ai_items

router = APIRouter(prefix="/ai", tags=["ai"])


def require_ai_enabled():
    if not get_settings().ai_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI features are disabled")


def _hash_prompt(value: str | None) -> str | None:
    if value is None:
        return None
    return sha256(value.encode("utf-8", errors="ignore")).hexdigest()


@router.get("/settings", response_model=AISettingsResponse, dependencies=[Depends(require_ai_enabled)])
def get_ai_settings_route(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    settings = get_or_create_ai_settings(db)
    return ai_settings_response_from_model(settings)


@router.put("/settings", response_model=AISettingsResponse, dependencies=[Depends(require_ai_enabled)])
def update_ai_settings_route(
    payload: AISettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    settings = get_or_create_ai_settings(db)
    before_values = {
        "base_url": settings.base_url,
        "model": settings.model,
        "summary_enabled": settings.summary_enabled,
        "relevance_enabled": settings.relevance_enabled,
        "daily_brief_enabled": settings.daily_brief_enabled,
        "auto_enrich_new_items": settings.auto_enrich_new_items,
        "daily_brief_window_hours": settings.daily_brief_window_hours,
        "daily_brief_max_items": settings.daily_brief_max_items,
        "daily_brief_history_limit": settings.daily_brief_history_limit,
        "daily_brief_schedule_hour_utc": settings.daily_brief_schedule_hour_utc,
        "daily_brief_schedule_minute_utc": settings.daily_brief_schedule_minute_utc,
        "temperature": settings.temperature,
        "max_completion_tokens": settings.max_completion_tokens,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "request_max_retries": settings.request_max_retries,
        "relevance_medium_threshold": settings.relevance_medium_threshold,
        "relevance_high_threshold": settings.relevance_high_threshold,
        "item_enrichment_system_prompt": settings.item_enrichment_system_prompt,
        "daily_brief_system_prompt": settings.daily_brief_system_prompt,
        "global_instructions": settings.global_instructions,
        "item_summary_instructions": settings.item_summary_instructions,
        "relevance_instructions": settings.relevance_instructions,
        "daily_brief_instructions": settings.daily_brief_instructions,
    }
    apply_ai_settings_update(settings, payload)
    db.add(settings)
    after_changed_fields = [
        field_name
        for field_name in (
            "base_url",
            "model",
            "summary_enabled",
            "relevance_enabled",
            "daily_brief_enabled",
            "auto_enrich_new_items",
            "daily_brief_window_hours",
            "daily_brief_max_items",
            "daily_brief_history_limit",
            "daily_brief_schedule_hour_utc",
            "daily_brief_schedule_minute_utc",
            "temperature",
            "max_completion_tokens",
            "request_timeout_seconds",
            "request_max_retries",
            "relevance_medium_threshold",
            "relevance_high_threshold",
            "item_enrichment_system_prompt",
            "daily_brief_system_prompt",
            "global_instructions",
            "item_summary_instructions",
            "relevance_instructions",
            "daily_brief_instructions",
        )
        if before_values[field_name] != getattr(payload, field_name)
    ]
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.settings.update",
        resource_type="ai_settings",
        resource_id=str(settings.id),
        metadata={
            "base_url": payload.base_url,
            "model": payload.model,
            "summary_enabled": payload.summary_enabled,
            "relevance_enabled": payload.relevance_enabled,
            "daily_brief_enabled": payload.daily_brief_enabled,
            "auto_enrich_new_items": payload.auto_enrich_new_items,
            "daily_brief_window_hours": payload.daily_brief_window_hours,
            "daily_brief_max_items": payload.daily_brief_max_items,
            "daily_brief_history_limit": payload.daily_brief_history_limit,
            "daily_brief_schedule_hour_utc": payload.daily_brief_schedule_hour_utc,
            "daily_brief_schedule_minute_utc": payload.daily_brief_schedule_minute_utc,
            "request_max_retries": payload.request_max_retries,
            "changed_fields": after_changed_fields,
            "prompt_hashes": {
                "item_enrichment_system_prompt": _hash_prompt(payload.item_enrichment_system_prompt),
                "daily_brief_system_prompt": _hash_prompt(payload.daily_brief_system_prompt),
                "global_instructions": _hash_prompt(payload.global_instructions),
                "item_summary_instructions": _hash_prompt(payload.item_summary_instructions),
                "relevance_instructions": _hash_prompt(payload.relevance_instructions),
                "daily_brief_instructions": _hash_prompt(payload.daily_brief_instructions),
            },
        },
    )
    prune_daily_brief_history(db, keep_limit=payload.daily_brief_history_limit)
    db.commit()
    db.refresh(settings)
    return ai_settings_response_from_model(settings)


@router.post("/test-connection", response_model=AITestConnectionResponse, dependencies=[Depends(require_ai_enabled)])
def test_ai_connection_route(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    settings = get_or_create_ai_settings(db)
    run = queue_ai_task_run(
        db,
        task_type=AI_TASK_TYPE_CONNECTION_TEST,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=admin.id,
        model=settings.model,
        metadata={"base_url": settings.base_url, "model": settings.model},
    )
    start_ai_task_run(db, run_id=run.id, worker_name="api")
    db.commit()
    try:
        result = test_ai_connection(db, task_run_id=run.id)
    except AIIntegrationError as exc:
        finish_ai_task_run(
            db,
            run_id=run.id,
            status=AI_STATUS_ERROR,
            reason="request_failed",
            error=str(exc),
            worker_name="api",
            model=settings.model,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    finish_ai_task_run(
        db,
        run_id=run.id,
        status=AI_STATUS_READY if result.success else AI_STATUS_ERROR,
        reason=None if result.success else "unexpected_response",
        error=result.error,
        worker_name="api",
        model=result.model,
        latency_ms=result.latency_ms,
    )
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.connection.test",
        resource_type="ai_settings",
        success=result.success,
        metadata={"model": result.model, "latency_ms": result.latency_ms, "run_id": str(run.id)},
    )
    db.commit()
    return result


@router.get("/usage", response_model=AIUsageSummaryResponse, dependencies=[Depends(require_ai_enabled)])
def get_ai_usage_route(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    return get_ai_usage_summary(db)


@router.get("/daily-brief/latest", response_model=AIDailyBriefResponse, dependencies=[Depends(require_ai_enabled)])
def get_latest_daily_brief_route(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    active = load_active_ai_settings(db)
    if not active.ai_configured or not active.daily_brief_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Daily brief is unavailable")

    brief = get_latest_daily_brief(db)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No daily brief has been generated yet")
    return daily_brief_response_from_model(db, brief)


@router.get("/daily-briefs", response_model=list[AIDailyBriefResponse], dependencies=[Depends(require_ai_enabled)])
def list_daily_briefs_route(
    limit: int | None = Query(default=None, ge=1, le=90),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    active = load_active_ai_settings(db)
    if not active.ai_configured or not active.daily_brief_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Daily brief is unavailable")

    effective_limit = limit or active.daily_brief_history_limit
    briefs = get_recent_daily_briefs(db, limit=effective_limit)
    return [daily_brief_response_from_model(db, brief) for brief in briefs]


@router.post("/daily-brief/generate", response_model=AIDailyBriefResponse, dependencies=[Depends(require_ai_enabled)])
def generate_daily_brief_route(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    settings = get_or_create_ai_settings(db)
    run = queue_ai_task_run(
        db,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=admin.id,
        model=settings.model,
        metadata={"force": True},
    )
    start_ai_task_run(db, run_id=run.id, worker_name="api", metadata_updates={"force": True})
    db.commit()

    try:
        with daily_ai_brief_lock() as acquired:
            if not acquired:
                finish_ai_task_run(
                    db,
                    run_id=run.id,
                    status=AI_STATUS_SKIPPED,
                    reason="already_running",
                    worker_name="api",
                    model=settings.model,
                    metadata_updates={"force": True},
                )
                db.commit()
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Daily brief is already running")

            result = run_daily_brief_generation(db, force=True, task_run_id=run.id)
    except CoordinationUnavailableError as exc:
        finish_ai_task_run(
            db,
            run_id=run.id,
            status=AI_STATUS_ERROR,
            reason="coordination_unavailable",
            error=str(exc),
            worker_name="api",
            model=settings.model,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Task queue is temporarily unavailable. Try again later.") from exc

    finish_ai_task_run(
        db,
        run_id=run.id,
        status=AI_STATUS_READY if result.status == "ready" else AI_STATUS_ERROR if result.status == "error" else AI_STATUS_SKIPPED,
        reason=result.reason,
        error=result.brief.error if result.brief is not None and result.status == "error" else None,
        worker_name="api",
        model=result.brief.model if result.brief is not None else settings.model,
        prompt_tokens=result.brief.prompt_tokens if result.brief is not None else None,
        completion_tokens=result.brief.completion_tokens if result.brief is not None else None,
        total_tokens=result.brief.total_tokens if result.brief is not None else None,
        latency_ms=result.brief.latency_ms if result.brief is not None else None,
        prompt_char_count=result.prompt_char_count,
        response_char_count=result.response_char_count,
        metadata_updates={"items_considered": result.items_considered, "items_selected": result.items_selected},
        daily_brief_id=result.brief.id if result.brief is not None else None,
    )
    if result.brief is None:
        db.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No items are available for a daily brief")

    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.daily_brief.generate",
        resource_type="ai_daily_brief",
        resource_id=str(result.brief.id),
        success=result.brief.status == "ready",
        metadata={
            "brief_date": result.brief.brief_date.isoformat(),
            "status": result.brief.status,
            "run_id": str(run.id),
            "items_considered": result.items_considered,
            "items_selected": result.items_selected,
        },
    )
    db.commit()
    db.refresh(result.brief)
    return daily_brief_response_from_model(db, result.brief)


@router.post("/daily-brief/queue", response_model=AIQueuedTaskResponse, dependencies=[Depends(require_ai_enabled)])
def queue_daily_brief_route(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    settings = get_or_create_ai_settings(db)
    run = queue_ai_task_run(
        db,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=admin.id,
        model=settings.model,
        metadata={"force": True, "queued_by": "api"},
    )
    db.commit()
    task = _enqueue_task_run_or_fail(
        db,
        run_id=run.id,
        task_factory=lambda: dispatch_daily_ai_brief_generation.delay(True, str(run.id), str(admin.id)),
    )
    task_id = getattr(task, "id", None)
    update_ai_task_run_celery(db, run_id=run.id, celery_task_id=task_id)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.daily_brief.queue",
        resource_type="ai_daily_brief",
        success=True,
        metadata={"task_id": task_id, "run_id": str(run.id)},
    )
    db.commit()
    return AIQueuedTaskResponse(task_id=task_id, queued=True, run_id=run.id)


@router.post("/reprocess", response_model=AIReprocessResponse, dependencies=[Depends(require_ai_enabled)])
def reprocess_ai_for_recent_items_route(
    payload: AIReprocessRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    settings = get_or_create_ai_settings(db)
    run = queue_ai_task_run(
        db,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=admin.id,
        model=settings.model,
        metadata={
            "days": payload.days,
            "limit": payload.limit,
            "start_time": payload.start_time.isoformat() if payload.start_time else None,
            "end_time": payload.end_time.isoformat() if payload.end_time else None,
            "feed_ids": [str(feed_id) for feed_id in payload.feed_ids],
            "item_ids": [str(item_id) for item_id in payload.item_ids],
        },
    )
    db.commit()
    task = _enqueue_task_run_or_fail(
        db,
        run_id=run.id,
        task_factory=lambda: reprocess_recent_ai_items.delay(
            payload.days,
            payload.limit,
            payload.start_time.isoformat() if payload.start_time else None,
            payload.end_time.isoformat() if payload.end_time else None,
            [str(feed_id) for feed_id in payload.feed_ids],
            [str(item_id) for item_id in payload.item_ids],
            task_run_id=str(run.id),
            actor_user_id=str(admin.id),
        ),
    )
    task_id = getattr(task, "id", None)
    update_ai_task_run_celery(db, run_id=run.id, celery_task_id=task_id)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.reprocess.queue",
        resource_type="ai_settings",
        metadata={
            "days": payload.days,
            "limit": payload.limit,
            "start_time": payload.start_time.isoformat() if payload.start_time else None,
            "end_time": payload.end_time.isoformat() if payload.end_time else None,
            "feed_ids": [str(feed_id) for feed_id in payload.feed_ids],
            "item_ids": [str(item_id) for item_id in payload.item_ids],
            "task_id": task_id,
            "run_id": str(run.id),
        },
    )
    db.commit()
    return AIReprocessResponse(task_id=task_id, queued=True, run_id=run.id)


def _enqueue_task_run_or_fail(db: Session, *, run_id: uuid.UUID, task_factory):
    try:
        return task_factory()
    except Exception as exc:
        finish_ai_task_run(
            db,
            run_id=run_id,
            status=AI_STATUS_ERROR,
            reason="enqueue_failed",
            error=str(exc),
            worker_name="api",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is temporarily unavailable. Try again later.",
        ) from exc


@router.get("/ops/overview", response_model=AIOpsOverviewResponse, dependencies=[Depends(require_ai_enabled)])
def get_ai_ops_overview_route(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    return get_ai_ops_overview(db, days=days)


@router.get("/ops/live", response_model=AILiveStatusResponse, dependencies=[Depends(require_ai_enabled)])
def get_ai_ops_live_route(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = (db, admin)
    return get_ai_live_status(db)


@router.get("/ops/runs", response_model=AITaskRunListResponse, dependencies=[Depends(require_ai_enabled)])
def list_ai_ops_runs_route(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    days: int | None = Query(default=None, ge=1, le=365),
    task_type: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    trigger_source: str | None = Query(default=None),
    model: str | None = Query(default=None),
    parent_run_id: uuid.UUID | None = Query(default=None),
    only_failures: bool = Query(default=False),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    since = None
    if days is not None:
        from datetime import datetime, timedelta, timezone

        since = datetime.now(timezone.utc) - timedelta(days=days)
    return list_ai_task_runs(
        db,
        limit=limit,
        offset=offset,
        task_type=task_type,
        status=status_value,
        trigger_source=trigger_source,
        model=model,
        since=since,
        parent_run_id=parent_run_id,
        only_failures=only_failures,
    )


@router.get("/ops/runs/{run_id}", response_model=AITaskRunDetailResponse, dependencies=[Depends(require_ai_enabled)])
def get_ai_ops_run_detail_route(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    detail = get_ai_task_run_detail(db, run_id=run_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI task run not found")
    return detail


@router.post("/ops/runs/{run_id}/cancel", response_model=AITaskRunResponse, dependencies=[Depends(require_ai_enabled)])
def cancel_ai_ops_run_route(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    run = cancel_ai_task_run(db, run_id=run_id, actor_user_id=admin.id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI task run not found")
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.run.cancel",
        resource_type="ai_task_run",
        resource_id=str(run.id),
        success=True,
        metadata={"task_type": run.task_type, "status": run.status, "reason": run.reason},
    )
    db.commit()
    detail = get_ai_task_run_detail(db, run_id=run.id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI task run not found")
    return detail.run


@router.get("/ops/manual-actions", response_model=list[AIAuditEntryResponse], dependencies=[Depends(require_ai_enabled)])
def list_ai_ops_manual_actions_route(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    return list_ai_manual_actions(db, limit=limit)


@router.get("/ops/prompt-history", response_model=list[AIAuditEntryResponse], dependencies=[Depends(require_ai_enabled)])
def list_ai_ops_prompt_history_route(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    return list_ai_prompt_history(db, limit=limit)


@router.get("/daily-briefs/{brief_id}/sources", response_model=list[AIDailyBriefSourceItemResponse], dependencies=[Depends(require_ai_enabled)])
def list_daily_brief_sources_route(
    brief_id: uuid.UUID,
    included: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    _ = admin
    sources = list_daily_brief_source_items(db, daily_brief_id=brief_id, included=included, limit=limit)
    if sources is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Daily brief not found")
    return sources
