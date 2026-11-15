"""Consumer progress, rather than elapsed time, permits another wake-up."""

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement


def recovery_publication_progress(
    reserved_at: ColumnElement[datetime | None],
    *,
    canary_at: datetime | None,
) -> ColumnElement[bool]:
    # An unset reservation admits new work even if Redis health evidence is
    # unavailable. Repeating an uncertain publication requires later execution
    # evidence; a stale lease alone does not prove the old message was lost.
    if canary_at is None:
        return reserved_at.is_(None)
    observed = (
        canary_at.replace(tzinfo=timezone.utc)
        if canary_at.tzinfo is None
        else canary_at.astimezone(timezone.utc)
    )
    return or_(reserved_at.is_(None), reserved_at < observed)
