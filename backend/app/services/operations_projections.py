from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


_BACKUP_FRESHNESS = timedelta(hours=26)
_RESTORE_DRILL_FRESHNESS = timedelta(days=31)


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
    if not database_ok:
        return OperationsRecoverySnapshot()

    loaded = safe_db_probe(
        db,
        "recovery_history",
        lambda: _load_recovery_state(db),
        None,
    )
    if loaded is None:
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
    recovery, correlation = loaded
    _append_recovery_issues(recovery, issues, correlation=correlation)
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
    correlation = _load_recovery_correlation(db)
    latest_backup = _latest_recovery_run(db, "backup")
    latest_restore = _latest_recovery_run(db, "restore")
    latest_verify = correlation.verify
    latest_restore_drill = correlation.restore_drill
    if correlation.backup is None:
        latest_verify = _latest_recovery_run(db, "verify")
        latest_restore_drill = _latest_recovery_run(db, "restore_drill")
    return OperationsRecoverySnapshot(
        latest_backup=latest_backup,
        latest_verify=latest_verify,
        latest_restore_drill=latest_restore_drill,
        latest_restore=latest_restore,
    ), correlation


def _latest_recovery_run(
    db: Session,
    operation_type: str,
    *,
    status: str | None = None,
    archive_sha256: str | None = None,
) -> SystemOperationRunResponse | None:
    filters = [SystemOperationRun.operation_type == operation_type]
    if status is not None:
        filters.append(SystemOperationRun.status == status)
    if archive_sha256 is not None:
        filters.append(
            SystemOperationRun.metadata_json["archive_sha256"].as_string()
            == archive_sha256
        )
    model = db.scalar(
        select(SystemOperationRun)
        .where(*filters)
        .order_by(SystemOperationRun.started_at.desc(), SystemOperationRun.id.desc())
        .limit(1)
    )
    return system_operation_run_response(model) if model is not None else None


def _load_recovery_correlation(db: Session) -> _RecoveryCorrelation:
    backup = _latest_recovery_run(db, "backup", status="succeeded")
    if backup is None:
        return _RecoveryCorrelation(
            backup=None,
            verify=None,
            restore_drill=None,
            latest_successful_verify=_latest_recovery_run(
                db, "verify", status="succeeded"
            ),
            latest_successful_restore_drill=_latest_recovery_run(
                db, "restore_drill", status="succeeded"
            ),
        )
    checksum = _archive_checksum(backup)
    if checksum is None:
        return _RecoveryCorrelation(
            backup=backup,
            verify=None,
            restore_drill=None,
            latest_successful_verify=_latest_recovery_run(
                db, "verify", status="succeeded"
            ),
            latest_successful_restore_drill=_latest_recovery_run(
                db, "restore_drill", status="succeeded"
            ),
        )

    return _RecoveryCorrelation(
        backup=backup,
        verify=_latest_recovery_run(db, "verify", archive_sha256=checksum),
        restore_drill=_latest_recovery_run(
            db, "restore_drill", archive_sha256=checksum
        ),
        latest_successful_verify=_latest_recovery_run(
            db, "verify", status="succeeded"
        ),
        latest_successful_restore_drill=_latest_recovery_run(
            db, "restore_drill", status="succeeded"
        ),
    )


def _append_recovery_issues(
    recovery: OperationsRecoverySnapshot,
    issues: list[OperationsIssue],
    *,
    correlation: _RecoveryCorrelation,
) -> None:
    observed_at = datetime.now(timezone.utc)
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
    elif recovery.latest_backup.status == "running":
        issues.append(
            issue(
                "latest_backup_incomplete",
                "critical",
                "recovery",
                "The latest backup run has not completed.",
                "No completed archive is proven for the latest backup attempt.",
                "Inspect the host recovery process and do not treat its partial directory as a backup.",
            )
        )
    elif _run_is_stale(
        recovery.latest_backup, observed_at=observed_at, maximum_age=_BACKUP_FRESHNESS
    ):
        issues.append(
            issue(
                "latest_backup_stale",
                "warning",
                "recovery",
                "The latest successful backup is older than 26 hours.",
                "The recoverable data point may be outside a daily backup objective.",
                "Run and verify a fresh offline backup, then confirm off-host retention.",
            )
        )
    if recovery.latest_restore_drill is None and correlation.backup is None:
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
    if (
        recovery.latest_restore is not None
        and recovery.latest_restore.status == "failed"
    ):
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
    elif (
        recovery.latest_restore is not None
        and recovery.latest_restore.status == "running"
    ):
        issues.append(
            issue(
                "latest_restore_incomplete",
                "critical",
                "recovery",
                "The latest destructive restore has not recorded completion.",
                "Database identity, quarantine, or connectivity may require operator verification.",
                "Keep application services stopped and follow the offline rollback inspection runbook.",
            )
        )
    _append_recovery_correlation_issues(
        correlation,
        issues,
        observed_at=observed_at,
    )


def _append_recovery_correlation_issues(
    correlation: _RecoveryCorrelation,
    issues: list[OperationsIssue],
    *,
    observed_at: datetime,
) -> None:
    backup = correlation.backup
    if backup is None:
        return
    backup_checksum = _archive_checksum(backup)
    if backup_checksum is None:
        issues.append(
            issue(
                "latest_backup_identity_missing",
                "warning",
                "recovery",
                "The latest successful backup has no archive identity metadata.",
                "Verification and drill evidence cannot be correlated to that backup.",
                "Create a new backup with the supported host recovery utility.",
            )
        )
        return

    _append_artifact_correlation_issue(
        backup_checksum=backup_checksum,
        evidence=correlation.verify,
        latest_successful_evidence=correlation.latest_successful_verify,
        missing_code="latest_backup_not_verified",
        mismatch_code="latest_backup_verify_mismatch",
        failed_code="latest_backup_verify_failed",
        incomplete_code="latest_backup_verify_incomplete",
        evidence_label="verification",
        observed_at=observed_at,
        issues=issues,
    )
    _append_artifact_correlation_issue(
        backup_checksum=backup_checksum,
        evidence=correlation.restore_drill,
        latest_successful_evidence=correlation.latest_successful_restore_drill,
        missing_code="latest_backup_not_drilled",
        mismatch_code="latest_backup_drill_mismatch",
        failed_code="latest_restore_drill_failed",
        incomplete_code="latest_restore_drill_incomplete",
        evidence_label="restore drill",
        stale_code="latest_restore_drill_stale",
        stale_after=_RESTORE_DRILL_FRESHNESS,
        observed_at=observed_at,
        issues=issues,
    )


def _append_artifact_correlation_issue(
    *,
    backup_checksum: str,
    evidence: SystemOperationRunResponse | None,
    latest_successful_evidence: SystemOperationRunResponse | None,
    missing_code: str,
    mismatch_code: str,
    failed_code: str,
    incomplete_code: str,
    evidence_label: str,
    observed_at: datetime,
    stale_code: str | None = None,
    stale_after: timedelta | None = None,
    issues: list[OperationsIssue],
) -> None:
    if evidence is None:
        if latest_successful_evidence is not None:
            issues.append(
                issue(
                    mismatch_code,
                    "warning",
                    "recovery",
                    f"The latest successful {evidence_label} covers a different archive.",
                    "The newest backup cannot inherit evidence from older recovery material.",
                    f"Run the supported {evidence_label} workflow against the latest backup checksum.",
                )
            )
            return
        issues.append(
            issue(
                missing_code,
                "warning",
                "recovery",
                f"The latest backup has no successful correlated {evidence_label}.",
                "The newest archive is not proven by the corresponding recovery check.",
                f"Run the supported {evidence_label} workflow against the latest backup.",
            )
        )
        return
    if evidence.status == "failed":
        issues.append(
            issue(
                failed_code,
                "critical",
                "recovery",
                f"The latest {evidence_label} for the newest backup failed.",
                "The current recovery material is not proven by this recovery check.",
                f"Correct the failure and rerun the supported {evidence_label} workflow against the latest backup.",
            )
        )
        return
    if evidence.status == "running":
        issues.append(
            issue(
                incomplete_code,
                "warning",
                "recovery",
                f"The latest {evidence_label} for the newest backup has not completed.",
                "The current recovery material cannot be treated as fully proven yet.",
                f"Inspect the host process and rerun the supported {evidence_label} workflow if it is no longer active.",
            )
        )
        return
    evidence_checksum = _archive_checksum(evidence)
    if evidence_checksum != backup_checksum:
        issues.append(
            issue(
                mismatch_code,
                "warning",
                "recovery",
                f"The latest successful {evidence_label} covers a different archive.",
                "The newest backup cannot inherit evidence from older recovery material.",
                f"Run the supported {evidence_label} workflow against the latest backup checksum.",
            )
        )
        return
    if (
        stale_code is not None
        and stale_after is not None
        and _run_is_stale(
            evidence,
            observed_at=observed_at,
            maximum_age=stale_after,
        )
    ):
        issues.append(
            issue(
                stale_code,
                "warning",
                "recovery",
                "The latest successful restore drill for the newest backup is older than 31 days.",
                "Current images, migrations, and quarantine logic have not been proven recently.",
                "Run the isolated packaged-code restore drill against a current backup.",
            )
        )


def _archive_checksum(run: SystemOperationRunResponse) -> str | None:
    value = run.metadata.get("archive_sha256")
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _run_is_stale(
    run: SystemOperationRunResponse,
    *,
    observed_at: datetime,
    maximum_age: timedelta,
) -> bool:
    reference = run.finished_at or run.started_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return observed_at - reference.astimezone(timezone.utc) > maximum_age


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
