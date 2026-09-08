from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context, require_permissions
from app.api.routes.report_route_helpers import normalize_report_creation_range
from app.core.token_scopes import SCOPE_READ_REPORTS
from app.db.session import get_db
from app.models.user import User
from app.schemas.report_library import ReportLibraryFilters, ReportLibraryPage
from app.schemas.reports import ReportStatus
from app.services.data_access_policy import DataAccessContext
from app.services.report_library import ReportLibraryCursorError, list_report_library_page

router = APIRouter()


@router.get("/library", response_model=ReportLibraryPage)
def list_report_library(
    q: str = Query(default="", max_length=200, description="Title words, quoted phrases, OR, exclusions, or an exact report UUID."),
    report_status: ReportStatus | None = Query(default=None, alias="status"),
    report_type: str | None = Query(default=None, min_length=1, max_length=64),
    trigger_source: Literal["manual", "scheduled", "retry"] | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=2048),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions(SCOPE_READ_REPORTS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    created_from, created_before = normalize_report_creation_range(created_from, created_before)
    filters = ReportLibraryFilters(q=q.strip(), status=report_status, report_type=report_type,
                                   trigger_source=trigger_source, created_from=created_from,
                                   created_before=created_before)
    try:
        return list_report_library_page(db, filters=filters, data_access=data_access, cursor=cursor, limit=limit)
    except ReportLibraryCursorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
