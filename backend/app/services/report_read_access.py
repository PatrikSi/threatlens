"""Shared current source authorization for retained report reads."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.models.report import Report
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import DataAccessContext


def get_accessible_report(
    db: Session,
    *,
    report_id: uuid.UUID,
    data_access: DataAccessContext,
    for_update: bool = False,
    load_fields: tuple | None = None,
    read_lock: bool = False,
) -> Report | None:
    statement = select(Report).where(
        Report.id == report_id,
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_REPORT, Report.id, data_access
        ),
    )
    if for_update:
        statement = statement.with_for_update()
    elif read_lock:
        statement = statement.with_for_update(read=True)
    if load_fields is not None:
        statement = statement.options(load_only(*load_fields, raiseload=True))
    return db.scalar(statement.execution_options(populate_existing=True))
