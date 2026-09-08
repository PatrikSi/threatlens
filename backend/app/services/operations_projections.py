from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, union_all
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.integration import IntegrationDelivery
from app.models.report import Report
from app.models.system_operation_run import SystemOperationRun
from app.schemas.operations import (
    OperationsBacklogSnapshot,
    OperationsIssue,
    OperationsRecoverySnapshot,
    SystemOperationRunResponse,
)
from app.services.ai_ops_common import (
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_RUNNING,
)
from app.services.integration_delivery import (
    DELIVERY_DEAD_LETTER,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_RETRY_WAIT,
    DELIVERY_SENDING,
)
from app.services.operations_common import issue, safe_db_probe, seconds_since
from app.services.operations_runs import system_operation_run_response


@dataclass(frozen=True)
class _RecoveryCorrelation:
    backup: SystemOperationRunResponse | None
    verify: SystemOperationRunResponse | None
    restore_drill: SystemOperationRunResponse | None
    latest_successful_verify: SystemOperationRunResponse | None
    latest_successful_restore_drill: SystemOperationRunResponse | None


def collect_backlog_snapshots(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
    issues: list[OperationsIssue],
    database_ok: bool,
) -> list[OperationsBacklogSnapshot]:
    delivery_threshold = max(
        1, int(settings.notification_delivery_queue_degraded_after_seconds)
    )
    report_threshold = max(1, int(settings.report_dispatch_start_grace_seconds))
    if not database_ok:
        return [
            _unknown_backlog(
                "integration_deliveries", "Integration deliveries", delivery_threshold
            ),
            _unknown_backlog("reports", "Report generation", report_threshold),
        ]

    delivery = safe_db_probe(
        db,
        "integration_delivery_backlog",
        lambda: _load_delivery_backlog(db, settings=settings, now=now),
        _unknown_backlog(
            "integration_deliveries", "Integration deliveries", delivery_threshold
        ),
    )
    report = safe_db_probe(
        db,
        "report_backlog",
        lambda: _load_report_backlog(db, settings=settings, now=now),
        _unknown_backlog("reports", "Report generation", report_threshold),
    )
    _append_backlog_issue(delivery, issues)
    _append_backlog_issue(report, issues)
    return [delivery, report]


def collect_recovery_snapshot(
    db: Session,
    *,
    issues: list[OperationsIssue],
    database_ok: bool,
) -> OperationsRecoverySnapshot:
    # Recovery runs are an informational operator ledger, not a service-health
    # signal. Keep accepting the shared list for compatibility with existing
    # callers while deliberately leaving it unchanged.
    _ = issues
    if not database_ok:
        return OperationsRecoverySnapshot()

    loaded = safe_db_probe(
        db,
        "recovery_history",
        lambda: _load_recovery_state(db),
        None,
    )
    if loaded is None:
        return OperationsRecoverySnapshot()
    recovery, _correlation = loaded
    return recovery


def _load_delivery_backlog(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
) -> OperationsBacklogSnapshot:
    stale_cutoff = now - timedelta(
        seconds=max(1, int(settings.notification_delivery_sending_stale_after_seconds))
    )
    pending_states = (DELIVERY_PENDING, DELIVERY_RETRY_WAIT)
    row = db.execute(
        select(
            func.count(IntegrationDelivery.id).filter(
                IntegrationDelivery.state.in_(pending_states)
            ),
            func.count(IntegrationDelivery.id).filter(
                IntegrationDelivery.state == DELIVERY_SENDING
            ),
            func.count(IntegrationDelivery.id).filter(
                IntegrationDelivery.state == DELIVERY_SENDING,
                or_(
                    IntegrationDelivery.claimed_at.is_(None),
                    IntegrationDelivery.claimed_at < stale_cutoff,
                ),
            ),
            func.count(IntegrationDelivery.id).filter(
                IntegrationDelivery.state.in_((DELIVERY_FAILED, DELIVERY_DEAD_LETTER))
            ),
            func.min(
                func.coalesce(
                    IntegrationDelivery.not_before,
                    IntegrationDelivery.created_at,
                )
            ).filter(IntegrationDelivery.state.in_(pending_states)),
        )
    ).one()
    oldest_age = seconds_since(now, row[4])
    threshold = max(1, int(settings.notification_delivery_queue_degraded_after_seconds))
    stale_count = int(row[2] or 0)
    status = (
        "critical"
        if stale_count
        else "degraded"
        if oldest_age is not None and oldest_age >= threshold
        else "healthy"
    )
    return OperationsBacklogSnapshot(
        key="integration_deliveries",
        label="Integration deliveries",
        status=status,
        pending_count=int(row[0] or 0),
        active_count=int(row[1] or 0),
        stale_count=stale_count,
        failed_count=int(row[3] or 0),
        oldest_pending_age_seconds=oldest_age,
        degraded_after_seconds=threshold,
    )


def _load_report_backlog(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
) -> OperationsBacklogSnapshot:
    stale_cutoff = now - timedelta(
        seconds=max(1, int(settings.report_generation_lease_seconds))
    )
    stale_predicate = and_(
        Report.status == AI_STATUS_RUNNING,
        or_(
            Report.generation_lease_expires_at < now,
            and_(
                Report.generation_lease_expires_at.is_(None),
                func.coalesce(Report.started_at, Report.queued_at) < stale_cutoff,
            ),
        ),
    )
    row = db.execute(
        select(
            func.count(Report.id).filter(Report.status == AI_STATUS_QUEUED),
            func.count(Report.id).filter(Report.status == AI_STATUS_RUNNING),
            func.count(Report.id).filter(stale_predicate),
            func.count(Report.id).filter(Report.status == AI_STATUS_ERROR),
            func.min(Report.queued_at).filter(Report.status == AI_STATUS_QUEUED),
        )
    ).one()
    oldest_age = seconds_since(now, row[4])
    threshold = max(1, int(settings.report_dispatch_start_grace_seconds))
    stale_count = int(row[2] or 0)
    status = (
        "critical"
        if stale_count
        else "degraded"
        if oldest_age is not None and oldest_age >= threshold
        else "healthy"
    )
    return OperationsBacklogSnapshot(
        key="reports",
        label="Report generation",
        status=status,
        pending_count=int(row[0] or 0),
        active_count=int(row[1] or 0),
        stale_count=stale_count,
        failed_count=int(row[3] or 0),
        oldest_pending_age_seconds=oldest_age,
        degraded_after_seconds=threshold,
    )


def _load_recovery_state(
    db: Session,
) -> tuple[OperationsRecoverySnapshot, _RecoveryCorrelation]:
    latest_successful_backup_checksum = (
        select(SystemOperationRun.metadata_json["archive_sha256"].as_string())
        .where(
            SystemOperationRun.operation_type == "backup",
            SystemOperationRun.status == "succeeded",
        )
        .order_by(SystemOperationRun.started_at.desc(), SystemOperationRun.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    candidate_ids = union_all(
        _latest_recovery_run_id("backup"),
        _latest_recovery_run_id("restore"),
        _latest_recovery_run_id("verify"),
        _latest_recovery_run_id("restore_drill"),
        _latest_recovery_run_id("backup", status="succeeded"),
        _latest_recovery_run_id("verify", status="succeeded"),
        _latest_recovery_run_id("restore_drill", status="succeeded"),
        _latest_recovery_run_id(
            "verify", archive_sha256=latest_successful_backup_checksum
        ),
        _latest_recovery_run_id(
            "restore_drill", archive_sha256=latest_successful_backup_checksum
        ),
    ).subquery()
    candidates = [
        system_operation_run_response(model)
        for model in db.scalars(
            select(SystemOperationRun).where(
                SystemOperationRun.id.in_(select(candidate_ids.c.run_id))
            )
        ).all()
    ]

    latest_backup = _latest_recovery_candidate(candidates, "backup")
    latest_restore = _latest_recovery_candidate(candidates, "restore")
    backup = _latest_recovery_candidate(candidates, "backup", status="succeeded")
    latest_successful_verify = _latest_recovery_candidate(
        candidates, "verify", status="succeeded"
    )
    latest_successful_restore_drill = _latest_recovery_candidate(
        candidates, "restore_drill", status="succeeded"
    )
    checksum = _archive_checksum(backup) if backup is not None else None
    correlation = _RecoveryCorrelation(
        backup=backup,
        verify=(
            _latest_recovery_candidate(candidates, "verify", archive_sha256=checksum)
            if checksum is not None
            else None
        ),
        restore_drill=(
            _latest_recovery_candidate(
                candidates, "restore_drill", archive_sha256=checksum
            )
            if checksum is not None
            else None
        ),
        latest_successful_verify=latest_successful_verify,
        latest_successful_restore_drill=latest_successful_restore_drill,
    )
    latest_verify = correlation.verify
    latest_restore_drill = correlation.restore_drill
    if correlation.backup is None:
        latest_verify = _latest_recovery_candidate(candidates, "verify")
        latest_restore_drill = _latest_recovery_candidate(candidates, "restore_drill")
    return OperationsRecoverySnapshot(
        latest_backup=latest_backup,
        latest_verify=latest_verify,
        latest_restore_drill=latest_restore_drill,
        latest_restore=latest_restore,
    ), correlation


def _latest_recovery_run_id(
    operation_type: str,
    *,
    status: str | None = None,
    archive_sha256=None,
):
    filters = [SystemOperationRun.operation_type == operation_type]
    if status is not None:
        filters.append(SystemOperationRun.status == status)
    if archive_sha256 is not None:
        filters.append(
            SystemOperationRun.metadata_json["archive_sha256"].as_string()
            == archive_sha256
        )
    return (
        select(SystemOperationRun.id.label("run_id"))
        .where(*filters)
        .order_by(SystemOperationRun.started_at.desc(), SystemOperationRun.id.desc())
        .limit(1)
    )


def _latest_recovery_candidate(
    candidates: list[SystemOperationRunResponse],
    operation_type: str,
    *,
    status: str | None = None,
    archive_sha256: str | None = None,
) -> SystemOperationRunResponse | None:
    matching = [
        run
        for run in candidates
        if run.operation_type == operation_type
        and (status is None or run.status == status)
        and (archive_sha256 is None or _archive_checksum(run) == archive_sha256)
    ]
    return max(matching, key=lambda run: (run.started_at, run.id.int), default=None)


def _archive_checksum(run: SystemOperationRunResponse) -> str | None:
    value = run.metadata.get("archive_sha256")
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _append_backlog_issue(
    backlog: OperationsBacklogSnapshot,
    issues: list[OperationsIssue],
) -> None:
    if backlog.status == "unknown":
        issues.append(
            issue(
                f"{backlog.key}_probe_unavailable",
                "warning",
                backlog.key,
                f"{backlog.label} backlog could not be measured.",
                "Queued-work delay and stale execution cannot be assessed.",
                "Check database access and inspect the corresponding worker logs.",
            )
        )
    elif backlog.stale_count:
        issues.append(
            issue(
                f"{backlog.key}_stale",
                "critical",
                backlog.key,
                f"{backlog.label} has stale active work.",
                "Work may remain incomplete until ownership or delivery recovery runs.",
                "Confirm the responsible worker is healthy, then run the supported recovery workflow.",
            )
        )
    elif backlog.status == "degraded":
        issues.append(
            issue(
                f"{backlog.key}_delayed",
                "warning",
                backlog.key,
                f"{backlog.label} has exceeded its queue-age threshold.",
                "Users may observe delayed processing or delivery.",
                "Check worker capacity and dependency health before adding more work.",
            )
        )


def _unknown_backlog(key: str, label: str, threshold: int) -> OperationsBacklogSnapshot:
    return OperationsBacklogSnapshot(
        key=key,
        label=label,
        status="unknown",
        degraded_after_seconds=threshold,
    )
