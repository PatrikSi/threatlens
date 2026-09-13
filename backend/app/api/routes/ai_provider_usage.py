from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_data_access_context, require_token_scopes
from app.api.routes.ai_policy_helpers import record_ai_telemetry_would_deny, refence_ai_context, require_ai_authorization_context
from app.api.routes.ai_route_helpers import require_ai_enabled
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_AI
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from app.db.session import get_db
from app.models.ai_usage_event import AIUsageEvent
from app.models.ai_task_run import AITaskRun
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.user import User
from app.schemas.ai_provider_usage import AIProviderUsageResponse
from app.schemas.ai_statistics import AIStatisticsResponse
from app.services.ai_provider_usage import list_provider_usage
from app.services.ai_statistics import build_ai_statistics
from app.services.ai_telemetry_data_policy import ai_task_run_would_deny_summary, ai_usage_event_would_deny_summary
from app.services.data_access_policy import DataAccessContext

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_ai_enabled)])


@router.get("/ops/statistics", response_model=AIStatisticsResponse)
def get_ai_statistics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_READ_AI)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    authorization = require_ai_authorization_context(request)
    try:
        with database_operation(db, operation="interactive"):
            response = build_ai_statistics(db, days=days, data_access=data_access)
            refence_ai_context(db, authorization=authorization, data_access=data_access)
            usage_summary = ai_usage_event_would_deny_summary(db, data_access=data_access, filters=(
                AIUsageEvent.created_at >= response.since, AIUsageEvent.created_at < response.until,
            ))
            receipt_runs = select(AIProviderAttemptReceipt.task_run_id_snapshot).where(
                AIProviderAttemptReceipt.created_at >= response.since,
                AIProviderAttemptReceipt.created_at < response.until,
            )
            run_summary = ai_task_run_would_deny_summary(db, data_access=data_access, filters=(or_(
                AITaskRun.status.in_(("queued", "running")), AITaskRun.id.in_(receipt_runs),
            ),))
        for summary, resource in ((usage_summary, "ai_usage_event"), (run_summary, "ai_task_run")):
            record_ai_telemetry_would_deny(request, db, data_access=data_access, summary=summary,
                surface="ai.ops.statistics.read", resource_type=resource, history_scope="statistics")
        return response
    except (DatabaseDeadlineExceeded, OperationalError, PoolTimeoutError) as exc:
        raise ApiHTTPException(
            status_code=503, error_code="ai_statistics_unavailable",
            detail="AI statistics are temporarily unavailable. Retry shortly.", headers={"Retry-After": "2"},
        ) from exc


@router.get("/ops/providers", response_model=AIProviderUsageResponse)
def get_ai_provider_usage(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_READ_AI)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    authorization = require_ai_authorization_context(request)
    try:
        with database_operation(db, operation="interactive"):
            response = list_provider_usage(db, days=days, limit=limit, offset=offset, data_access=data_access)
            refence_ai_context(db, authorization=authorization, data_access=data_access)
            summary = ai_usage_event_would_deny_summary(
                db, data_access=data_access,
                filters=(AIUsageEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=days),),
            )
        record_ai_telemetry_would_deny(
            request, db, data_access=data_access, summary=summary,
            surface="ai.ops.providers.read", resource_type="ai_usage_event", history_scope="provider_usage",
        )
        return response
    except (DatabaseDeadlineExceeded, OperationalError, PoolTimeoutError) as exc:
        raise ApiHTTPException(
            status_code=503, error_code="provider_usage_unavailable",
            detail="Provider usage is temporarily unavailable. Retry shortly.", headers={"Retry-After": "2"},
        ) from exc
