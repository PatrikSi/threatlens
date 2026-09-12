"""Reserve bounded export wake-ups before attempting broker publication."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.export_job import ExportJob

EXPORT_PUBLICATION_GRACE_SECONDS = 30


def reserve_export_publications(
    db: Session,
    *,
    canary_at: datetime | None,
    job_id: uuid.UUID | None = None,
    now: datetime | None = None,
    limit: int = 25,
) -> list[uuid.UUID]:
    """A paused consumer must not accumulate another wake-up each sweep.

    First publications need no consumer evidence. After an uncertain publication,
    only a newer execution canary permits repair; elapsed time alone does not
    prove the old message was lost. Generation leases remain independent.
    """
    current_time = now or datetime.now(timezone.utc)
    publication_due = ExportJob.published_at.is_(None)
    if canary_at is not None:
        publication_due = or_(
            publication_due,
            and_(
                ExportJob.published_at <= current_time - timedelta(seconds=EXPORT_PUBLICATION_GRACE_SECONDS),
                ExportJob.published_canary_at < canary_at,
            ),
        )
    statement = select(ExportJob).where(
        ExportJob.status == "queued",
        ExportJob.expires_at > current_time,
        ExportJob.next_attempt_at <= current_time,
        ExportJob.next_dispatch_at <= current_time,
        publication_due,
    )
    if job_id is not None:
        statement = statement.where(ExportJob.id == job_id)
    jobs = db.scalars(
        statement.order_by(ExportJob.created_at, ExportJob.id)
        .limit(max(1, min(limit, 25)))
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    for job in jobs:
        job.published_at = current_time
        # With no observed canary, require execution after this reservation.
        job.published_canary_at = max(current_time, canary_at or current_time)
        job.next_dispatch_at = current_time + timedelta(seconds=EXPORT_PUBLICATION_GRACE_SECONDS)
    db.flush()
    return [job.id for job in jobs]


def reset_export_publication(job: ExportJob) -> None:
    """An acknowledged attempt can safely schedule its next generation attempt."""
    job.published_at = None
    job.published_canary_at = None
    job.next_dispatch_at = job.next_attempt_at
