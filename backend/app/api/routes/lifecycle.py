from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import AuthenticatedPrincipal, require_permissions
from app.api.governance_support import (
    authorize_governance_actor,
    commit_governance_mutation,
    governance_authorization_http_error,
    raise_governance_storage_error,
    record_governance_audit,
    record_rejected_governance_mutation,
)
from app.api.sensitive_action_auth import require_sensitive_browser_session
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_OPERATIONS, SCOPE_WRITE_OPERATIONS
from app.db.session import get_db
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.user import User
from app.schemas.lifecycle import (
    LifecycleOverviewResponse,
    LifecyclePolicyResponse,
    LifecyclePolicyUpdateRequest,
    LifecyclePreviewRequest,
    LifecyclePreviewResponse,
    LifecycleRunCancelRequest,
    LifecycleRunCreateRequest,
    LifecycleRunListResponse,
    LifecycleRunResponse,
    LifecycleRunStatus,
    LifecycleRunTrigger,
    LifecycleTargetKey,
)
from app.services.lifecycle import (
    LifecycleConflict,
    LifecycleError,
    LifecycleNotFound,
    LifecycleValidationError,
    cancel_lifecycle_run,
    create_lifecycle_preview,
    create_manual_lifecycle_run,
    get_lifecycle_run,
    lifecycle_policy_audit_snapshot,
    lifecycle_policy_response_audit_snapshot,
    lifecycle_preview_evidence,
    lifecycle_overview,
    list_lifecycle_runs,
    update_lifecycle_policy,
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


logger = logging.getLogger("threatlens.lifecycle")
router = APIRouter(prefix="/operations/lifecycle", tags=["data lifecycle"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key")]
_read_permission = require_permissions(SCOPE_READ_OPERATIONS)
_write_permission = require_permissions(SCOPE_WRITE_OPERATIONS)
_BROWSER_ONLY = {"x-threatlens-browser-session-only": True}
_POLICY_RESOURCE_NAMESPACE = uuid.UUID("919d76f5-c915-4984-9a95-3e439b759c72")


def require_lifecycle_writer(
    principal: AuthenticatedPrincipal = Depends(_write_permission),
) -> User:
    if not isinstance(principal, User):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lifecycle changes require a human browser session.",
            error_code="human_principal_required",
        )
    return principal


require_lifecycle_writer._threatlens_required_scopes = (SCOPE_WRITE_OPERATIONS,)


@router.get("", response_model=LifecycleOverviewResponse)
def get_lifecycle_overview(
    response: Response,
    db: Session = Depends(get_db),
    reader: AuthenticatedPrincipal = Depends(_read_permission),
) -> LifecycleOverviewResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return lifecycle_overview(
            db,
            requested_by_user_id=reader.id if isinstance(reader, User) else None,
        )
    except (LifecycleError, SQLAlchemyError) as exc:
        raise _http_error(db, exc) from exc


@router.post("/preview", response_model=LifecyclePreviewResponse)
def post_lifecycle_preview(
    payload: LifecyclePreviewRequest,
    response: Response,
    db: Session = Depends(get_db),
    reader: AuthenticatedPrincipal = Depends(_read_permission),
) -> LifecyclePreviewResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        rendered = create_lifecycle_preview(
            db,
            target_key=payload.target_key,
            expected_revision=payload.expected_revision,
            draft=payload.draft,
            requested_by_user_id=reader.id if isinstance(reader, User) else None,
        )
        db.commit()
        return rendered
    except (LifecycleError, SQLAlchemyError) as exc:
        raise _http_error(db, exc) from exc


@router.put(
    "/policies/{target_key}",
    response_model=LifecyclePolicyResponse,
    openapi_extra=_BROWSER_ONLY,
)
def put_lifecycle_policy(
    target_key: LifecycleTargetKey,
    payload: LifecyclePolicyUpdateRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_lifecycle_writer),
) -> LifecyclePolicyResponse:
    response.headers["Cache-Control"] = "no-store"
    action = "lifecycle.policy.update"
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload={"target_key": target_key, **payload.model_dump(mode="json")},
        )
        locked_actor, replay = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            operation_label="changing data lifecycle settings",
            action=action,
            resource_type="lifecycle_policy",
            resource_id=target_key,
        )
        if replay is not None:
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = replay.http_status
            return LifecyclePolicyResponse.model_validate(
                governance_operation_replay_payload(replay)
            )
        before_policy = lifecycle_policy_audit_snapshot(
            db,
            target_key=target_key,
        )
        rendered = update_lifecycle_policy(
            db,
            target_key=target_key,
            payload=payload,
            actor=locked_actor,
        )
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="lifecycle_policy",
            resource_id=_policy_resource_id(target_key),
            rendered=rendered,
            http_status=status.HTTP_200_OK,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="lifecycle_policy",
            resource_id=target_key,
            metadata={
                "target_key": target_key,
                "before": before_policy,
                "after": lifecycle_policy_response_audit_snapshot(rendered),
                "purge_confirmed": payload.confirmation == "PURGE",
                "reason": payload.reason,
                "preview": lifecycle_preview_evidence(
                    db,
                    preview_id=payload.preview_id,
                    consumed_for_policy_revision=rendered.revision,
                ),
            },
        )
        commit_governance_mutation(db, action=action)
        return rendered
    except (
        LifecycleError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, "lifecycle_policy", target_key, exc)
        raise _http_error(db, exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="lifecycle", operation="update_policy", exc=exc
        )


@router.get("/runs", response_model=LifecycleRunListResponse)
def get_lifecycle_runs(
    response: Response,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=25, ge=1, le=100),
    target_key: LifecycleTargetKey | None = None,
    run_status: LifecycleRunStatus | None = Query(default=None, alias="status"),
    trigger_source: LifecycleRunTrigger | None = None,
    db: Session = Depends(get_db),
    _reader: AuthenticatedPrincipal = Depends(_read_permission),
) -> LifecycleRunListResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return list_lifecycle_runs(
            db,
            page=page,
            page_size=page_size,
            target_key=target_key,
            status=run_status,
            trigger_source=trigger_source,
        )
    except (LifecycleError, SQLAlchemyError) as exc:
        raise _http_error(db, exc) from exc


@router.get("/runs/{run_id}", response_model=LifecycleRunResponse)
def get_lifecycle_run_detail(
    run_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    _reader: AuthenticatedPrincipal = Depends(_read_permission),
) -> LifecycleRunResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_lifecycle_run(db, run_id)
    except (LifecycleError, SQLAlchemyError) as exc:
        raise _http_error(db, exc) from exc


@router.post(
    "/runs",
    response_model=LifecycleRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=_BROWSER_ONLY,
)
def post_lifecycle_run(
    payload: LifecycleRunCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_lifecycle_writer),
) -> LifecycleRunResponse:
    response.headers["Cache-Control"] = "no-store"
    action = "lifecycle.run.request"
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload=payload.model_dump(mode="json"),
        )
        locked_actor, receipt = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            operation_label="starting lifecycle cleanup",
            action=action,
            resource_type="lifecycle_run",
            resource_id=None,
        )
        if receipt is not None:
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = receipt.http_status
            return LifecycleRunResponse.model_validate(
                governance_operation_replay_payload(receipt)
            )
        rendered, replayed = create_manual_lifecycle_run(
            db,
            target_key=payload.target_key,
            expected_revision=payload.expected_revision,
            preview_id=payload.preview_id,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            actor=locked_actor,
        )
        response_status = status.HTTP_200_OK if replayed else status.HTTP_202_ACCEPTED
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="lifecycle_run",
            resource_id=rendered.id,
            rendered=rendered,
            http_status=response_status,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="lifecycle_run",
            resource_id=str(rendered.id),
            metadata={
                "target_key": rendered.target_key,
                "policy_revision": rendered.policy_revision,
                "cutoff_at": rendered.cutoff_at.isoformat(),
                "reason": payload.reason,
                "preview": dict(rendered.details).get("preview"),
            },
        )
        commit_governance_mutation(db, action=action)
        response.status_code = response_status
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        if not replayed:
            _publish_run_best_effort(db, rendered.id)
        return rendered
    except (
        LifecycleError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, "lifecycle_run", None, exc)
        raise _http_error(db, exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="lifecycle", operation="create_run", exc=exc
        )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=LifecycleRunResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_lifecycle_run_cancel(
    run_id: uuid.UUID,
    payload: LifecycleRunCancelRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_lifecycle_writer),
) -> LifecycleRunResponse:
    response.headers["Cache-Control"] = "no-store"
    action = "lifecycle.run.cancel"
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload={"run_id": str(run_id), **payload.model_dump(mode="json")},
        )
        locked_actor, receipt = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            operation_label="cancelling lifecycle cleanup",
            action=action,
            resource_type="lifecycle_run",
            resource_id=str(run_id),
        )
        if receipt is not None:
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = receipt.http_status
            return LifecycleRunResponse.model_validate(
                governance_operation_replay_payload(receipt)
            )
        rendered = cancel_lifecycle_run(
            db,
            run_id=run_id,
            reason=payload.reason,
            actor=locked_actor,
        )
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="lifecycle_run",
            resource_id=run_id,
            rendered=rendered,
            http_status=status.HTTP_200_OK,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="lifecycle_run",
            resource_id=str(run_id),
            metadata={"target_key": rendered.target_key, "reason": payload.reason},
        )
        commit_governance_mutation(db, action=action)
        return rendered
    except (
        LifecycleError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, "lifecycle_run", str(run_id), exc)
        raise _http_error(db, exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="lifecycle", operation="cancel_run", exc=exc
        )


def _publish_run_best_effort(db: Session, run_id: uuid.UUID) -> None:
    try:
        from app.tasks.lifecycle_tasks import enqueue_lifecycle_run

        enqueue_lifecycle_run(db, run_id=run_id)
    except Exception:
        db.rollback()
        logger.warning(
            "lifecycle_run_immediate_publish_failed run_id=%s; dispatcher will retry",
            run_id,
            exc_info=True,
        )


def _prepare_mutation(
    db: Session,
    *,
    request: Request,
    actor: User,
    identity: GovernanceOperationIdentity,
    operation_label: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
) -> tuple[User, GovernanceOperationReceipt | None]:
    lock_governance_operation_identity(db, actor_user_id=actor.id, identity=identity)
    locked_actor, _authorization = authorize_governance_actor(
        db,
        request=request,
        actor=actor,
        required_permission=SCOPE_WRITE_OPERATIONS,
        durable=True,
    )
    try:
        require_sensitive_browser_session(
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
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=exc.error_code,
        )
        raise
    receipt = find_governance_operation_replay(
        db,
        actor_user_id=locked_actor.id,
        identity=identity,
    )
    return locked_actor, receipt


def _identity(
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


def _record_receipt(
    db: Session,
    *,
    actor: User,
    identity: GovernanceOperationIdentity,
    resource_type: str,
    resource_id: uuid.UUID,
    rendered: BaseModel,
    http_status: int,
) -> None:
    record_governance_operation_receipt(
        db,
        actor_user_id=actor.id,
        identity=identity,
        resource_type=resource_type,
        resource_id=resource_id,
        response_json=rendered.model_dump(mode="json"),
        http_status=http_status,
    )


def _policy_resource_id(target_key: str) -> uuid.UUID:
    return uuid.uuid5(_POLICY_RESOURCE_NAMESPACE, target_key)


def _reject(
    db: Session,
    request: Request,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | None,
    exc: Exception,
) -> None:
    reason = getattr(exc, "error_code", None) or getattr(exc, "code", None)
    record_rejected_governance_mutation(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=str(reason or type(exc).__name__),
    )


def _http_error(db: Session, exc: Exception) -> ApiHTTPException:
    db.rollback()
    if isinstance(exc, GovernanceAuthorizationDenied):
        return governance_authorization_http_error(exc)
    if isinstance(exc, GovernanceIdempotencyError):
        return ApiHTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if isinstance(exc, GovernanceIdempotencyKeyInvalid)
                else status.HTTP_409_CONFLICT
            ),
            detail=str(exc),
            error_code=exc.code,
        )
    if isinstance(exc, LifecycleNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, LifecycleConflict):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, LifecycleValidationError):
        code = status.HTTP_400_BAD_REQUEST
    else:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.error("lifecycle_storage_error", exc_info=exc)
    return ApiHTTPException(
        status_code=code,
        detail=str(exc)
        if isinstance(exc, LifecycleError)
        else "Lifecycle storage is unavailable.",
        error_code=getattr(exc, "error_code", "lifecycle_storage_unavailable"),
    )


__all__ = ["require_lifecycle_writer", "router"]
