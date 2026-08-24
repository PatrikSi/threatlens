from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.ai_ops_common import (
    AI_STATUS_QUEUED,
    AI_TASK_TYPE_REPORT,
)
from app.services.report_task_lineage import (
    find_report_request_task_run,
    resolve_report_task_run,
)


@dataclass(frozen=True)
class ReportDispatchClaim:
    claimed: bool
    dispatch_token: str | None = None
    celery_task_id: str | None = None


def initialize_report_dispatch(run: AITaskRun, *, now: datetime | None = None) -> None:
    run.dispatch_attempt_count = 0
    run.dispatch_next_attempt_at = _as_utc(now or datetime.now(timezone.utc))
    run.dispatch_error = None
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    run.dispatch_published_at = None


def supersede_legacy_report_dispatch(
    db: Session,
    *,
    report_id: uuid.UUID,
    task_run_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    """Replace queued v1 work so delayed old messages cannot share its run."""

    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        run is not None
        and run.task_type == AI_TASK_TYPE_REPORT
        and run.report_id == report_id
    ):
        canonical = resolve_report_task_run(db, run)
        if canonical.id != run.id:
            return canonical.id
    if (
        not _is_dispatchable(run, report_id=report_id)
        or int(run.dispatch_protocol_version or 1) >= 2
    ):
        return task_run_id
    report = db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if report is None or report.status not in {"queued", "running"}:
        return task_run_id

    request_run = find_report_request_task_run(db, report=report)
    observed_at = _as_utc(now)
    replacement_id = uuid.uuid4()
    request_key_hash = run.request_idempotency_key_hash
    request_fingerprint = run.request_fingerprint
    run.status = "skipped"
    run.reason = "superseded_for_fenced_dispatch"
    run.finished_at = observed_at
    run.dispatch_next_attempt_at = None
    run.dispatch_error = None
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    run.request_idempotency_key_hash = None
    run.request_fingerprint = None
    run.metadata_json = {
        **(run.metadata_json or {}),
        "superseded_by_task_run_id": str(replacement_id),
    }
    db.add(run)
    db.add(
        AITaskEvent(
            task_run_id=run.id,
            event_type="superseded",
            message="Queued legacy report work was replaced by a fenced dispatch.",
            payload_json={"replacement_task_run_id": str(replacement_id)},
        )
    )
    # Release active-run and idempotency uniqueness before inserting the replacement.
    db.flush()

    replacement_metadata = {
        **(run.metadata_json or {}),
        "supersedes_task_run_id": str(run.id),
    }
    replacement_metadata.pop("superseded_by_task_run_id", None)
    replacement = AITaskRun(
        id=replacement_id,
        task_type=AI_TASK_TYPE_REPORT,
        trigger_source=run.trigger_source,
        status=AI_STATUS_QUEUED,
        actor_user_id=run.actor_user_id,
        report_id=run.report_id,
        parent_run_id=run.parent_run_id,
        model=run.model,
        request_idempotency_key_hash=request_key_hash,
        request_fingerprint=request_fingerprint,
        metadata_json=replacement_metadata,
        target_count=run.target_count,
        queued_at=observed_at,
        created_at=observed_at,
        updated_at=observed_at,
        dispatch_protocol_version=2,
    )
    initialize_report_dispatch(replacement, now=observed_at)
    db.add(replacement)
    db.flush()
    run.superseded_by_task_run_id = replacement.id
    db.add(run)
    if request_run is not None:
        report.request_task_run_id = (
            replacement.id if request_run.id == run.id else request_run.id
        )
        db.add(report)
    db.add(
        AITaskEvent(
            task_run_id=replacement.id,
            event_type="queued",
            message="Fenced replacement for queued legacy report work.",
            payload_json={"superseded_task_run_id": str(run.id)},
        )
    )
    db.flush()
    return replacement.id


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

    if not _dispatch_is_due(
        run,
        now=observed_at,
        start_grace_seconds=settings.report_dispatch_start_grace_seconds,
    ):
        return ReportDispatchClaim(False, celery_task_id=run.celery_task_id)

    attempt_count = int(run.dispatch_attempt_count or 0)
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
    observed_at = _as_utc(now)
    run.celery_task_id = celery_task_id
    run.dispatch_attempt_count = 0
    run.dispatch_published_at = observed_at
    run.dispatch_next_attempt_at = None
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

    observed_at = _as_utc(now)
    run.dispatch_claim_token = None
    run.dispatch_claim_expires_at = None
    run.dispatch_error = (
        "The report worker queue did not confirm this publication attempt. "
        "Its outcome is unknown, so ThreatLens will retry safely with the same task identity."
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
    recovery_before = observed_at - timedelta(
        seconds=settings.report_dispatch_start_grace_seconds
    )
    dispatch_due = or_(
        and_(
            AITaskRun.dispatch_next_attempt_at.is_not(None),
            AITaskRun.dispatch_next_attempt_at <= observed_at,
        ),
        and_(
            AITaskRun.dispatch_next_attempt_at.is_(None),
            AITaskRun.dispatch_published_at.is_not(None),
            AITaskRun.dispatch_published_at <= recovery_before,
        ),
        and_(
            AITaskRun.dispatch_next_attempt_at.is_(None),
            AITaskRun.dispatch_published_at.is_(None),
            func.coalesce(AITaskRun.queued_at, AITaskRun.created_at)
            <= recovery_before,
        ),
    )
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
            dispatch_due,
            Report.status.in_(["queued", "running"]),
        )
        .order_by(
            func.coalesce(
                AITaskRun.dispatch_next_attempt_at,
                AITaskRun.dispatch_published_at,
                AITaskRun.queued_at,
                AITaskRun.created_at,
            ).asc(),
            AITaskRun.created_at.asc(),
        )
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


def _is_dispatchable(run: AITaskRun | None, *, report_id: uuid.UUID) -> bool:
    return bool(
        run is not None
        and run.task_type == AI_TASK_TYPE_REPORT
        and run.report_id == report_id
        and run.status == AI_STATUS_QUEUED
        and run.finished_at is None
    )


def _dispatch_is_due(
    run: AITaskRun,
    *,
    now: datetime,
    start_grace_seconds: int,
) -> bool:
    next_attempt_at = _as_optional_utc(run.dispatch_next_attempt_at)
    if next_attempt_at is not None:
        return next_attempt_at <= now
    published_at = _as_optional_utc(run.dispatch_published_at)
    if published_at is not None:
        return published_at + timedelta(seconds=start_grace_seconds) <= now
    queued_at = _as_optional_utc(run.queued_at or run.created_at)
    return bool(
        queued_at is not None
        and queued_at + timedelta(seconds=start_grace_seconds) <= now
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
    "supersede_legacy_report_dispatch",
]
