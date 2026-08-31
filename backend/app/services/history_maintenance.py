from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
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
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
)
from app.services.data_access_retention import prune_deleted_resource_envelopes
from app.services.data_access_runtime import (
    lock_data_policy_revision_for_derivation,
)
from app.services.local_mfa import (
    cleanup_mfa_challenges,
    cleanup_pending_totp_enrollments,
)

settings = get_settings()


@dataclass(frozen=True)
class HistoryMaintenanceResult:
    audit_logs_deleted: int
    ai_task_runs_deleted: int
    ai_provider_attempt_receipts_deleted: int
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
    lock_data_policy_revision_for_derivation(db)
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
    ai_history_cutoff = current_time - timedelta(
        days=max(1, int(settings.ai_task_history_retention_days))
    )
    ai_usage_events_deleted = _delete_ai_history_with_envelopes(
        db,
        AIUsageEvent,
        AIUsageEvent.created_at,
        current_time - timedelta(days=max(1, int(settings.ai_usage_retention_days))),
        effective_batch_size,
        resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
    )
    ai_task_runs_deleted = _delete_ai_history_with_envelopes(
        db,
        AITaskRun,
        AITaskRun.finished_at,
        ai_history_cutoff,
        effective_batch_size,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        extra_predicate=and_(
            AITaskRun.finished_at.is_not(None),
            ~select(Report.id)
            .where(
                Report.request_task_run_id == AITaskRun.id,
            )
            .exists(),
            ~select(AIProviderAttemptReceipt.id)
            .where(
                AIProviderAttemptReceipt.task_run_id_snapshot == AITaskRun.id,
                _unresolved_ai_provider_receipt(AIProviderAttemptReceipt),
            )
            .exists(),
        ),
    )
    ai_provider_attempt_receipts_deleted = _delete_expired_ai_provider_receipt_ledgers(
        db,
        cutoff=ai_history_cutoff,
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
        ai_task_runs_deleted=ai_task_runs_deleted,
        ai_provider_attempt_receipts_deleted=(ai_provider_attempt_receipts_deleted),
        ai_usage_events_deleted=ai_usage_events_deleted,
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
    if deleted.ai_provider_attempt_receipts_deleted:
        record_audit(
            db,
            actor_user_id=None,
            actor_principal_type="system",
            action="history.ai_provider_attempt_receipts.prune",
            resource_type="ai_provider_attempt_receipt",
            metadata={
                "deleted_count": deleted.ai_provider_attempt_receipts_deleted,
                "retention_days": max(1, int(settings.ai_task_history_retention_days)),
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


def _delete_ai_history_with_envelopes(
    db: Session,
    model,
    timestamp_column,
    cutoff,
    batch_size: int,
    *,
    resource_type: str,
    extra_predicate=None,
) -> int:
    query = select(model.id).where(timestamp_column < cutoff)
    if extra_predicate is not None:
        query = query.where(extra_predicate)
    ids = list(
        db.scalars(
            query.order_by(timestamp_column.asc(), model.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not ids:
        return 0
    result = db.execute(
        delete(model)
        .where(model.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    db.flush()
    prune_deleted_resource_envelopes(
        db,
        resources=((resource_type, resource_id) for resource_id in ids),
    )
    return int(result.rowcount or 0)


def _delete_expired_ai_provider_receipt_ledgers(
    db: Session,
    *,
    cutoff: datetime,
    batch_size: int,
) -> int:
    candidate_operation_ids = _eligible_ai_provider_receipt_operation_ids(
        db,
        cutoff=cutoff,
        batch_size=batch_size,
    )
    if not candidate_operation_ids:
        return 0

    locked_receipts = list(
        db.scalars(
            select(AIProviderAttemptReceipt)
            .where(AIProviderAttemptReceipt.operation_id.in_(candidate_operation_ids))
            .order_by(
                AIProviderAttemptReceipt.operation_id.asc(),
                AIProviderAttemptReceipt.attempt_number.asc(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if not locked_receipts:
        return 0

    eligible_operation_ids = set(
        _eligible_ai_provider_receipt_operation_ids(
            db,
            cutoff=cutoff,
            batch_size=None,
            operation_ids=candidate_operation_ids,
        )
    )
    receipt_ids = [
        receipt.id
        for receipt in locked_receipts
        if receipt.operation_id in eligible_operation_ids
    ]
    if not receipt_ids:
        return 0
    result = db.execute(
        delete(AIProviderAttemptReceipt)
        .where(AIProviderAttemptReceipt.id.in_(receipt_ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _eligible_ai_provider_receipt_operation_ids(
    db: Session,
    *,
    cutoff: datetime,
    batch_size: int | None,
    operation_ids: list | None = None,
) -> list:
    receipt = aliased(AIProviderAttemptReceipt)
    unsafe_receipt = aliased(AIProviderAttemptReceipt)
    recent_receipt = aliased(AIProviderAttemptReceipt)
    task_receipt = aliased(AIProviderAttemptReceipt)
    query = (
        select(receipt.operation_id)
        .where(
            receipt.updated_at < cutoff,
            ~select(unsafe_receipt.id)
            .where(
                unsafe_receipt.operation_id == receipt.operation_id,
                _unresolved_ai_provider_receipt(unsafe_receipt),
            )
            .exists(),
            ~select(recent_receipt.id)
            .where(
                recent_receipt.operation_id == receipt.operation_id,
                recent_receipt.updated_at >= cutoff,
            )
            .exists(),
            ~select(task_receipt.id)
            .join(
                AITaskRun,
                AITaskRun.id == task_receipt.task_run_id_snapshot,
            )
            .where(task_receipt.operation_id == receipt.operation_id)
            .exists(),
        )
        .distinct()
        .order_by(receipt.operation_id.asc())
    )
    if operation_ids is not None:
        query = query.where(receipt.operation_id.in_(operation_ids))
    if batch_size is not None:
        query = query.limit(batch_size)
    return list(db.scalars(query).all())


def _unresolved_ai_provider_receipt(receipt):
    return and_(
        receipt.reconciliation_action.is_(None),
        receipt.state.in_(("reserved", "ambiguous")),
    )


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
