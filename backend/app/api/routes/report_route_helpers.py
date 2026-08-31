from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.resource_preconditions import (
    InvalidResourceVersion,
    ResourceVersionMismatch,
    require_matching_resource_version,
)
from app.api.deps import (
    get_authorization_context,
    require_permission_roles,
    require_permissions,
)
from app.core.token_scopes import SCOPE_READ_REPORTS, SCOPE_WRITE_REPORTS
from app.core.rbac import ROLE_ADMIN
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.user import User
from app.schemas.reports import ReportQueueResponse
from app.services.ai_config import load_active_ai_settings
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import DataAccessContext
from app.services.ai_telemetry_data_policy import ai_task_run_access_predicate
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataPolicyError,
    fence_data_access_context,
)
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.report_rendering import (
    render_report_html,
    render_report_markdown,
    render_report_pdf,
)
from app.services.report_storage import report_detail_response


_REPORT_ADMIN_DETAIL = (
    "Report schedules and shared templates require the administrator role."
)
require_report_write = require_permissions(
    SCOPE_WRITE_REPORTS,
    denial_detail="Report generation requires the analyst or administrator role.",
)
require_report_admin_read = require_permission_roles(
    SCOPE_READ_REPORTS,
    roles=(ROLE_ADMIN,),
    detail=_REPORT_ADMIN_DETAIL,
)
require_report_admin_write = require_permission_roles(
    SCOPE_WRITE_REPORTS,
    roles=(ROLE_ADMIN,),
    detail=_REPORT_ADMIN_DETAIL,
)
REPORT_PREVIEW_LIMIT = 25
RESOURCE_PRECONDITION_RESPONSES = {
    status.HTTP_400_BAD_REQUEST: {"description": "Malformed If-Match header"},
    status.HTTP_412_PRECONDITION_FAILED: {
        "description": "Resource version no longer matches"
    },
}


def active_reporting_settings(db: Session):
    active = load_active_ai_settings(db)
    try:
        ensure_reporting_available(active)
    except ReportingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return active


def get_accessible_report(
    db: Session,
    *,
    report_id: uuid.UUID,
    data_access: DataAccessContext,
    for_update: bool = False,
) -> Report | None:
    statement = select(Report).where(
        Report.id == report_id,
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_REPORT,
            Report.id,
            data_access,
        ),
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement.execution_options(populate_existing=True))


def get_accessible_report_task(
    db: Session,
    *,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    data_access: DataAccessContext,
) -> tuple[Report, AITaskRun] | None:
    report = get_accessible_report(
        db,
        report_id=report_id,
        data_access=data_access,
    )
    run = db.scalar(
        select(AITaskRun).where(
            AITaskRun.id == run_id,
            ai_task_run_access_predicate(data_access),
        )
    )
    if report is None or run is None:
        return None
    return report, run


def require_report_authorization_context(request: Request) -> AuthorizationContext:
    authorization = get_authorization_context(request)
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report authorization is unavailable. Retry the request.",
        )
    return authorization


def refence_report_context(
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
                "Report authorization changed while the request was in "
                "progress. Retry the request."
            ),
        ) from exc


def queue_response(
    report: Report,
    run: AITaskRun,
    *,
    celery_task_id: str | None = None,
) -> ReportQueueResponse:
    return ReportQueueResponse(
        report_id=report.id,
        task_run_id=run.id,
        celery_task_id=celery_task_id or run.celery_task_id,
        status=run.status,
        schedule_id=report.schedule_id,
    )


def render_report_download(
    db: Session,
    *,
    report: Report,
    format: str,
) -> Response:
    detail = report_detail_response(db, report=report)
    filename = f"threatlens-report-{report.id}"
    if format == "pdf":
        content = render_report_pdf(detail)
        media_type = "application/pdf"
        extension = "pdf"
    elif format == "html":
        content = render_report_html(detail).encode("utf-8")
        media_type = "text/html; charset=utf-8"
        extension = "html"
    else:
        content = render_report_markdown(detail).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        extension = "md"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{extension}"'
        },
    )


def require_current_resource_version(
    *,
    current_updated_at: datetime,
    if_match: str | list[str] | None,
    resource_label: str,
) -> None:
    try:
        require_matching_resource_version(
            current_updated_at=current_updated_at,
            if_match=if_match,
        )
    except InvalidResourceVersion as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ResourceVersionMismatch as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=(
                f"The {resource_label} changed after you loaded it. Refresh the "
                "latest version, review the changes, and try again."
            ),
        ) from exc


def integrity_constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def require_shared_template_admin(user: User, visibility: str) -> None:
    if visibility == "shared" and user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can create or update shared report templates.",
        )


def require_template_owner_or_admin(
    user: User,
    owner_user_id: uuid.UUID | None,
) -> None:
    if user.role != ROLE_ADMIN and owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own private report templates.",
        )


def require_report_owner_or_admin(
    user: User,
    owner_user_id: uuid.UUID | None,
) -> None:
    if user.role != ROLE_ADMIN and owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only retry or delete reports that you generated.",
        )
