from datetime import datetime

from pydantic import BaseModel

from app.schemas.reports import ReportListItem, ReportPublicationStatus, ReportStatus


class ReportLibraryFilters(BaseModel):
    q: str = ""
    status: ReportStatus | None = None
    publication_status: ReportPublicationStatus | None = None
    report_type: str | None = None
    trigger_source: str | None = None
    created_from: datetime | None = None
    created_before: datetime | None = None


class ReportLibraryPage(BaseModel):
    items: list[ReportListItem]
    current_cursor: str
    next_cursor: str | None
    as_of: datetime
