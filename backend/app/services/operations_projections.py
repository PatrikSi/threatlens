from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
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
from app.services.ai_ops_common import AI_STATUS_ERROR, AI_STATUS_QUEUED, AI_STATUS_RUNNING
from app.services.integration_delivery import (
    DELIVERY_DEAD_LETTER,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_RETRY_WAIT,
    DELIVERY_SENDING,
)
from app.services.operations_common import issue, safe_db_probe, seconds_since
from app.services.operations_runs import system_operation_run_response


_RECOVERY_OPERATION_TYPES = ("backup", "verify", "restore_drill", "restore")


def collect_backlog_snapshots(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
    issues: list[OperationsIssue],
    database_ok: bool,
) -> list[OperationsBacklogSnapshot]:
    delivery_threshold = max(1, int(settings.notification_delivery_queue_degraded_after_seconds))
    report_threshold = max(1, int(settings.report_dispatch_start_grace_seconds))
    if not database_ok:
        return [
            _unknown_backlog("integration_deliveries", "Integration deliveries", delivery_threshold),
            _unknown_backlog("reports", "Report generation", report_threshold),
        ]

    delivery = safe_db_probe(
        db,
        "integration_delivery_backlog",
        lambda: _load_delivery_backlog(db, settings=settings, now=now),
        _unknown_backlog("integration_deliveries", "Integration deliveries", delivery_threshold),
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
    if not database_ok:
        return OperationsRecoverySnapshot()

    recovery = safe_db_probe(
        db,
        "recovery_history",
        lambda: _load_recovery_snapshot(db),
        None,
    )
    if recovery is None:
        issues.append(
            issue(
                "recovery_history_unavailable",
                "warning",
                "recovery",
                "Recovery operation history could not be read.",
                "Recent backup and restore-drill outcomes cannot be confirmed.",
                "Check database access and query the operation-run ledger offline.",
            )
        )
        return OperationsRecoverySnapshot()
    _append_recovery_issues(recovery, issues)
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
            func.count(IntegrationDelivery.id).filter(IntegrationDelivery.state.in_(pending_states)),
            func.count(IntegrationDelivery.id).filter(IntegrationDelivery.state == DELIVERY_SENDING),
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
    stale_cutoff = now - timedelta(seconds=max(1, int(settings.report_generation_lease_seconds)))
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


def _load_recovery_snapshot(db: Session) -> OperationsRecoverySnapshot:
    latest: dict[str, SystemOperationRunResponse | None] = {}
    for operation_type in _RECOVERY_OPERATION_TYPES:
        run = db.scalar(
            select(SystemOperationRun)
            .where(SystemOperationRun.operation_type == operation_type)
            .order_by(SystemOperationRun.started_at.desc(), SystemOperationRun.id.desc())
            .limit(1)
        )
        latest[operation_type] = system_operation_run_response(run) if run is not None else None
    return OperationsRecoverySnapshot(
        latest_backup=latest["backup"],
        latest_verify=latest["verify"],
        latest_restore_drill=latest["restore_drill"],
        latest_restore=latest["restore"],
    )


def _append_recovery_issues(
    recovery: OperationsRecoverySnapshot,
    issues: list[OperationsIssue],
) -> None:
    if recovery.latest_backup is None:
        issues.append(
            issue(
                "backup_not_recorded",
                "warning",
                "recovery",
                "No backup run has been recorded.",
                "The operations view cannot confirm that a recoverable backup exists.",
                "Run the supported offline backup workflow and verify its archive.",
            )
        )
    elif recovery.latest_backup.status == "failed":
        issues.append(
            issue(
                "latest_backup_failed",
                "critical",
                "recovery",
                "The latest backup run failed.",
                "Recent durable state may not have a recoverable backup.",
                "Review the run error code, correct the backup target, and retry offline.",
            )
        )
    if recovery.latest_restore_drill is None:
        issues.append(
            issue(
                "restore_drill_not_recorded",
                "warning",
                "recovery",
                "No isolated restore drill has been recorded.",
                "Archive integrity has not been proven by restoring into an isolated database.",
                "Run the supported offline restore drill without connecting it to production workers.",
            )
        )
    elif recovery.latest_restore_drill.status == "failed":
        issues.append(
            issue(
                "latest_restore_drill_failed",
                "critical",
                "recovery",
                "The latest isolated restore drill failed.",
                "The current recovery material is not proven restorable.",
                "Correct the recorded failure and complete another isolated restore drill.",
            )
        )
    if recovery.latest_verify is not None and recovery.latest_verify.status == "failed":
        issues.append(
            issue(
                "latest_backup_verify_failed",
                "critical",
                "recovery",
                "The latest backup verification run failed.",
                "The recorded archive cannot currently be trusted for recovery.",
                "Correct the verification failure and verify a fresh archive offline.",
            )
        )
    if recovery.latest_restore is not None and recovery.latest_restore.status == "failed":
        issues.append(
            issue(
                "latest_restore_failed",
                "critical",
                "recovery",
                "The latest restore run failed.",
                "Recovery may be incomplete and the restored instance may be unsafe to serve.",
                "Keep outbound work quarantined and complete the documented recovery checks offline.",
            )
        )


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
