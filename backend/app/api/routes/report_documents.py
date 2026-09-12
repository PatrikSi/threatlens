"""Read retained report content using its current source authorization envelope."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context, require_permissions
from app.api.routes.report_route_helpers import get_accessible_report
from app.core.token_scopes import SCOPE_READ_REPORTS
from app.db.session import get_db
from app.models.user import User
from app.schemas.reports import ReportDetailResponse
from app.services.data_access_policy import DataAccessContext
from app.services.report_storage import report_detail_response

router = APIRouter()


@router.get("/{report_id:uuid}", response_model=ReportDetailResponse)
def get_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    report = get_accessible_report(
        db,
        report_id=report_id,
        data_access=data_access,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    return report_detail_response(db, report=report)
