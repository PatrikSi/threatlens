from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.report import Report


class ReportGenerationLeaseLostError(RuntimeError):
    code = "ownership_lost"


def claim_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
    lease_seconds: int,
) -> bool:
    report = db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if report is None or report.status not in {"queued", "running"}:
        return False

    now = datetime.now(timezone.utc)
    current_expiry = _as_utc(report.generation_lease_expires_at)
    if (
        report.generation_lease_token
        and report.generation_lease_token != lease_token
        and current_expiry is not None
        and current_expiry > now
    ):
        return False

    report.generation_lease_token = lease_token
    report.generation_lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.add(report)
    return True


def renew_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
    lease_seconds: int,
) -> bool:
    result = db.execute(
        update(Report)
        .where(
            Report.id == report_id,
            Report.generation_lease_token == lease_token,
            Report.status.in_(["queued", "running"]),
        )
        .values(
            generation_lease_expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=lease_seconds)
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def release_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
) -> bool:
    result = db.execute(
        update(Report)
        .where(
            Report.id == report_id,
            Report.generation_lease_token == lease_token,
        )
        .values(generation_lease_token=None, generation_lease_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ReportGenerationLeaseLostError",
    "claim_report_generation",
    "release_report_generation",
    "renew_report_generation",
]
