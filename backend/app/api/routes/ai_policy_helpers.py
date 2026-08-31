from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_context
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.schemas.ai import AILiveStatusResponse
from app.services.ai_ops_common import (
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_RUNNING,
)
from app.services.ai_task_runtime import filter_ai_live_tasks
from app.services.ai_telemetry_data_policy import (
    AITelemetryWouldDenySummary,
    ai_overview_source_would_deny_summary,
    ai_task_run_would_deny_summary,
    ai_usage_event_would_deny_summary,
)
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    fence_data_access_context,
)
from app.services.data_policy_audit import record_data_policy_decision


def ai_run_list_filters(
    *,
    task_type: str | None,
    status_value: str | None,
    trigger_source: str | None,
    model: str | None,
    since: datetime | None,
    parent_run_id: uuid.UUID | None,
    only_failures: bool,
) -> tuple[object, ...]:
    filters: list[object] = []
    if task_type:
        filters.append(AITaskRun.task_type == task_type)
    if status_value:
        filters.append(AITaskRun.status == status_value)
    if trigger_source:
        filters.append(AITaskRun.trigger_source == trigger_source)
    if model:
        filters.append(AITaskRun.model == model)
    if since:
        filters.append(AITaskRun.created_at >= since)
    if parent_run_id:
        filters.append(AITaskRun.parent_run_id == parent_run_id)
    if only_failures:
        filters.append(
            or_(AITaskRun.status == AI_STATUS_ERROR, AITaskRun.error.is_not(None))
        )
    return tuple(filters)


def ai_live_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    response: AILiveStatusResponse,
) -> AITelemetryWouldDenySummary:
    summary = ai_task_run_would_deny_summary(
        db,
        data_access=data_access,
        filters=(AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),),
    )
    if not data_access.auditing or not data_access.principal_eligible:
        return summary
    enforced = replace(data_access, mode="enforced")
    served_tasks = [
        *response.active_tasks,
        *response.reserved_tasks,
        *response.scheduled_tasks,
    ]
    enforced_tasks = filter_ai_live_tasks(
        db,
        tasks=served_tasks,
        data_access=enforced,
    )
    broker_denied_count = len(served_tasks) - len(enforced_tasks)
    handling_label_ids = set(summary.handling_label_ids)
    if broker_denied_count > summary.affected_count:
        handling_label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
    return AITelemetryWouldDenySummary(
        affected_count=max(summary.affected_count, broker_denied_count),
        handling_label_ids=frozenset(handling_label_ids),
    )


def record_ai_telemetry_would_deny(
    request: Request,
    db: Session,
    *,
    data_access: DataAccessContext,
    summary: AITelemetryWouldDenySummary,
    surface: str,
    resource_type: str,
    history_scope: str,
) -> None:
    if not summary.affected_count:
        return
    record_data_policy_decision(
        db,
        context=data_access,
        decision="would_deny",
        resource_type=resource_type,
        surface=surface,
        handling_label_ids=summary.handling_label_ids,
        affected_count=summary.affected_count,
        metadata_extra={"history_scope": history_scope},
    )
    db.commit()
    refence_ai_context(
        db,
        authorization=require_ai_authorization_context(request),
        data_access=data_access,
    )


def record_ai_overview_would_deny(
    request: Request,
    db: Session,
    *,
    data_access: DataAccessContext,
    days: int,
) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    for summary, resource_type, history_scope in (
        (
            ai_usage_event_would_deny_summary(
                db,
                data_access=data_access,
                filters=(AIUsageEvent.created_at >= since,),
            ),
            "ai_usage_event",
            "overview_usage",
        ),
        (
            ai_task_run_would_deny_summary(
                db,
                data_access=data_access,
            ),
            "ai_task_run",
            "overview_runs",
        ),
        (
            ai_overview_source_would_deny_summary(
                db,
                data_access=data_access,
            ),
            "ai_overview_source",
            "overview_sources",
        ),
    ):
        record_ai_telemetry_would_deny(
            request,
            db,
            data_access=data_access,
            summary=summary,
            surface="ai.ops.overview.read",
            resource_type=resource_type,
            history_scope=history_scope,
        )


def require_ai_authorization_context(request: Request) -> AuthorizationContext:
    authorization = get_authorization_context(request)
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI operations authorization is unavailable. Retry the request.",
        )
    return authorization


def refence_ai_context(
    db: Session,
    *,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
) -> None:
    try:
        fence_authorization_context(db, authorization)
        fence_data_access_context(db, data_access)
    except (AuthorizationStateUnavailable, DataPolicyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "AI operations authorization changed while the request was in "
                "progress. Retry the request."
            ),
        ) from exc


__all__ = [
    "ai_live_would_deny_summary",
    "ai_run_list_filters",
    "record_ai_overview_would_deny",
    "record_ai_telemetry_would_deny",
    "refence_ai_context",
    "require_ai_authorization_context",
]
