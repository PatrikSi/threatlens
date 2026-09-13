"""Human report publication commands, fenced by current authority and evidence access."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context
from app.api.routes.report_route_helpers import (
    get_accessible_report,
    require_report_authorization_context,
    require_report_write,
)
from app.api.routes.report_editorial_authorization import fence_editorial_request
from app.core.api_errors import ApiHTTPException
from app.db.budgets import database_operation
from app.db.session import get_db
from app.models.report import Report
from app.models.user import User
from app.schemas.report_editorial import ReportDraftUpdate, ReportEditorialTransition
from app.schemas.reports import ReportDetailResponse
from app.services.data_access_policy import DataAccessContext
from app.services.report_editorial import (
    ReportEditorialError,
    transition_report,
    update_report_draft,
)
from app.services.report_storage import report_detail_response

router = APIRouter()


def _report(
    db: Session, request: Request, report_id: uuid.UUID, data_access: DataAccessContext
) -> Report:
    authorization = require_report_authorization_context(request)
    if authorization.principal_type != "user":
        raise HTTPException(
            status_code=403, detail="Editorial review requires a user account."
        )
    fence_editorial_request(
        db, request=request, authorization=authorization, data_access=data_access
    )
    report = get_accessible_report(
        db, report_id=report_id, data_access=data_access, for_update=True
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _require_author(user: User, report: Report) -> None:
    if user.role != "admin" and user.id != report.owner_user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the report owner or an administrator can edit, submit, or publish this report.",
        )


@router.put("/{report_id:uuid}/draft", response_model=ReportDetailResponse)
def edit_report_draft(
    report_id: uuid.UUID,
    payload: ReportDraftUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    try:
        with database_operation(db, operation="interactive"):
            report = _report(db, request, report_id, data_access)
            _require_author(user, report)
            update_report_draft(
                db, report=report, payload=payload, actor_user_id=user.id
            )
            response = report_detail_response(db, report=report)
            fence_editorial_request(
                db,
                request=request,
                authorization=require_report_authorization_context(request),
                data_access=data_access,
            )
            db.commit()
    except ReportEditorialError as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=409, error_code=exc.code, detail=str(exc)
        ) from exc
    return response


@router.post("/{report_id:uuid}/editorial", response_model=ReportDetailResponse)
def change_report_editorial_state(
    report_id: uuid.UUID,
    payload: ReportEditorialTransition,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_report_write),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    try:
        with database_operation(db, operation="interactive"):
            report = _report(db, request, report_id, data_access)
            if payload.action in {"submit", "publish"}:
                _require_author(user, report)
            event_id = transition_report(
                db, report=report, payload=payload, actor_user_id=user.id
            )
            response = report_detail_response(db, report=report)
            fence_editorial_request(
                db,
                request=request,
                authorization=require_report_authorization_context(request),
                data_access=data_access,
            )
            db.commit()
    except ReportEditorialError as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=409, error_code=exc.code, detail=str(exc)
        ) from exc
    if event_id is not None:
        from app.tasks.integration_tasks import enqueue_integration_event_routing

        enqueue_integration_event_routing([event_id])
    return response
