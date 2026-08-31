from __future__ import annotations

import uuid
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_data_access_context,
    require_permissions,
)
from app.api.governance_support import (
    authorize_governance_actor,
    commit_governance_mutation,
    governance_authorization_http_error,
    raise_governance_storage_error,
    record_governance_audit,
    record_rejected_governance_mutation,
)
from app.api.sensitive_action_auth import require_sensitive_browser_session
from app.api.routes.action_approval_policy_helpers import (
    authorize_action_approval_data_access,
    commit_policy_evidence_and_refence,
    record_action_approval_would_deny,
    refence_action_approval_context,
)
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import (
    SCOPE_APPROVE_APPROVALS,
    SCOPE_READ_APPROVALS,
    SCOPE_WRITE_APPROVALS,
)
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.action_approval import ActionApprovalRequest
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.user import User
from app.schemas.action_approval import (
    ActionApprovalCancelRequest,
    ActionApprovalCreateRequest,
    ActionApprovalDecisionRequest,
    ActionApprovalExecuteRequest,
    ActionApprovalExecutionResponse,
    ActionApprovalListResponse,
    ActionApprovalResponse,
    ActionDefinitionResponse,
    ActionExecutionReceiptResponse,
)
from app.services.action_approvals import (
    ActionApprovalConflict,
    ActionApprovalError,
    ActionApprovalForbidden,
    ActionApprovalInvalidated,
    ActionApprovalNotFound,
    action_approval_list_filters,
    cancel_action_approval,
    create_action_approval,
    decide_action_approval,
    execute_action_approval,
    get_action_approval_response,
    get_action_execution_receipt_response,
    get_action_execution_response,
    list_action_approvals,
    lock_action_approval_for_mutation,
)
from app.services.action_approval_data_policy import (
    ActionApprovalWouldDenySummary,
    action_approval_would_deny_summary,
)
from app.services.authorization import (
    AuthorizationContext,
    lock_iam_policy_for_mutation,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    fence_data_access_context,
)
from app.services.action_registry import (
    ACTION_DEFINITIONS,
    RegisteredActionError,
    get_registered_action,
)
from app.services.governance_authorization import GovernanceAuthorizationDenied
from app.services.governance_idempotency import (
    GovernanceIdempotencyError,
    GovernanceIdempotencyKeyInvalid,
    GovernanceOperationIdentity,
    build_governance_operation_identity,
    find_governance_operation_replay,
    governance_operation_replay_payload,
    lock_governance_operation_identity,
    record_governance_operation_receipt,
)


router = APIRouter(prefix="/iam/action-approvals", tags=["action approvals"])
_BROWSER_ONLY = {"x-threatlens-browser-session-only": True}
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


@router.get("/actions", response_model=list[ActionDefinitionResponse])
def get_action_catalog(
    _reader: User = Depends(require_permissions(SCOPE_READ_APPROVALS)),
) -> list[ActionDefinitionResponse]:
    return [
        ActionDefinitionResponse(
            key=definition.key,
            label=definition.label,
            description=definition.description,
            target_type=definition.target_type,
            requester_permission=definition.requester_permission,
            approver_permission=definition.approver_permission,
            risk=definition.risk,
            version=definition.version,
            payload_fields=list(definition.payload_fields),
        )
        for definition in ACTION_DEFINITIONS
    ]


@router.get("", response_model=ActionApprovalListResponse)
def get_action_approvals(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    action_type: str | None = Query(default=None, min_length=3, max_length=96),
    stored_status: Literal[
        "pending", "approved", "denied", "cancelled", "invalidated", "executed"
    ]
    | None = None,
    requester_user_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_APPROVALS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionApprovalListResponse:
    try:
        rendered = list_action_approvals(
            db,
            page=page,
            page_size=page_size,
            action_type=action_type,
            stored_status=stored_status,
            requester_user_id=requester_user_id,
            data_access=data_access,
        )
        summary = action_approval_would_deny_summary(
            db,
            data_access=data_access,
            filters=action_approval_list_filters(
                action_type=action_type,
                stored_status=stored_status,
                requester_user_id=requester_user_id,
            ),
        )
        _finalize_read_policy_evidence(
            request,
            db,
            data_access=data_access,
            summary=summary,
            surface="action_approval.list",
            history_scope="list",
        )
        return rendered
    except (ActionApprovalError, DataPolicyError) as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="list", exc=exc
        )


@router.get("/{approval_id}", response_model=ActionApprovalResponse)
def get_action_approval(
    approval_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_APPROVALS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionApprovalResponse:
    try:
        rendered = get_action_approval_response(
            db,
            approval_id,
            data_access=data_access,
        )
        _finalize_read_policy_evidence(
            request,
            db,
            data_access=data_access,
            summary=action_approval_would_deny_summary(
                db,
                data_access=data_access,
                filters=(ActionApprovalRequest.id == approval_id,),
            ),
            surface="action_approval.detail",
            history_scope="detail",
        )
        return rendered
    except (ActionApprovalError, DataPolicyError) as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="get", exc=exc
        )


@router.get(
    "/{approval_id}/receipt",
    response_model=ActionExecutionReceiptResponse,
)
def get_action_receipt(
    approval_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_APPROVALS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionExecutionReceiptResponse:
    try:
        rendered = get_action_execution_receipt_response(
            db,
            approval_id,
            data_access=data_access,
        )
        _finalize_read_policy_evidence(
            request,
            db,
            data_access=data_access,
            summary=action_approval_would_deny_summary(
                db,
                data_access=data_access,
                filters=(ActionApprovalRequest.id == approval_id,),
            ),
            surface="action_approval.receipt",
            history_scope="receipt",
        )
        return rendered
    except (ActionApprovalError, DataPolicyError) as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="get_receipt", exc=exc
        )


@router.post(
    "",
    response_model=ActionApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_BROWSER_ONLY,
)
def post_action_approval(
    payload: ActionApprovalCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_APPROVALS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionApprovalResponse:
    action = "approvals.action.request"
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="action_approval.create",
            payload=payload.model_dump(mode="json"),
        )
        locked_actor, authorization, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            required_permission=SCOPE_WRITE_APPROVALS,
            operation_label="requesting a sensitive action",
            audit_action=action,
            data_access=data_access,
        )
        definition = get_registered_action(payload.action_type)
        _require_durable_permission(
            authorization,
            definition.requester_permission,
            detail=(
                f"Requesting {definition.label.lower()} requires durably assigned "
                f"{definition.requester_permission} access."
            ),
        )
        if replay is not None:
            _require_policy_access_for_mutation(
                db,
                approval_id=replay.resource_id,
                data_access=data_access,
                surface="action_approval.create.replay",
            )
            _refence_replay_policy(request, db, data_access=data_access)
            return _replay_response(response, replay, ActionApprovalResponse)
        result = create_action_approval(
            db,
            requester=locked_actor,
            requester_authorization=authorization,
            data_access=data_access,
            payload=payload,
        )
        if result.target_data_access is not None:
            _record_mutation_would_deny(
                db,
                data_access=data_access,
                would_deny=result.target_data_access.decision.would_deny,
                label_ids=result.target_data_access.decision.label_ids,
                surface="action_approval.create",
            )
        rendered = get_action_approval_response(
            db,
            result.approval.id,
            data_access=data_access,
        )
        _record_success_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            approval_id=result.approval.id,
            rendered=rendered,
            http_status=status.HTTP_201_CREATED,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="action_approval",
            resource_id=str(result.approval.id),
            authorization_approval_id=result.approval.id,
            metadata=_approval_audit_metadata(rendered),
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(response, rendered.revision, changed=True)
        return rendered
    except (
        ActionApprovalError,
        RegisteredActionError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
        DataPolicyError,
    ) as exc:
        _reject(db, request, actor, action, None, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="create", exc=exc
        )


@router.post(
    "/{approval_id}/decision",
    response_model=ActionApprovalResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_action_approval_decision(
    approval_id: uuid.UUID,
    payload: ActionApprovalDecisionRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_APPROVE_APPROVALS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionApprovalResponse:
    action = "approvals.action.approve" if payload.approve else "approvals.action.deny"
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="action_approval.decision",
            payload={
                "approval_id": str(approval_id),
                **payload.model_dump(mode="json"),
            },
        )
        locked_actor, authorization, replay, session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            required_permission=SCOPE_APPROVE_APPROVALS,
            operation_label="deciding a sensitive action request",
            audit_action=action,
            approval_id=approval_id,
            data_access=data_access,
        )
        approval = lock_action_approval_for_mutation(
            db,
            approval_id,
            data_access=data_access,
        )
        _require_policy_access_for_mutation(
            db,
            approval_id=approval_id,
            data_access=data_access,
            surface="action_approval.decision",
        )
        _require_durable_permission(
            authorization,
            approval.approver_permission_snapshot,
            detail="Your durable authority for this action changed. Reload the request before retrying.",
        )
        if replay is not None:
            _refence_replay_policy(request, db, data_access=data_access)
            return _replay_response(response, replay, ActionApprovalResponse)
        result = decide_action_approval(
            db,
            approval_id=approval_id,
            approver=locked_actor,
            approver_authorization=authorization,
            data_access=data_access,
            approver_auth_method=session.auth_method,
            approver_mfa_method=session.mfa_method,
            payload=payload,
        )
        rendered = get_action_approval_response(
            db,
            approval_id,
            data_access=data_access,
        )
        _record_success_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            approval_id=approval_id,
            rendered=rendered,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="action_approval",
            resource_id=str(approval_id),
            authorization_approval_id=approval_id,
            metadata={
                **_approval_audit_metadata(rendered),
                "previous_status": result.previous_status,
            },
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(response, rendered.revision, changed=True)
        return rendered
    except (
        ActionApprovalError,
        RegisteredActionError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
        DataPolicyError,
    ) as exc:
        _reject(db, request, actor, action, approval_id, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="decide", exc=exc
        )


@router.post(
    "/{approval_id}/cancel",
    response_model=ActionApprovalResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_action_approval_cancel(
    approval_id: uuid.UUID,
    payload: ActionApprovalCancelRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionApprovalResponse:
    action = "approvals.action.cancel"
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="action_approval.cancel",
            payload={
                "approval_id": str(approval_id),
                **payload.model_dump(mode="json"),
            },
        )
        locked_actor, authorization, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            required_permission=None,
            operation_label="cancelling a sensitive action request",
            audit_action=action,
            approval_id=approval_id,
            data_access=data_access,
        )
        approval = lock_action_approval_for_mutation(
            db,
            approval_id,
            data_access=data_access,
        )
        _require_policy_access_for_mutation(
            db,
            approval_id=approval_id,
            data_access=data_access,
            surface="action_approval.cancel",
        )
        if approval.requested_by_user_id != locked_actor.id:
            _require_durable_permission(
                authorization,
                SCOPE_APPROVE_APPROVALS,
                detail="Only the requester or a durably authorized approver can cancel this request.",
            )
            _require_durable_permission(
                authorization,
                approval.approver_permission_snapshot,
                detail="Your durable authority for this action changed. Reload the request before retrying.",
            )
        if replay is not None:
            _refence_replay_policy(request, db, data_access=data_access)
            return _replay_response(response, replay, ActionApprovalResponse)
        result = cancel_action_approval(
            db,
            approval_id=approval_id,
            actor=locked_actor,
            actor_authorization=authorization,
            data_access=data_access,
            payload=payload,
        )
        rendered = get_action_approval_response(
            db,
            approval_id,
            data_access=data_access,
        )
        _record_success_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            approval_id=approval_id,
            rendered=rendered,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="action_approval",
            resource_id=str(approval_id),
            authorization_approval_id=approval_id,
            metadata={
                **_approval_audit_metadata(rendered),
                "previous_status": result.previous_status,
            },
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(response, rendered.revision, changed=True)
        return rendered
    except (
        ActionApprovalError,
        RegisteredActionError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
        DataPolicyError,
    ) as exc:
        _reject(db, request, actor, action, approval_id, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="cancel", exc=exc
        )


@router.post(
    "/{approval_id}/execute",
    response_model=ActionApprovalExecutionResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_action_approval_execute(
    approval_id: uuid.UUID,
    payload: ActionApprovalExecuteRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_APPROVALS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
) -> ActionApprovalExecutionResponse:
    action = "approvals.action.execute"
    identity: GovernanceOperationIdentity | None = None
    locked_actor = actor
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="action_approval.execute",
            payload={
                "approval_id": str(approval_id),
                **payload.model_dump(mode="json"),
            },
        )
        locked_actor, authorization, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            required_permission=SCOPE_WRITE_APPROVALS,
            operation_label="executing an approved sensitive action",
            audit_action=action,
            approval_id=approval_id,
            data_access=data_access,
        )
        approval = lock_action_approval_for_mutation(
            db,
            approval_id,
            data_access=data_access,
        )
        _require_policy_access_for_mutation(
            db,
            approval_id=approval_id,
            data_access=data_access,
            surface="action_approval.execute",
        )
        _require_durable_permission(
            authorization,
            approval.requester_permission_snapshot,
            detail="Your durable authority for this action changed. Reload the request before retrying.",
        )
        if replay is not None:
            _refence_replay_policy(request, db, data_access=data_access)
            return _replay_response(response, replay, ActionApprovalExecutionResponse)
        mutation, receipt = execute_action_approval(
            db,
            approval_id=approval_id,
            requester=locked_actor,
            requester_authorization=authorization,
            data_access=data_access,
            expected_revision=payload.expected_revision,
        )
        rendered = get_action_execution_response(
            db,
            approval_id=approval_id,
            receipt=receipt,
            data_access=data_access,
        )
        _record_success_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            approval_id=approval_id,
            rendered=rendered,
        )
        audit_metadata = {
            **_approval_audit_metadata(rendered.approval),
            "previous_status": mutation.previous_status,
            "execution_receipt_id": str(receipt.id),
        }
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="action_approval",
            resource_id=str(approval_id),
            authorization_approval_id=approval_id,
            execution_receipt_id=receipt.id,
            metadata=audit_metadata,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=mutation.approval.audit_action_snapshot,
            resource_type=mutation.approval.target_type,
            resource_id=mutation.approval.target_id,
            authorization_approval_id=approval_id,
            execution_receipt_id=receipt.id,
            metadata=audit_metadata,
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(response, rendered.approval.revision, changed=True)
        return rendered
    except ActionApprovalInvalidated as exc:
        if identity is None:
            raise _http_error(exc) from exc
        _commit_invalidation(
            db,
            request=request,
            actor=locked_actor,
            identity=identity,
            approval_id=approval_id,
            exc=exc,
        )
        raise _http_error(exc, changed=True) from exc
    except (
        ActionApprovalError,
        RegisteredActionError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
        DataPolicyError,
    ) as exc:
        _reject(db, request, actor, action, approval_id, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="action_approval", operation="execute", exc=exc
        )


def _prepare_mutation(
    db: Session,
    *,
    request: Request,
    actor: User,
    identity: GovernanceOperationIdentity,
    required_permission: str | None,
    operation_label: str,
    audit_action: str,
    data_access: DataAccessContext,
    approval_id: uuid.UUID | None = None,
) -> tuple[
    User,
    AuthorizationContext,
    GovernanceOperationReceipt | None,
    AuthSession,
]:
    lock_governance_operation_identity(db, actor_user_id=actor.id, identity=identity)
    lock_iam_policy_for_mutation(db)
    fence_data_access_context(db, data_access)
    if approval_id is not None:
        lock_action_approval_for_mutation(
            db,
            approval_id,
            data_access=data_access,
        )
    locked_actor, authorization = authorize_governance_actor(
        db,
        request=request,
        actor=actor,
        required_permission=required_permission,
        durable=required_permission is not None,
    )
    try:
        session = require_sensitive_browser_session(
            db,
            request=request,
            user=locked_actor,
            action=identity.operation,
            operation_label=operation_label,
        )
    except ApiHTTPException as exc:
        record_rejected_governance_mutation(
            db,
            request=request,
            actor=actor,
            action=audit_action,
            resource_type="action_approval",
            resource_id=str(approval_id) if approval_id is not None else None,
            reason=exc.error_code,
        )
        raise
    replay = find_governance_operation_replay(
        db, actor_user_id=locked_actor.id, identity=identity
    )
    fence_data_access_context(db, data_access)
    return locked_actor, authorization, replay, session


def _operation_identity(
    idempotency_key: str,
    *,
    operation: str,
    payload: dict[str, object],
) -> GovernanceOperationIdentity:
    return build_governance_operation_identity(
        idempotency_key,
        operation=operation,
        payload=payload,
    )


def _record_success_receipt(
    db: Session,
    *,
    actor: User,
    identity: GovernanceOperationIdentity,
    approval_id: uuid.UUID,
    rendered: BaseModel,
    http_status: int = status.HTTP_200_OK,
) -> None:
    record_governance_operation_receipt(
        db,
        actor_user_id=actor.id,
        identity=identity,
        resource_type="action_approval",
        resource_id=approval_id,
        response_json=rendered.model_dump(mode="json"),
        http_status=http_status,
    )


def _commit_invalidation(
    db: Session,
    *,
    request: Request,
    actor: User,
    identity: GovernanceOperationIdentity,
    approval_id: uuid.UUID,
    exc: ActionApprovalInvalidated,
) -> None:
    context = {
        "current_revision": exc.current_revision,
        "invalidation_reason": exc.invalidation_reason,
    }
    record_governance_operation_receipt(
        db,
        actor_user_id=actor.id,
        identity=identity,
        resource_type="action_approval",
        resource_id=approval_id,
        response_json={
            "detail": str(exc),
            "error_code": exc.code,
            "error_context": context,
        },
        http_status=status.HTTP_409_CONFLICT,
    )
    record_governance_audit(
        db,
        request=request,
        actor=actor,
        action="approvals.action.invalidate",
        resource_type="action_approval",
        resource_id=str(approval_id),
        authorization_approval_id=approval_id,
        metadata={
            "invalidation_reason": exc.invalidation_reason,
            "revision": exc.current_revision,
        },
    )
    record_governance_audit(
        db,
        request=request,
        actor=actor,
        action=exc.approval.audit_action_snapshot,
        resource_type=exc.approval.target_type,
        resource_id=exc.approval.target_id,
        success=False,
        authorization_approval_id=approval_id,
        metadata={
            "reason": exc.invalidation_reason,
            "target_revision": exc.approval.target_revision,
        },
    )
    commit_governance_mutation(db, action="approvals.action.invalidate")


def _replay_response(
    response: Response,
    receipt: GovernanceOperationReceipt,
    response_model: type[_ResponseModel],
) -> _ResponseModel:
    payload = governance_operation_replay_payload(receipt)
    if receipt.http_status >= 400:
        context = payload.get("error_context")
        revision = (
            context.get("current_revision") if isinstance(context, dict) else None
        )
        headers = {"X-ThreatLens-Mutation-Changed": "false"}
        if revision is not None:
            headers["X-Current-Revision"] = str(revision)
        raise ApiHTTPException(
            status_code=receipt.http_status,
            detail=payload.get("detail", "The stored governance operation failed."),
            error_code=str(payload.get("error_code", "governance_operation_failed")),
            error_context=context if isinstance(context, dict) else None,
            headers=headers,
        )
    rendered = response_model.model_validate(payload)
    revision = getattr(rendered, "revision", None)
    if revision is None and hasattr(rendered, "approval"):
        revision = rendered.approval.revision
    _set_mutation_headers(response, revision, changed=False)
    return rendered


def _approval_audit_metadata(
    approval: ActionApprovalResponse,
) -> dict[str, object]:
    return {
        "action_type": approval.action_type,
        "action_definition_version": approval.action_definition_version,
        "target_type": approval.target_type,
        "target_id": approval.target_id,
        "target_revision": approval.target_revision,
        "status": approval.stored_status,
        "revision": approval.revision,
    }


def _finalize_read_policy_evidence(
    request: Request,
    db: Session,
    *,
    data_access: DataAccessContext,
    summary: ActionApprovalWouldDenySummary,
    surface: str,
    history_scope: str,
) -> None:
    recorded = record_action_approval_would_deny(
        db,
        data_access=data_access,
        summary=summary,
        surface=surface,
        history_scope=history_scope,
    )
    if recorded:
        commit_policy_evidence_and_refence(
            request,
            db,
            data_access=data_access,
        )
    else:
        refence_action_approval_context(
            request,
            db,
            data_access=data_access,
        )


def _require_policy_access_for_mutation(
    db: Session,
    *,
    approval_id: uuid.UUID,
    data_access: DataAccessContext,
    surface: str,
) -> None:
    decision = authorize_action_approval_data_access(
        db,
        approval_id=approval_id,
        data_access=data_access,
        surface=surface,
    )
    if not decision.allowed:
        raise ActionApprovalNotFound("Action approval request not found.")


def _record_mutation_would_deny(
    db: Session,
    *,
    data_access: DataAccessContext,
    would_deny: bool,
    label_ids: frozenset[uuid.UUID],
    surface: str,
) -> None:
    if not would_deny:
        return
    record_action_approval_would_deny(
        db,
        data_access=data_access,
        summary=ActionApprovalWouldDenySummary(
            affected_count=1,
            handling_label_ids=label_ids,
        ),
        surface=surface,
        history_scope="target_authorization",
    )


def _refence_replay_policy(
    request: Request,
    db: Session,
    *,
    data_access: DataAccessContext,
) -> None:
    if data_access.auditing:
        commit_policy_evidence_and_refence(
            request,
            db,
            data_access=data_access,
        )
        return
    refence_action_approval_context(
        request,
        db,
        data_access=data_access,
    )


def _require_durable_permission(
    authorization: AuthorizationContext,
    permission: str,
    *,
    detail: str,
) -> None:
    if not authorization.has_durable(permission):
        raise ActionApprovalForbidden(detail)


def _reject(
    db: Session,
    request: Request,
    actor: User,
    action: str,
    approval_id: uuid.UUID | None,
    exc: Exception,
) -> None:
    visible_approval_id = (
        None if isinstance(exc, ActionApprovalNotFound) else approval_id
    )
    record_rejected_governance_mutation(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type="action_approval",
        resource_id=(
            str(visible_approval_id)
            if visible_approval_id is not None
            else None
        ),
        reason=str(getattr(exc, "code", "action_approval_error")),
        context=getattr(exc, "context", None),
    )


def _http_error(exc: Exception, *, changed: bool = False) -> ApiHTTPException:
    if isinstance(exc, GovernanceAuthorizationDenied):
        return governance_authorization_http_error(exc)
    if isinstance(exc, ActionApprovalNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, ActionApprovalForbidden):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, GovernanceIdempotencyKeyInvalid):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, (RegisteredActionError, DataPolicyError)):
        status_code = exc.status_code
    elif isinstance(exc, (ActionApprovalConflict, GovernanceIdempotencyError)):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    context = dict(getattr(exc, "context", {}) or {})
    current_revision = getattr(exc, "current_revision", None)
    if current_revision is not None:
        context["current_revision"] = current_revision
    invalidation_reason = getattr(exc, "invalidation_reason", None)
    if invalidation_reason is not None:
        context["invalidation_reason"] = invalidation_reason
    headers = {"X-ThreatLens-Mutation-Changed": str(changed).lower()}
    if current_revision is not None:
        headers["X-Current-Revision"] = str(current_revision)
    return ApiHTTPException(
        status_code=status_code,
        detail=str(exc),
        error_code=str(getattr(exc, "code", "action_approval_error")),
        error_context=context or None,
        headers=headers,
    )


def _set_mutation_headers(
    response: Response, revision: int | None, *, changed: bool
) -> None:
    if revision is not None:
        response.headers["X-Current-Revision"] = str(revision)
    response.headers["X-ThreatLens-Mutation-Changed"] = str(changed).lower()


__all__ = ["router"]
