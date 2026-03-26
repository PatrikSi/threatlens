import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_current_user, require_token_scopes
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_AI, SCOPE_READ_ITEMS, SCOPE_WRITE_AI
from app.db.session import get_db
from app.models.user import User
from app.schemas.ai import (
    AIDailyBriefResponse,
    AIReprocessRequest,
    AIReprocessResponse,
    AISettingsResponse,
    AISettingsUpdate,
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
    generate_daily_brief,
    get_ai_usage_summary,
    get_latest_daily_brief,
    get_recent_daily_briefs,
    prune_daily_brief_history,
    test_ai_connection,
)
from app.services.audit import record_audit
from app.tasks.feed_tasks import reprocess_recent_ai_items

router = APIRouter(prefix="/ai", tags=["ai"])


def require_ai_enabled():
    if not get_settings().ai_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI features are disabled")


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
    apply_ai_settings_update(settings, payload)
    db.add(settings)
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
            "daily_brief_history_limit": payload.daily_brief_history_limit,
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
    try:
        result = test_ai_connection(db)
    except AIIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.connection.test",
        resource_type="ai_settings",
        success=result.success,
        metadata={"model": result.model, "latency_ms": result.latency_ms},
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
    brief = generate_daily_brief(db, force=True)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No items are available for a daily brief")

    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.daily_brief.generate",
        resource_type="ai_daily_brief",
        resource_id=str(brief.id),
        success=brief.status == "ready",
        metadata={"brief_date": brief.brief_date.isoformat(), "status": brief.status},
    )
    db.commit()
    db.refresh(brief)
    return daily_brief_response_from_model(db, brief)


@router.post("/reprocess", response_model=AIReprocessResponse, dependencies=[Depends(require_ai_enabled)])
def reprocess_ai_for_recent_items_route(
    payload: AIReprocessRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    task = reprocess_recent_ai_items.delay(payload.days, payload.limit)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.reprocess.queue",
        resource_type="ai_settings",
        metadata={"days": payload.days, "limit": payload.limit, "task_id": task.id},
    )
    db.commit()
    return AIReprocessResponse(task_id=task.id, queued=True)
