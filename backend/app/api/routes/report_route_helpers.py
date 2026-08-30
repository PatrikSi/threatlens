from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.resource_preconditions import (
    InvalidResourceVersion,
    ResourceVersionMismatch,
    require_matching_resource_version,
)
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
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)


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
