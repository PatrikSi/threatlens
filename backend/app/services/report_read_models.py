"""Small report projections shared by both report library APIs."""

from sqlalchemy import ColumnElement, func

from app.models.report import Report
from app.schemas.reports import ReportListItem

REPORT_LIST_ERROR_LIMIT = 4000


def report_list_columns() -> list[ColumnElement]:
    """Exclude execution snapshots and bound diagnostic text before materialization."""
    return [
        func.substr(Report.error, 1, REPORT_LIST_ERROR_LIMIT).label("error")
        if name == "error"
        else getattr(Report, name)
        for name in ReportListItem.model_fields
    ]
