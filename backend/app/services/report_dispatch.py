from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.ai_ops import AI_STATUS_ERROR, finish_ai_task_run
from app.services.ai_ops_common import AI_STATUS_QUEUED, AI_TASK_TYPE_REPORT


@dataclass(frozen=True)
class ReportDispatchClaim:
    claimed: bool
    celery_task_id: str | None = None


def initialize_report_dispatch(run: AITaskRun, *, now: datetime | None = None) -> None:
    run.dispatch_attempt_count = 0
    run.dispatch_next_attempt_at = _as_utc(now or datetime.now(timezone.utc))
    run.dispatch_error = None


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
        return ReportDispatchClaim(False, run.celery_task_id if run else None)
    if run.celery_task_id:
        return ReportDispatchClaim(False, run.celery_task_id)

    observed_at = _as_utc(now)
    next_attempt_at = _as_utc(run.dispatch_next_attempt_at)
    if next_attempt_at is not None and next_attempt_at > observed_at:
        return ReportDispatchClaim(False)

    run.dispatch_next_attempt_at = observed_at + timedelta(
        seconds=_retry_delay_seconds(int(run.dispatch_attempt_count or 0) + 1)
    )
    db.add(run)
    return ReportDispatchClaim(True)


def record_report_dispatch_success(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    celery_task_id: str,
) -> bool:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not _is_dispatchable(run, report_id=report_id):
        return False
    run.celery_task_id = celery_task_id
    run.dispatch_next_attempt_at = None
    run.dispatch_error = None
    db.add(run)
    return True


def record_report_dispatch_failure(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    now: datetime,
) -> bool:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not _is_dispatchable(run, report_id=report_id) or run.celery_task_id:
        return False

    settings = get_settings()
    observed_at = _as_utc(now)
    run.dispatch_attempt_count = int(run.dispatch_attempt_count or 0) + 1
    run.dispatch_error = (
        "The report worker queue did not accept this dispatch attempt. "
        "ThreatLens will retry automatically."
    )
    if run.dispatch_attempt_count >= settings.report_dispatch_max_attempts:
        run.dispatch_next_attempt_at = None
        report = db.scalar(
            select(Report).where(Report.id == report_id).with_for_update()
        )
        if report is not None and report.status in {"queued", "running"}:
            report.status = "error"
            report.generation_stage = "failed"
            report.error_code = "enqueue_failed"
            report.error = (
                "The report could not be queued after repeated attempts. "
                "Retry it after the worker queue recovers."
            )
            report.generation_lease_token = None
            report.generation_lease_expires_at = None
            db.add(report)
        db.add(run)
        db.flush()
        finish_ai_task_run(
            db,
            run_id=task_run_id,
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
    else:
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
    rows = db.execute(
        select(AITaskRun.report_id, AITaskRun.id)
        .join(Report, Report.id == AITaskRun.report_id)
        .where(
            AITaskRun.task_type == AI_TASK_TYPE_REPORT,
            AITaskRun.status == AI_STATUS_QUEUED,
            AITaskRun.finished_at.is_(None),
            AITaskRun.celery_task_id.is_(None),
            AITaskRun.dispatch_attempt_count < settings.report_dispatch_max_attempts,
            or_(
                AITaskRun.dispatch_next_attempt_at.is_(None),
                AITaskRun.dispatch_next_attempt_at <= _as_utc(now),
            ),
            Report.status.in_(["queued", "running"]),
        )
        .order_by(AITaskRun.dispatch_next_attempt_at.asc(), AITaskRun.created_at.asc())
        .limit(limit or settings.report_dispatch_batch_size)
    ).all()
    return [(report_id, run_id) for report_id, run_id in rows if report_id is not None]


def stable_report_task_id(task_run_id: uuid.UUID) -> str:
    return f"report-{task_run_id}"


def _is_dispatchable(
    run: AITaskRun | None,
    *,
    report_id: uuid.UUID,
) -> bool:
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
