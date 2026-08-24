from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease


logger = logging.getLogger(__name__)


class ReportGenerationOwnershipError(RuntimeError):
    """Base class for generation ownership failures."""


class ReportGenerationLeaseLostError(ReportGenerationOwnershipError):
    code = "ownership_lost"


class ReportGenerationLeaseUnavailableError(ReportGenerationOwnershipError):
    code = "ownership_unverified"


@dataclass(frozen=True)
class ReportGenerationClaim:
    status: Literal["claimed", "busy", "interrupted", "unavailable"]
    generation_fence: int | None = None
    lease_expires_at: datetime | None = None

    @property
    def owns_lease(self) -> bool:
        return self.status in {"claimed", "interrupted"}


def claim_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
    lease_seconds: int,
    legacy_worker_grace_seconds: int = 86_400,
) -> ReportGenerationClaim:
    report = db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if report is None or report.status not in {"queued", "running"}:
        return ReportGenerationClaim("unavailable")

    lease = db.scalar(
        select(ReportGenerationLease)
        .where(ReportGenerationLease.report_id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if lease is None:
        lease = ReportGenerationLease(report_id=report_id)
        db.add(lease)
        db.flush()

    now = datetime.now(timezone.utc)
    lease_expiry = _as_utc(lease.lease_expires_at)
    legacy_expiry = _as_utc(report.generation_lease_expires_at)
    if (
        report.status == "running"
        and lease.lease_token is None
        and report.generation_lease_token is None
    ):
        compatibility_token = f"legacy-unfenced:{report_id.hex}"
        compatibility_expiry = now + timedelta(seconds=legacy_worker_grace_seconds)
        lease.generation_fence = int(lease.generation_fence or 0) + 1
        lease.lease_token = compatibility_token
        lease.lease_expires_at = compatibility_expiry
        report.generation_lease_token = compatibility_token
        report.generation_lease_expires_at = compatibility_expiry
        db.add(lease)
        db.add(report)
        return ReportGenerationClaim(
            "busy",
            generation_fence=lease.generation_fence,
            lease_expires_at=compatibility_expiry,
        )
    lease_is_active = _active_foreign_lease(
        token=lease.lease_token,
        expected_token=lease_token,
        expires_at=lease_expiry,
        now=now,
    )
    legacy_is_active = _active_foreign_lease(
        token=report.generation_lease_token,
        expected_token=lease_token,
        expires_at=legacy_expiry,
        now=now,
    )
    if lease_is_active or legacy_is_active:
        active_expiries = [
            expiry
            for active, expiry in (
                (lease_is_active, lease_expiry),
                (legacy_is_active, legacy_expiry),
            )
            if active and expiry is not None
        ]
        return ReportGenerationClaim(
            "busy",
            generation_fence=lease.generation_fence,
            lease_expires_at=max(active_expiries) if active_expiries else None,
        )

    interrupted = report.status == "running"
    lease.generation_fence = int(lease.generation_fence or 0) + 1
    lease.lease_token = lease_token
    lease.lease_expires_at = now + timedelta(seconds=lease_seconds)
    report.generation_lease_token = lease_token
    report.generation_lease_expires_at = _legacy_lease_expiry(
        now=now,
        lease_seconds=lease_seconds,
    )
    db.add(lease)
    db.add(report)
    return ReportGenerationClaim(
        "interrupted" if interrupted else "claimed",
        generation_fence=lease.generation_fence,
        lease_expires_at=lease.lease_expires_at,
    )


def renew_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
    generation_fence: int,
    lease_seconds: int,
) -> bool:
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(ReportGenerationLease)
        .where(
            ReportGenerationLease.report_id == report_id,
            ReportGenerationLease.lease_token == lease_token,
            ReportGenerationLease.generation_fence == generation_fence,
            ReportGenerationLease.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def fence_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
    generation_fence: int,
    lease_seconds: int,
) -> bool:
    """Validate ownership in the transaction that will commit report output."""

    now = datetime.now(timezone.utc)
    # Keep the pre-0049 columns alive during rolling upgrades. Lock the report
    # before the companion lease everywhere that both rows are touched.
    report_result = db.execute(
        update(Report)
        .where(
            Report.id == report_id,
            Report.generation_lease_token == lease_token,
        )
        .values(
            generation_lease_expires_at=_legacy_lease_expiry(
                now=now,
                lease_seconds=lease_seconds,
            )
        )
        .execution_options(synchronize_session=False)
    )
    if report_result.rowcount != 1:
        logger.warning(
            "report_generation_commit_legacy_mismatch report_id=%s fence=%s",
            report_id,
            generation_fence,
        )
        return False

    lease_result = db.execute(
        update(ReportGenerationLease)
        .where(
            ReportGenerationLease.report_id == report_id,
            ReportGenerationLease.lease_token == lease_token,
            ReportGenerationLease.generation_fence == generation_fence,
            ReportGenerationLease.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        .execution_options(synchronize_session=False)
    )
    if lease_result.rowcount != 1:
        logger.warning(
            "report_generation_commit_fence_mismatch report_id=%s fence=%s",
            report_id,
            generation_fence,
        )
        return False
    return True


def release_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    lease_token: str,
    generation_fence: int,
) -> bool:
    report_result = db.execute(
        update(Report)
        .where(
            Report.id == report_id,
            Report.generation_lease_token == lease_token,
        )
        .values(generation_lease_token=None, generation_lease_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    if report_result.rowcount != 1:
        logger.warning(
            "report_generation_release_legacy_mismatch report_id=%s fence=%s",
            report_id,
            generation_fence,
        )
        return False
    lease_result = db.execute(
        update(ReportGenerationLease)
        .where(
            ReportGenerationLease.report_id == report_id,
            ReportGenerationLease.lease_token == lease_token,
            ReportGenerationLease.generation_fence == generation_fence,
        )
        .values(lease_token=None, lease_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    if lease_result.rowcount != 1:
        logger.warning(
            "report_generation_release_fence_mismatch report_id=%s fence=%s",
            report_id,
            generation_fence,
        )
        return False
    return True


def invalidate_stale_report_generation(
    db: Session,
    *,
    report_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Fence expired generation work before its task run is reconciled."""

    observed_at = _as_utc(now or datetime.now(timezone.utc))
    report = db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    lease = db.scalar(
        select(ReportGenerationLease)
        .where(ReportGenerationLease.report_id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if _active_lease(
        token=lease.lease_token if lease is not None else None,
        expires_at=_as_utc(lease.lease_expires_at) if lease is not None else None,
        now=observed_at,
    ) or _active_lease(
        token=report.generation_lease_token if report is not None else None,
        expires_at=(
            _as_utc(report.generation_lease_expires_at)
            if report is not None
            else None
        ),
        now=observed_at,
    ):
        return False

    if report is not None:
        report.generation_lease_token = None
        report.generation_lease_expires_at = None
        db.add(report)
    if lease is not None:
        lease.generation_fence = int(lease.generation_fence or 0) + 1
        lease.lease_token = None
        lease.lease_expires_at = None
        db.add(lease)
    db.flush()
    return True


def _active_foreign_lease(
    *,
    token: str | None,
    expected_token: str,
    expires_at: datetime | None,
    now: datetime,
) -> bool:
    return bool(
        token
        and token != expected_token
        and expires_at is not None
        and expires_at > now
    )


def _active_lease(
    *, token: str | None, expires_at: datetime | None, now: datetime
) -> bool:
    return bool(token and expires_at is not None and expires_at > now)


def _legacy_lease_expiry(*, now: datetime, lease_seconds: int) -> datetime:
    # The legacy lease is only a rolling-upgrade compatibility guard. Give it
    # one extra lease period so an old worker cannot race a provider call.
    return now + timedelta(seconds=lease_seconds * 2)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ReportGenerationClaim",
    "ReportGenerationLeaseLostError",
    "ReportGenerationLeaseUnavailableError",
    "ReportGenerationOwnershipError",
    "claim_report_generation",
    "fence_report_generation",
    "invalidate_stale_report_generation",
    "release_report_generation",
    "renew_report_generation",
]
