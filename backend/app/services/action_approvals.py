from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.token_scopes import (
    SCOPE_APPROVE_APPROVALS,
    SCOPE_WRITE_APPROVALS,
)
from app.models.action_approval import (
    ActionApprovalRequest,
    ActionExecutionReceipt,
)
from app.models.user import User
from app.schemas.action_approval import (
    ActionApprovalCancelRequest,
    ActionApprovalCreateRequest,
    ActionApprovalDecisionRequest,
    ActionApprovalExecutionResponse,
    ActionApprovalListResponse,
    ActionApprovalResponse,
    ActionExecutionReceiptResponse,
)
from app.services.action_registry import (
    RegisteredActionDefinition,
    RegisteredActionTargetConflict,
    RegisteredActionTargetNotFound,
    execute_registered_action,
    get_registered_action,
    inspect_registered_action_target,
    normalize_registered_action_payload,
)
from app.services.authorization import (
    AuthorizationContext,
    authorization_context_for_user,
    database_clock,
    lock_iam_policy_for_mutation,
)


MAX_LIVE_APPROVALS_PER_REQUESTER = 25


class ActionApprovalError(RuntimeError):
    code = "action_approval_error"


class ActionApprovalNotFound(ActionApprovalError):
    code = "action_approval_not_found"


class ActionApprovalForbidden(ActionApprovalError):
    code = "action_approval_forbidden"


class ActionApprovalConflict(ActionApprovalError):
    code = "action_approval_conflict"


class ActionApprovalDuplicate(ActionApprovalConflict):
    code = "action_approval_duplicate"


class ActionApprovalExpired(ActionApprovalConflict):
    code = "action_approval_expired"


class ActionApprovalLimitReached(ActionApprovalConflict):
    code = "action_approval_limit_reached"


class ActionApprovalDefinitionChanged(ActionApprovalConflict):
    code = "action_approval_definition_changed"


class ActionApprovalRevisionConflict(ActionApprovalConflict):
    code = "action_approval_revision_conflict"

    def __init__(self, approval: ActionApprovalRequest) -> None:
        self.current_revision = approval.revision
        super().__init__(
            "This action approval changed after it was loaded. Reload it and retry."
        )


class ActionApprovalInvalidated(ActionApprovalConflict):
    code = "action_approval_invalidated"

    def __init__(self, approval: ActionApprovalRequest, detail: str) -> None:
        self.approval = approval
        self.current_revision = approval.revision
        self.invalidation_reason = approval.invalidation_reason
        super().__init__(detail)


@dataclass(frozen=True)
class ActionApprovalMutationResult:
    approval: ActionApprovalRequest
    previous_status: str | None = None


def create_action_approval(
    db: Session,
    *,
    requester: User,
    requester_authorization: AuthorizationContext,
    payload: ActionApprovalCreateRequest,
) -> ActionApprovalMutationResult:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    definition = get_registered_action(payload.action_type)
    _require_permission(
        requester_authorization,
        SCOPE_WRITE_APPROVALS,
        durable=True,
        detail="Requesting a sensitive action requires durably assigned write:approvals access.",
    )
    _require_permission(
        requester_authorization,
        definition.requester_permission,
        durable=True,
        detail=(
            f"Requesting {definition.label.lower()} requires durably assigned "
            f"{definition.requester_permission} access."
        ),
    )
    normalized_payload = normalize_registered_action_payload(
        definition, payload.payload
    )
    target = inspect_registered_action_target(
        db,
        definition=definition,
        target_id=payload.target_id,
        target_revision=payload.target_revision,
        lock=True,
    )
    payload_digest = canonical_action_payload_digest(
        definition=definition,
        target_id=target.target_id,
        target_revision=payload.target_revision,
        target_snapshot=target.snapshot,
        payload=normalized_payload,
    )
    live_filter = (
        ActionApprovalRequest.requested_by_user_id == requester.id,
        ActionApprovalRequest.status.in_(["pending", "approved"]),
        ActionApprovalRequest.expires_at > now,
    )
    live_count = int(
        db.scalar(select(func.count(ActionApprovalRequest.id)).where(*live_filter)) or 0
    )
    if live_count >= MAX_LIVE_APPROVALS_PER_REQUESTER:
        raise ActionApprovalLimitReached(
            "You already have the maximum number of pending or approved actions. Resolve, execute, cancel, or let one expire before creating another."
        )
    duplicate = db.scalar(
        select(ActionApprovalRequest.id).where(
            *live_filter,
            ActionApprovalRequest.action_type == definition.key,
            ActionApprovalRequest.target_id == target.target_id,
            ActionApprovalRequest.payload_digest == payload_digest,
        )
    )
    if duplicate is not None:
        raise ActionApprovalDuplicate(
            "An equivalent pending or approved action already exists for this target."
        )
    approval = ActionApprovalRequest(
        action_type=definition.key,
        action_label_snapshot=definition.label,
        audit_action_snapshot=definition.audit_action,
        requester_permission_snapshot=definition.requester_permission,
        approver_permission_snapshot=definition.approver_permission,
        action_definition_version=definition.version,
        target_type=definition.target_type,
        target_id=target.target_id,
        target_revision=payload.target_revision,
        target_snapshot=target.snapshot,
        payload_json=normalized_payload,
        payload_digest=payload_digest,
        requested_by_user_id=requester.id,
        requested_by_email_snapshot=requester.email,
        request_reason=payload.reason,
        expires_at=now + timedelta(seconds=payload.expires_in_seconds),
        status="pending",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(approval)
    db.flush()
    return ActionApprovalMutationResult(approval=approval)


def decide_action_approval(
    db: Session,
    *,
    approval_id: uuid.UUID,
    approver: User,
    approver_authorization: AuthorizationContext,
    approver_auth_method: str,
    approver_mfa_method: str | None,
    payload: ActionApprovalDecisionRequest,
) -> ActionApprovalMutationResult:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    approval = lock_action_approval_for_mutation(db, approval_id)
    _require_revision(approval, payload.expected_revision)
    _require_live_status(approval, now, expected="pending")
    if approval.requested_by_user_id == approver.id:
        raise ActionApprovalForbidden(
            "A sensitive action must be decided by someone other than its requester."
        )
    definition = _require_exact_definition(approval)
    _require_permission(
        approver_authorization,
        SCOPE_APPROVE_APPROVALS,
        durable=True,
        detail="Action approval authority must be durably assigned.",
    )
    _require_permission(
        approver_authorization,
        definition.approver_permission,
        durable=True,
        detail=(
            f"Deciding {definition.label.lower()} requires durably assigned "
            f"{definition.approver_permission} access."
        ),
    )
    _verify_stored_integrity(approval, definition)
    if payload.approve:
        _verify_target_is_still_approvable(db, approval, definition)

    previous_status = approval.status
    approval.status = "approved" if payload.approve else "denied"
    approval.decided_by_user_id = approver.id
    approval.decided_by_email_snapshot = approver.email
    approval.decided_at = now
    approval.decision_reason = payload.reason
    approval.decided_auth_token_version_snapshot = int(approver.auth_token_version or 0)
    approval.decided_auth_method_snapshot = approver_auth_method
    approval.decided_mfa_method_snapshot = approver_mfa_method
    approval.revision += 1
    approval.updated_at = now
    db.add(approval)
    db.flush()
    return ActionApprovalMutationResult(approval, previous_status)


def cancel_action_approval(
    db: Session,
    *,
    approval_id: uuid.UUID,
    actor: User,
    actor_authorization: AuthorizationContext,
    payload: ActionApprovalCancelRequest,
) -> ActionApprovalMutationResult:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    approval = lock_action_approval_for_mutation(db, approval_id)
    _require_revision(approval, payload.expected_revision)
    if approval.status not in {"pending", "approved"}:
        raise ActionApprovalConflict(
            f"This action approval is already {effective_action_approval_status(approval, now)}."
        )
    if approval.expires_at <= now:
        raise ActionApprovalExpired(
            "This action approval has expired and no longer needs cancellation."
        )
    is_requester = approval.requested_by_user_id == actor.id
    can_manage = actor_authorization.has_durable(SCOPE_APPROVE_APPROVALS)
    if can_manage:
        can_manage = actor_authorization.has_durable(
            approval.approver_permission_snapshot
        )
    if not is_requester and not can_manage:
        raise ActionApprovalForbidden(
            "Only the requester or an approver with durable authority for this action can cancel this request."
        )
    previous_status = approval.status
    approval.status = "cancelled"
    approval.cancelled_from_status = previous_status
    approval.cancelled_by_user_id = actor.id
    approval.cancelled_by_principal_type = "user"
    approval.cancelled_by_email_snapshot = actor.email
    approval.cancelled_at = now
    approval.cancel_reason = payload.reason
    approval.revision += 1
    approval.updated_at = now
    db.add(approval)
    db.flush()
    return ActionApprovalMutationResult(approval, previous_status)


def execute_action_approval(
    db: Session,
    *,
    approval_id: uuid.UUID,
    requester: User,
    requester_authorization: AuthorizationContext,
    expected_revision: int,
) -> tuple[ActionApprovalMutationResult, ActionExecutionReceipt]:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    approval = lock_action_approval_for_mutation(db, approval_id)
    _require_revision(approval, expected_revision)
    _require_live_status(approval, now, expected="approved")
    if approval.requested_by_user_id != requester.id:
        raise ActionApprovalForbidden(
            "Only the original requester can execute this approved action."
        )
    try:
        definition = _require_exact_definition(approval)
    except ActionApprovalDefinitionChanged as exc:
        _invalidate_approval(
            db,
            approval,
            now=now,
            reason="action_definition_changed",
        )
        raise ActionApprovalInvalidated(
            approval,
            f"{exc} The approval was invalidated and no action was executed.",
        ) from exc
    _require_permission(
        requester_authorization,
        SCOPE_WRITE_APPROVALS,
        durable=True,
        detail="Executing an approved action requires durably assigned write:approvals access.",
    )
    _require_permission(
        requester_authorization,
        definition.requester_permission,
        durable=True,
        detail=(
            f"Executing {definition.label.lower()} requires durably assigned "
            f"{definition.requester_permission} access."
        ),
    )
    approver = _lock_current_approver(db, approval)
    decided_security_version = approval.decided_auth_token_version_snapshot
    if decided_security_version is None or int(approver.auth_token_version or 0) != int(
        decided_security_version
    ):
        raise ActionApprovalConflict(
            "The approver's security state changed after approval. Cancel this request and create a new one."
        )
    approver_authorization = authorization_context_for_user(db, approver)
    _require_permission(
        approver_authorization,
        SCOPE_APPROVE_APPROVALS,
        durable=True,
        detail="The original approver no longer has durable action-approval authority.",
    )
    _require_permission(
        approver_authorization,
        definition.approver_permission,
        durable=True,
        detail=(
            f"The original approver no longer has durable "
            f"{definition.approver_permission} access."
        ),
    )
    try:
        _verify_stored_integrity(approval, definition)
        result = execute_registered_action(
            db,
            definition=definition,
            target_id=approval.target_id,
            target_revision=approval.target_revision,
            expected_target_snapshot=dict(approval.target_snapshot),
            payload=dict(approval.payload_json),
            actor_user_id=requester.id,
        )
    except (
        ActionApprovalDefinitionChanged,
        RegisteredActionTargetConflict,
        RegisteredActionTargetNotFound,
    ) as exc:
        reason = (
            "action_definition_changed"
            if isinstance(exc, ActionApprovalDefinitionChanged)
            else "target_missing"
            if isinstance(exc, RegisteredActionTargetNotFound)
            else "target_preconditions_changed"
        )
        _invalidate_approval(db, approval, now=now, reason=reason)
        raise ActionApprovalInvalidated(
            approval,
            f"{exc} The approval was invalidated and no action was executed.",
        ) from exc

    previous_status = approval.status
    approval.status = "executed"
    approval.executed_by_user_id = requester.id
    approval.executed_by_email_snapshot = requester.email
    approval.executed_at = now
    approval.revision += 1
    approval.updated_at = now
    db.add(approval)
    db.flush()
    receipt = ActionExecutionReceipt(
        approval_request_id=approval.id,
        action_type=approval.action_type,
        target_type=approval.target_type,
        target_id=approval.target_id,
        target_revision=approval.target_revision,
        payload_digest=approval.payload_digest,
        requester_user_id=approval.requested_by_user_id,
        requester_email_snapshot=approval.requested_by_email_snapshot,
        approver_user_id=approval.decided_by_user_id,
        approver_email_snapshot=approval.decided_by_email_snapshot or approver.email,
        executed_by_user_id=requester.id,
        executed_by_email_snapshot=requester.email,
        result_json=result,
        result_schema_version=1,
        created_at=now,
    )
    db.add(receipt)
    db.flush()
    return ActionApprovalMutationResult(approval, previous_status), receipt


def lock_action_approval_for_mutation(
    db: Session, approval_id: uuid.UUID
) -> ActionApprovalRequest:
    approval = db.scalar(
        select(ActionApprovalRequest)
        .where(ActionApprovalRequest.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if approval is None:
        raise ActionApprovalNotFound("Action approval request not found.")
    return approval


def get_action_approval_response(
    db: Session, approval_id: uuid.UUID
) -> ActionApprovalResponse:
    row = db.execute(
        _response_row_query().where(ActionApprovalRequest.id == approval_id)
    ).one_or_none()
    if row is None:
        raise ActionApprovalNotFound("Action approval request not found.")
    return _response_from_row(row, _database_now(db))


def list_action_approvals(
    db: Session,
    *,
    page: int,
    page_size: int,
    action_type: str | None = None,
    stored_status: str | None = None,
    requester_user_id: uuid.UUID | None = None,
) -> ActionApprovalListResponse:
    filters = []
    if action_type is not None:
        filters.append(ActionApprovalRequest.action_type == action_type)
    if stored_status is not None:
        filters.append(ActionApprovalRequest.status == stored_status)
    if requester_user_id is not None:
        filters.append(ActionApprovalRequest.requested_by_user_id == requester_user_id)
    total = int(
        db.scalar(select(func.count(ActionApprovalRequest.id)).where(*filters)) or 0
    )
    rows = db.execute(
        _response_row_query()
        .where(*filters)
        .order_by(
            ActionApprovalRequest.created_at.desc(), ActionApprovalRequest.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    now = _database_now(db)
    return ActionApprovalListResponse(
        approvals=[_response_from_row(row, now) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_action_execution_response(
    db: Session,
    *,
    approval_id: uuid.UUID,
    receipt: ActionExecutionReceipt,
) -> ActionApprovalExecutionResponse:
    return ActionApprovalExecutionResponse(
        approval=get_action_approval_response(db, approval_id),
        receipt=_receipt_response(receipt),
    )


def get_action_execution_receipt_response(
    db: Session, approval_id: uuid.UUID
) -> ActionExecutionReceiptResponse:
    if db.get(ActionApprovalRequest, approval_id) is None:
        raise ActionApprovalNotFound("Action approval request not found.")
    receipt = db.scalar(
        select(ActionExecutionReceipt).where(
            ActionExecutionReceipt.approval_request_id == approval_id
        )
    )
    if receipt is None:
        raise ActionApprovalNotFound(
            "This action approval has no execution receipt because it has not executed."
        )
    return _receipt_response(receipt)


def effective_action_approval_status(
    approval: ActionApprovalRequest, now: datetime
) -> str:
    if approval.status in {"pending", "approved"} and approval.expires_at <= now:
        return "expired"
    return approval.status


def canonical_action_payload_digest(
    *,
    definition: RegisteredActionDefinition,
    target_id: str,
    target_revision: int,
    target_snapshot: dict[str, object],
    payload: dict[str, object],
) -> str:
    canonical = json.dumps(
        {
            "action_definition_version": definition.version,
            "action_type": definition.key,
            "audit_action": definition.audit_action,
            "approver_permission": definition.approver_permission,
            "payload": payload,
            "requester_permission": definition.requester_permission,
            "target_id": target_id,
            "target_revision": target_revision,
            "target_snapshot": target_snapshot,
            "target_type": definition.target_type,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_exact_definition(
    approval: ActionApprovalRequest,
) -> RegisteredActionDefinition:
    definition = get_registered_action(
        approval.action_type,
        version=approval.action_definition_version,
    )
    if (
        definition.target_type != approval.target_type
        or definition.audit_action != approval.audit_action_snapshot
        or definition.requester_permission != approval.requester_permission_snapshot
        or definition.approver_permission != approval.approver_permission_snapshot
    ):
        raise ActionApprovalDefinitionChanged(
            "The registered action contract changed without a version change."
        )
    return definition


def _verify_stored_integrity(
    approval: ActionApprovalRequest,
    definition: RegisteredActionDefinition,
) -> None:
    expected_digest = canonical_action_payload_digest(
        definition=definition,
        target_id=approval.target_id,
        target_revision=approval.target_revision,
        target_snapshot=dict(approval.target_snapshot),
        payload=dict(approval.payload_json),
    )
    if expected_digest != approval.payload_digest:
        raise ActionApprovalDefinitionChanged(
            "The stored action request failed its integrity check."
        )


def _verify_target_is_still_approvable(
    db: Session,
    approval: ActionApprovalRequest,
    definition: RegisteredActionDefinition,
) -> None:
    target = inspect_registered_action_target(
        db,
        definition=definition,
        target_id=approval.target_id,
        target_revision=approval.target_revision,
        lock=True,
    )
    if target.snapshot.get("precondition_digest") != approval.target_snapshot.get(
        "precondition_digest"
    ):
        raise RegisteredActionTargetConflict(
            "The action target's preconditions changed before approval. Reload it and create a new request."
        )


def _require_permission(
    authorization: AuthorizationContext,
    permission: str,
    *,
    durable: bool,
    detail: str,
) -> None:
    allowed = (
        authorization.has_durable(permission)
        if durable
        else authorization.has(permission)
    )
    if not allowed:
        raise ActionApprovalForbidden(detail)


def _require_live_status(
    approval: ActionApprovalRequest, now: datetime, *, expected: str
) -> None:
    if approval.status != expected:
        raise ActionApprovalConflict(
            f"This action approval is {effective_action_approval_status(approval, now)} and cannot perform this operation."
        )
    if approval.expires_at <= now:
        raise ActionApprovalExpired(
            "This action approval expired. Create and approve a new request."
        )


def _lock_current_approver(db: Session, approval: ActionApprovalRequest) -> User:
    if approval.decided_by_user_id is None:
        raise ActionApprovalConflict(
            "The original approver account no longer exists. Create and approve a new request."
        )
    approver = db.scalar(
        select(User)
        .where(User.id == approval.decided_by_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if approver is None or not approver.is_active or not approver.is_approved:
        raise ActionApprovalConflict(
            "The original approver is no longer active and approved. Create and approve a new request."
        )
    return approver


def _invalidate_approval(
    db: Session,
    approval: ActionApprovalRequest,
    *,
    now: datetime,
    reason: str,
) -> None:
    approval.status = "invalidated"
    approval.invalidated_at = now
    approval.invalidation_reason = reason
    approval.revision += 1
    approval.updated_at = now
    db.add(approval)
    db.flush()


def _require_revision(approval: ActionApprovalRequest, expected_revision: int) -> None:
    if approval.revision != expected_revision:
        raise ActionApprovalRevisionConflict(approval)


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(database_clock(db)))
    if not isinstance(value, datetime):
        raise ActionApprovalError(
            "The database clock could not be read. No action approval state was changed."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _response_row_query():
    requester = aliased(User)
    decider = aliased(User)
    canceller = aliased(User)
    executor = aliased(User)
    return (
        select(
            ActionApprovalRequest,
            requester.email.label("requester_email"),
            decider.email.label("decider_email"),
            canceller.email.label("canceller_email"),
            executor.email.label("executor_email"),
        )
        .outerjoin(
            requester, requester.id == ActionApprovalRequest.requested_by_user_id
        )
        .outerjoin(decider, decider.id == ActionApprovalRequest.decided_by_user_id)
        .outerjoin(
            canceller, canceller.id == ActionApprovalRequest.cancelled_by_user_id
        )
        .outerjoin(executor, executor.id == ActionApprovalRequest.executed_by_user_id)
    )


def _response_from_row(row, now: datetime) -> ActionApprovalResponse:
    approval = row[0]
    return ActionApprovalResponse(
        id=approval.id,
        action_type=approval.action_type,
        action_label=approval.action_label_snapshot,
        audit_action=approval.audit_action_snapshot,
        target_type=approval.target_type,
        target_id=approval.target_id,
        target_revision=approval.target_revision,
        target_snapshot=dict(approval.target_snapshot),
        payload=dict(approval.payload_json),
        payload_digest=approval.payload_digest,
        requester_permission=approval.requester_permission_snapshot,
        approver_permission=approval.approver_permission_snapshot,
        action_definition_version=approval.action_definition_version,
        requested_by_user_id=approval.requested_by_user_id,
        requested_by_email=approval.requested_by_email_snapshot,
        requested_by_current_email=row.requester_email,
        request_reason=approval.request_reason,
        expires_at=approval.expires_at,
        stored_status=approval.status,
        status=effective_action_approval_status(approval, now),
        revision=approval.revision,
        decided_by_user_id=approval.decided_by_user_id,
        decided_by_email=approval.decided_by_email_snapshot,
        decided_by_current_email=row.decider_email,
        decided_at=approval.decided_at,
        decision_reason=approval.decision_reason,
        decided_auth_token_version=approval.decided_auth_token_version_snapshot,
        decided_auth_method=approval.decided_auth_method_snapshot,
        decided_mfa_method=approval.decided_mfa_method_snapshot,
        cancelled_by_user_id=approval.cancelled_by_user_id,
        cancelled_by_principal_type=approval.cancelled_by_principal_type,
        cancelled_from_status=approval.cancelled_from_status,
        cancelled_by_email=approval.cancelled_by_email_snapshot,
        cancelled_by_current_email=row.canceller_email,
        cancelled_at=approval.cancelled_at,
        cancel_reason=approval.cancel_reason,
        executed_by_user_id=approval.executed_by_user_id,
        executed_by_email=approval.executed_by_email_snapshot,
        executed_by_current_email=row.executor_email,
        executed_at=approval.executed_at,
        invalidated_at=approval.invalidated_at,
        invalidation_reason=approval.invalidation_reason,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )


def _receipt_response(
    receipt: ActionExecutionReceipt,
) -> ActionExecutionReceiptResponse:
    return ActionExecutionReceiptResponse(
        id=receipt.id,
        approval_request_id=receipt.approval_request_id,
        action_type=receipt.action_type,
        target_type=receipt.target_type,
        target_id=receipt.target_id,
        target_revision=receipt.target_revision,
        payload_digest=receipt.payload_digest,
        requester_email=receipt.requester_email_snapshot,
        approver_email=receipt.approver_email_snapshot,
        executed_by_email=receipt.executed_by_email_snapshot,
        result=dict(receipt.result_json),
        result_schema_version=receipt.result_schema_version,
        created_at=receipt.created_at,
    )


__all__ = [
    "ActionApprovalConflict",
    "ActionApprovalDefinitionChanged",
    "ActionApprovalDuplicate",
    "ActionApprovalError",
    "ActionApprovalExpired",
    "ActionApprovalForbidden",
    "ActionApprovalInvalidated",
    "ActionApprovalLimitReached",
    "ActionApprovalMutationResult",
    "ActionApprovalNotFound",
    "ActionApprovalRevisionConflict",
    "canonical_action_payload_digest",
    "cancel_action_approval",
    "create_action_approval",
    "decide_action_approval",
    "effective_action_approval_status",
    "execute_action_approval",
    "get_action_approval_response",
    "get_action_execution_response",
    "get_action_execution_receipt_response",
    "list_action_approvals",
    "lock_action_approval_for_mutation",
]
