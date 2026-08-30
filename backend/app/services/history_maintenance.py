from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.action_approval import ActionApprovalRequest, ActionExecutionReceipt
from app.models.audit_log import AuditLog
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.integration import IntegrationRun
from app.models.report import Report
from app.models.tag import TagFeedbackEvent
from app.services.auth_sessions import cleanup_auth_sessions
from app.services.audit import record_audit
from app.services.local_mfa import (
    cleanup_mfa_challenges,
    cleanup_pending_totp_enrollments,
)

settings = get_settings()


@dataclass(frozen=True)
class HistoryMaintenanceResult:
    audit_logs_deleted: int
    ai_task_runs_deleted: int
    ai_usage_events_deleted: int
    tag_feedback_events_deleted: int
    integration_runs_deleted: int
    auth_sessions_deleted: int
    mfa_challenges_deleted: int
    pending_mfa_enrollments_deleted: int
    action_approval_requests_deleted: int
    action_execution_receipts_deleted: int
    action_operation_receipts_deleted: int


def prune_application_history(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> HistoryMaintenanceResult:
    current_time = now or datetime.now(timezone.utc)
    effective_batch_size = max(
        1, int(batch_size or settings.integration_delivery_maintenance_batch_size)
    )
    (
        approval_requests_deleted,
        execution_receipts_deleted,
        action_operation_receipts_deleted,
    ) = _delete_action_approval_history(
        db,
        cutoff=current_time
        - timedelta(days=max(1, int(settings.action_approval_retention_days))),
        now=current_time,
        batch_size=effective_batch_size,
    )
    deleted = HistoryMaintenanceResult(
        audit_logs_deleted=_delete_older_than(
            db,
            AuditLog,
            AuditLog.created_at,
            current_time
            - timedelta(days=max(1, int(settings.audit_log_retention_days))),
            effective_batch_size,
        ),
        ai_task_runs_deleted=_delete_older_than(
            db,
            AITaskRun,
            AITaskRun.finished_at,
            current_time
            - timedelta(days=max(1, int(settings.ai_task_history_retention_days))),
            effective_batch_size,
            extra_predicate=and_(
                AITaskRun.finished_at.is_not(None),
                ~select(Report.id)
                .where(
                    Report.request_task_run_id == AITaskRun.id,
                )
                .exists(),
            ),
        ),
        ai_usage_events_deleted=_delete_older_than(
            db,
            AIUsageEvent,
            AIUsageEvent.created_at,
            current_time
            - timedelta(days=max(1, int(settings.ai_usage_retention_days))),
            effective_batch_size,
        ),
        tag_feedback_events_deleted=_delete_older_than(
            db,
            TagFeedbackEvent,
            TagFeedbackEvent.created_at,
            current_time
            - timedelta(days=max(1, int(settings.tag_feedback_retention_days))),
            effective_batch_size,
        ),
        integration_runs_deleted=_delete_older_than(
            db,
            IntegrationRun,
            IntegrationRun.finished_at,
            current_time
            - timedelta(days=max(1, int(settings.integration_run_retention_days))),
            effective_batch_size,
            extra_predicate=IntegrationRun.finished_at.is_not(None),
        ),
        auth_sessions_deleted=cleanup_auth_sessions(
            db,
            retention_days=settings.auth_session_retention_days,
            now=current_time,
            limit=effective_batch_size,
        ),
        mfa_challenges_deleted=cleanup_mfa_challenges(
            db,
            now=current_time,
            limit=effective_batch_size,
        ),
        pending_mfa_enrollments_deleted=cleanup_pending_totp_enrollments(
            db,
            now=current_time,
            limit=effective_batch_size,
        ),
        action_approval_requests_deleted=approval_requests_deleted,
        action_execution_receipts_deleted=execution_receipts_deleted,
        action_operation_receipts_deleted=action_operation_receipts_deleted,
    )
    if deleted.audit_logs_deleted:
        record_audit(
            db,
            actor_user_id=None,
            actor_principal_type="system",
            action="history.audit.prune",
            resource_type="audit_log",
            metadata={
                "deleted_count": deleted.audit_logs_deleted,
                "retention_days": max(1, int(settings.audit_log_retention_days)),
                "batch_size": effective_batch_size,
                "completed_at": current_time.isoformat(),
            },
        )
    if deleted.action_approval_requests_deleted:
        record_audit(
            db,
            actor_user_id=None,
            actor_principal_type="system",
            action="history.action_approvals.prune",
            resource_type="action_approval",
            metadata={
                "deleted_requests": deleted.action_approval_requests_deleted,
                "deleted_execution_receipts": deleted.action_execution_receipts_deleted,
                "deleted_operation_receipts": deleted.action_operation_receipts_deleted,
                "retention_days": max(1, int(settings.action_approval_retention_days)),
                "batch_size": effective_batch_size,
                "completed_at": current_time.isoformat(),
            },
        )
    db.commit()
    return deleted


def _delete_older_than(
    db, model, timestamp_column, cutoff, batch_size, *, extra_predicate=None
) -> int:
    query = select(model.id).where(timestamp_column < cutoff)
    if extra_predicate is not None:
        query = query.where(extra_predicate)
    ids = list(
        db.scalars(
            query.order_by(timestamp_column.asc(), model.id.asc()).limit(batch_size)
        ).all()
    )
    if not ids:
        return 0
    result = db.execute(
        delete(model)
        .where(model.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _delete_action_approval_history(
    db: Session,
    *,
    cutoff: datetime,
    now: datetime,
    batch_size: int,
) -> tuple[int, int, int]:
    approval_ids = list(
        db.scalars(
            select(ActionApprovalRequest.id)
            .where(
                ActionApprovalRequest.created_at < cutoff,
                (
                    ActionApprovalRequest.status.in_(
                        ["denied", "cancelled", "invalidated", "executed"]
                    )
                    | (ActionApprovalRequest.expires_at <= now)
                ),
            )
            .order_by(
                ActionApprovalRequest.created_at.asc(),
                ActionApprovalRequest.id.asc(),
            )
            .limit(batch_size)
        ).all()
    )
    if not approval_ids:
        return 0, 0, 0
    operation_receipt_result = db.execute(
        delete(GovernanceOperationReceipt)
        .where(
            GovernanceOperationReceipt.resource_type == "action_approval",
            GovernanceOperationReceipt.resource_id.in_(approval_ids),
        )
        .execution_options(synchronize_session=False)
    )
    receipt_result = db.execute(
        delete(ActionExecutionReceipt)
        .where(ActionExecutionReceipt.approval_request_id.in_(approval_ids))
        .execution_options(synchronize_session=False)
    )
    request_result = db.execute(
        delete(ActionApprovalRequest)
        .where(ActionApprovalRequest.id.in_(approval_ids))
        .execution_options(synchronize_session=False)
    )
    return (
        int(request_result.rowcount or 0),
        int(receipt_result.rowcount or 0),
        int(operation_receipt_result.rowcount or 0),
    )
