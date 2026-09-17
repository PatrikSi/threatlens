"""Read retained report content using its current source authorization envelope."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context, require_permissions
from app.api.routes.report_route_helpers import get_accessible_report
from app.core.token_scopes import SCOPE_READ_REPORTS
from app.db.session import get_db
from app.models.user import User
from app.schemas.reports import ReportDetailResponse
from app.schemas.report_evidence import ReportSourceEvidenceResponse
from app.models.report import Report
from app.db.budgets import database_operation
from app.api.routes.report_editorial_authorization import fence_report_read_request
from app.api.routes.report_route_helpers import require_report_authorization_context
from app.services.report_evidence_reads import retained_source_evidence
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


@router.get("/{report_id:uuid}/sources/{citation_key}/evidence", response_model=ReportSourceEvidenceResponse)
def get_report_source_evidence(
    report_id: uuid.UUID,
    request: Request,
    response: Response,
    citation_key: str = Path(min_length=1, max_length=16),
    editorial_version: int = Query(ge=1),
    offset: int = Query(default=0, ge=0, le=10_000_000),
    limit: int = Query(default=8000, ge=1, le=16_000),
    source_revision: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ReportSourceEvidenceResponse:
    authorization = require_report_authorization_context(request)
    with database_operation(db, operation="interactive"):
        fence_report_read_request(db, request=request, authorization=authorization, data_access=data_access)
        report = get_accessible_report(
            db, report_id=report_id, data_access=data_access, read_lock=True,
            load_fields=(Report.id, Report.editorial_version),
        )
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        result = retained_source_evidence(
            db, report=report, citation_key=citation_key, editorial_version=editorial_version,
            offset=offset, limit=limit, source_revision=source_revision,
        )
        fence_report_read_request(db, request=request, authorization=authorization, data_access=data_access)
    response.headers["Cache-Control"] = "no-store"
    return result
