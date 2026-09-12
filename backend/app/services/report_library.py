"""Permission-scoped title search and stable report keyset navigation."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, literal_column, select, tuple_
from sqlalchemy.orm import Session

from app.models.report import Report
from app.schemas.report_library import ReportLibraryFilters, ReportLibraryPage
from app.schemas.reports import ReportListItem
from app.services.data_access_envelopes import DATA_ACCESS_RESOURCE_REPORT, data_access_envelope_predicate
from app.services.data_access_policy import DataAccessContext
from app.services.report_read_models import report_list_columns


class ReportLibraryCursorError(ValueError):
    pass


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    as_of: AwareDatetime
    before: AwareDatetime | None = None
    before_id: uuid.UUID | None = None
    scope: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_position(self):
        if (self.before is None) != (self.before_id is None):
            raise ValueError("incomplete cursor position")
        if self.before is not None and self.before > self.as_of:
            raise ValueError("position is after result cutoff")
        return self


def _scope(filters: ReportLibraryFilters, data_access: DataAccessContext) -> str:
    payload = {"filters": filters.model_dump(mode="json"),
               "principal_type": data_access.principal_type, "principal_id": str(data_access.principal_id)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _encode(cursor: _Cursor) -> str:
    return base64.urlsafe_b64encode(cursor.model_dump_json().encode()).rstrip(b"=").decode("ascii")


def _decode(raw: str, *, scope: str, now: datetime) -> _Cursor:
    try:
        if len(raw) > 2048:
            raise ValueError("cursor too long")
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        cursor = _Cursor.model_validate_json(decoded)
        if cursor.scope != scope or cursor.as_of > now + timedelta(minutes=1):
            raise ValueError("cursor scope or cutoff mismatch")
        return cursor
    except (ValueError, ValidationError, binascii.Error) as exc:
        raise ReportLibraryCursorError("Invalid report cursor or changed filters. Restart from the first page.") from exc


def list_report_library_page(
    db: Session, *, filters: ReportLibraryFilters, data_access: DataAccessContext,
    cursor: str | None = None, limit: int = 25,
) -> ReportLibraryPage:
    now = datetime.now(timezone.utc)
    scope = _scope(filters, data_access)
    position = _decode(cursor, scope=scope, now=now) if cursor else _Cursor(as_of=now, scope=scope)
    # Cursors only describe positions, never authorization. Every page checks the
    # current permission envelope even when its timestamp/filter token is reused.
    query = select(*report_list_columns()).where(
        Report.created_at <= position.as_of,
        data_access_envelope_predicate(DATA_ACCESS_RESOURCE_REPORT, Report.id, data_access),
    )
    if position.before is not None:
        query = query.where(tuple_(Report.created_at, Report.id) < tuple_(position.before, position.before_id))
    for field in ("status", "report_type", "trigger_source"):
        value = getattr(filters, field)
        if value is not None:
            query = query.where(getattr(Report, field) == value)
    if filters.created_from is not None:
        query = query.where(Report.created_at >= filters.created_from)
    if filters.created_before is not None:
        query = query.where(Report.created_at < filters.created_before)
    if filters.q:
        try:
            identity = uuid.UUID(filters.q)
        except ValueError:
            configuration = literal_column("'simple'::regconfig")
            query = query.where(func.to_tsvector(configuration, Report.title).op("@@")(
                func.websearch_to_tsquery(configuration, filters.q)))
        else:
            query = query.where(Report.id == identity)
    limit = max(1, min(limit, 100))
    rows = db.execute(query.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit + 1)).all()
    items = [ReportListItem.model_validate(row._mapping) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = _encode(position.model_copy(update={"before": last.created_at, "before_id": last.id}))
    return ReportLibraryPage(items=items, current_cursor=_encode(position),
                             next_cursor=next_cursor, as_of=position.as_of)
