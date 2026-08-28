from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.rbac import ROLE_ADMIN
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_ALERTS, SCOPE_WRITE_ALERTS
from app.db.session import get_db
from app.models.user import User
from app.schemas.alert import (
    AlertEvaluationActivityListResponse,
    AlertEvaluationReplayRequest,
    AlertEvaluationReplayResponse,
    AlertEvaluationRequestListResponse,
    AlertEvaluationRequestResponse,
    AlertOccurrenceMetricListResponse,
)
from app.services.alert_evaluation_admin import (
    ALERT_EVALUATION_SOURCES,
    ALERT_EVALUATION_STATES,
    AlertEvaluationConflictError,
    AlertEvaluationNotFoundError,
    AlertEvaluationValidationError,
    get_alert_evaluation_request,
    list_alert_evaluation_activity,
    list_alert_evaluation_requests,
    list_alert_occurrence_metrics,
    replay_dead_letter_evaluation,
)
from app.services.alert_occurrences import (
    ALERT_OCCURRENCE_SEVERITIES,
    ALERT_OCCURRENCE_STATES,
)
from app.services.audit import record_audit
from app.tasks.alert_tasks import enqueue_alert_evaluation_requests


router = APIRouter(prefix="/occurrences")
MAX_ALERT_PAGE = 1_000_000
AlertPage = Annotated[int, Query(ge=1, le=MAX_ALERT_PAGE)]


@router.get("/metrics", response_model=AlertOccurrenceMetricListResponse)
def get_alert_occurrence_metrics(
    since: datetime | None = None,
    until: datetime | None = None,
    severities: list[str] = Query(default=[]),
    lifecycle_states: list[str] = Query(default=[]),
    suppressed: bool | None = None,
    limit: int = Query(default=500, ge=1, le=1_000),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    current_time = datetime.now(timezone.utc)
    normalized_until = _as_utc(until) or current_time
    normalized_since = _as_utc(since) or normalized_until - timedelta(days=365)
    if normalized_since > normalized_until:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="since must be earlier than or equal to until.",
            error_code="alert_metrics_window_invalid",
        )
    if normalized_until - normalized_since > timedelta(days=730):
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Metric windows cannot exceed 730 days.",
            error_code="alert_metrics_window_too_large",
        )
    _validate_values(
        severities,
        ALERT_OCCURRENCE_SEVERITIES,
        "metric severity",
        error_code="alert_metrics_filter_invalid",
    )
    _validate_values(
        lifecycle_states,
        ALERT_OCCURRENCE_STATES,
        "metric state",
        error_code="alert_metrics_filter_invalid",
    )
    result = list_alert_occurrence_metrics(
        db,
        owner_user_id=user.id,
        since=normalized_since,
        until=normalized_until,
        severities=list(dict.fromkeys(severities)),
        lifecycle_states=list(dict.fromkeys(lifecycle_states)),
        suppressed=suppressed,
        limit=limit,
    )
    return {"items": result.items, "truncated": result.truncated}


@router.get("/evaluations", response_model=AlertEvaluationRequestListResponse)
def get_alert_evaluations(
    states: list[str] = Query(default=[]),
    sources: list[str] = Query(default=[]),
    item_id: uuid.UUID | None = None,
    needs_attention: bool = False,
    page: AlertPage = 1,
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    _require_admin(user)
    _validate_values(states, ALERT_EVALUATION_STATES, "evaluation state")
    _validate_values(sources, ALERT_EVALUATION_SOURCES, "evaluation source")
    result = list_alert_evaluation_requests(
        db,
        states=list(dict.fromkeys(states)),
        sources=list(dict.fromkeys(sources)),
        item_id=item_id,
        needs_attention=needs_attention,
        page=page,
        page_size=page_size,
    )
    return {
        "items": result.items,
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@router.get(
    "/evaluations/{request_id}",
    response_model=AlertEvaluationRequestResponse,
)
def get_alert_evaluation_detail(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    _require_admin(user)
    try:
        return get_alert_evaluation_request(db, request_id=request_id)
    except AlertEvaluationNotFoundError as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            error_code=exc.code,
        ) from exc


@router.get(
    "/evaluations/{request_id}/activity",
    response_model=AlertEvaluationActivityListResponse,
)
def get_alert_evaluation_activity(
    request_id: uuid.UUID,
    page: AlertPage = 1,
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ALERTS)),
):
    _require_admin(user)
    try:
        result = list_alert_evaluation_activity(
            db,
            request_id=request_id,
            page=page,
            page_size=page_size,
        )
    except AlertEvaluationNotFoundError as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            error_code=exc.code,
        ) from exc
    return {
        "items": result.items,
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@router.post(
    "/evaluations/{request_id}/replay",
    response_model=AlertEvaluationReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def replay_alert_evaluation(
    request_id: uuid.UUID,
    payload: AlertEvaluationReplayRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_ALERTS)),
):
    _require_admin(user)
    try:
        request = replay_dead_letter_evaluation(
            db,
            request_id=request_id,
            expected_version=payload.expected_version,
            actor_user_id=user.id,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="alerts.evaluations.replay",
            resource_type="alert_evaluation_request",
            resource_id=str(request.id),
            metadata={
                "original_source": request.source,
                "notify": request.notify,
                "version": request.version,
            },
        )
        db.commit()
        db.refresh(request)
    except Exception as exc:
        return _evaluation_error_response(db, exc)
    enqueue_ok = enqueue_alert_evaluation_requests([request.id])
    return {"request": request, "enqueue_failed": not enqueue_ok}


def _require_admin(user: User) -> None:
    if user.role != ROLE_ADMIN:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alert evaluation operations require the administrator role.",
            error_code="alert_evaluation_admin_required",
        )


def _validate_values(
    values: list[str],
    allowed: frozenset[str],
    label: str,
    *,
    error_code: str = "alert_evaluation_filter_invalid",
) -> None:
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported {label}: {', '.join(invalid)}.",
            error_code=error_code,
        )


def _evaluation_error_response(db: Session, exc: Exception):
    db.rollback()
    if isinstance(exc, AlertEvaluationNotFoundError):
        raise ApiHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            error_code=exc.code,
        ) from exc
    if isinstance(exc, AlertEvaluationValidationError):
        raise ApiHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
            error_code=exc.code,
        ) from exc
    if isinstance(exc, AlertEvaluationConflictError):
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code=exc.code,
            headers={
                "X-Error-Code": exc.code,
                "X-Current-Version": str(exc.current_version),
            },
        ) from exc
    raise exc


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
