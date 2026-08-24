from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
from app.services.ai_ops import AI_STATUS_ERROR, finish_ai_task_run
from app.services.ai_ops_common import AI_STATUS_QUEUED, AI_TASK_TYPE_REPORT


@dataclass(frozen=True)
class ReportDispatchClaim:
    claimed: bool
    dispatch_token: str | None = None
    celery_task_id: str | None = None
    terminalized: bool = False


def initialize_report_dispatch(run: AITaskRun, *, now: datetime | None = None) -> None:
    run.dispatch_attempt_count = 0
    run.dispatch_next_attempt_at = _as_utc(now or datetime.now(timezone.utc))
    run.dispatch_error = None
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    run.dispatch_published_at = None


def claim_report_dispatch(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    now: datetime,
) -> ReportDispatchClaim:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not _is_dispatchable(run, report_id=report_id):
        return ReportDispatchClaim(
            False, celery_task_id=run.celery_task_id if run else None
        )

    settings = get_settings()
    observed_at = _as_utc(now)
    claim_expires_at = _as_optional_utc(run.dispatch_claim_expires_at)
    if (
        run.dispatch_claim_token
        and claim_expires_at is not None
        and claim_expires_at > observed_at
    ):
        return ReportDispatchClaim(False, celery_task_id=run.celery_task_id)

    next_attempt_at = _as_optional_utc(run.dispatch_next_attempt_at)
    if next_attempt_at is not None and next_attempt_at > observed_at:
        return ReportDispatchClaim(False, celery_task_id=run.celery_task_id)

    attempt_count = int(run.dispatch_attempt_count or 0)
    if (
        attempt_count >= settings.report_dispatch_max_attempts
        and run.dispatch_published_at is None
    ):
        _settle_report_dispatch_exhausted(
            db,
            run=run,
            report_id=report_id,
            observed_at=observed_at,
        )
        return ReportDispatchClaim(
            False,
            celery_task_id=run.celery_task_id,
            terminalized=True,
        )

    dispatch_token = uuid.uuid4().hex
    run.dispatch_attempt_count = min(
        attempt_count + 1,
        settings.report_dispatch_max_attempts,
    )
    run.dispatch_claim_token = dispatch_token
    run.dispatch_claim_expires_at = observed_at + timedelta(
        seconds=settings.report_dispatch_claim_seconds
    )
    run.dispatch_next_attempt_at = run.dispatch_claim_expires_at
    db.add(run)
    return ReportDispatchClaim(
        True,
        dispatch_token=dispatch_token,
        celery_task_id=run.celery_task_id,
    )


def record_report_dispatch_success(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    dispatch_token: str,
    celery_task_id: str,
    now: datetime,
) -> bool:
    run = _load_claimed_dispatch(
        db,
        report_id=report_id,
        task_run_id=task_run_id,
        dispatch_token=dispatch_token,
    )
    if run is None:
        return False
    settings = get_settings()
    observed_at = _as_utc(now)
    run.celery_task_id = celery_task_id
    run.dispatch_attempt_count = 0
    run.dispatch_published_at = observed_at
    run.dispatch_next_attempt_at = observed_at + timedelta(
        seconds=settings.report_dispatch_stale_after_seconds
    )
    run.dispatch_error = None
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    db.add(run)
    return True


def record_report_dispatch_failure(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    dispatch_token: str,
    now: datetime,
) -> bool:
    run = _load_claimed_dispatch(
        db,
        report_id=report_id,
        task_run_id=task_run_id,
        dispatch_token=dispatch_token,
    )
    if run is None:
        return False

    settings = get_settings()
    observed_at = _as_utc(now)
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    if (
        run.dispatch_attempt_count >= settings.report_dispatch_max_attempts
        and run.dispatch_published_at is None
    ):
        _settle_report_dispatch_exhausted(
            db,
            run=run,
            report_id=report_id,
            observed_at=observed_at,
        )
    else:
        run.dispatch_error = (
            "The report worker queue did not accept this dispatch attempt. "
            "ThreatLens will retry automatically."
        )
        run.dispatch_next_attempt_at = observed_at + timedelta(
            seconds=_retry_delay_seconds(run.dispatch_attempt_count)
        )
        db.add(run)
    return True


def list_due_report_dispatches(
    db: Session,
    *,
    now: datetime,
    limit: int | None = None,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    settings = get_settings()
    observed_at = _as_utc(now)
    rows = db.execute(
        select(AITaskRun.report_id, AITaskRun.id)
        .join(Report, Report.id == AITaskRun.report_id)
        .where(
            AITaskRun.task_type == AI_TASK_TYPE_REPORT,
            AITaskRun.status == AI_STATUS_QUEUED,
            AITaskRun.finished_at.is_(None),
            or_(
                AITaskRun.dispatch_claim_token.is_(None),
                AITaskRun.dispatch_claim_expires_at.is_(None),
                AITaskRun.dispatch_claim_expires_at <= observed_at,
            ),
            or_(
                AITaskRun.dispatch_next_attempt_at.is_(None),
                AITaskRun.dispatch_next_attempt_at <= observed_at,
            ),
            Report.status.in_(["queued", "running"]),
        )
        .order_by(AITaskRun.dispatch_next_attempt_at.asc(), AITaskRun.created_at.asc())
        .limit(limit or settings.report_dispatch_batch_size)
    ).all()
    return [(report_id, run_id) for report_id, run_id in rows if report_id is not None]


def stable_report_task_id(task_run_id: uuid.UUID) -> str:
    return f"report-{task_run_id}"


def _load_claimed_dispatch(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    dispatch_token: str,
) -> AITaskRun | None:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not _is_dispatchable(run, report_id=report_id):
        return None
    if run.dispatch_claim_token != dispatch_token:
        return None
    return run


def _settle_report_dispatch_exhausted(
    db: Session,
    *,
    run: AITaskRun,
    report_id: uuid.UUID,
    observed_at: datetime,
) -> None:
    run.dispatch_error = (
        "The report worker queue did not confirm delivery after repeated attempts. "
        "No further automatic dispatch attempts will be made; retry the report after the worker queue recovers."
    )
    run.dispatch_next_attempt_at = None
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    report = db.scalar(select(Report).where(Report.id == report_id).with_for_update())
    if report is not None and report.status in {"queued", "running"}:
        report.status = "error"
        report.generation_stage = "failed"
        report.error_code = "enqueue_failed"
        report.error = (
            "The report could not be delivered to a report worker after repeated attempts. "
            "Retry it after the worker queue recovers."
        )
        report.generation_lease_token = None
        report.generation_lease_expires_at = None
        db.add(report)
    db.execute(
        update(ReportGenerationLease)
        .where(ReportGenerationLease.report_id == report_id)
        .values(
            generation_fence=ReportGenerationLease.generation_fence + 1,
            lease_token=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.add(run)
    db.flush()
    finish_ai_task_run(
        db,
        run_id=run.id,
        status=AI_STATUS_ERROR,
        reason="enqueue_failed",
        error=run.dispatch_error,
        worker_name="dispatcher",
        report_id=report_id,
        metadata_updates={
            "dispatch_attempt_count": run.dispatch_attempt_count,
            "dispatch_exhausted_at": observed_at.isoformat(),
        },
    )


def _is_dispatchable(run: AITaskRun | None, *, report_id: uuid.UUID) -> bool:
    return bool(
        run is not None
        and run.task_type == AI_TASK_TYPE_REPORT
        and run.report_id == report_id
        and run.status == AI_STATUS_QUEUED
        and run.finished_at is None
    )


def _retry_delay_seconds(attempt: int) -> int:
    settings = get_settings()
    return min(
        settings.report_dispatch_retry_max_backoff_seconds,
        settings.report_dispatch_retry_backoff_seconds * (2 ** max(0, attempt - 1)),
    )


def _as_optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ReportDispatchClaim",
    "claim_report_dispatch",
    "initialize_report_dispatch",
    "list_due_report_dispatches",
    "record_report_dispatch_failure",
    "record_report_dispatch_success",
    "stable_report_task_id",
]
