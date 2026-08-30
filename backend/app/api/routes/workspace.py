from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_auth_credential_kind,
    get_authorization_context,
    get_current_auth_session_id,
    require_permissions,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import (
    SCOPE_READ_WORKSPACE,
    SCOPE_WRITE_WORKSPACE,
    SCOPE_WRITE_WORKSPACE_PREFERENCES,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.workspace import (
    WorkspaceEffectiveResponse,
    WorkspaceRegistryResponse,
    WorkspaceRolePolicyResetRequest,
    WorkspaceRolePolicyResponse,
    WorkspaceRolePolicyWriteRequest,
    WorkspaceUserPreferenceResponse,
    WorkspaceUserPreferenceResetRequest,
    WorkspaceUserPreferenceWriteRequest,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationStateUnavailable,
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)
from app.services.workspace_policy import (
    WorkspacePolicyError,
    WorkspacePolicyUnavailable,
    effective_workspace,
    get_role_policy,
    get_user_preferences,
    list_role_policies,
    reset_role_policy,
    reset_user_preferences,
    runtime_workspace_feature_flags,
    update_role_policy,
    update_user_preferences,
    workspace_registry_response,
)


logger = logging.getLogger("threatlens.workspace")
router = APIRouter(prefix="/workspace", tags=["workspace"])


class _WorkspaceActorAccessChanged(WorkspacePolicyError):
    code = "workspace_actor_access_changed"
    status_code = 403


class _WorkspaceDurableAuthorityRequired(WorkspacePolicyError):
    code = "workspace_durable_authority_required"
    status_code = 403


@router.get("/modules", response_model=WorkspaceRegistryResponse)
def get_workspace_modules(
    _reader: User = Depends(require_permissions(SCOPE_READ_WORKSPACE)),
):
    return workspace_registry_response()


@router.get("/role-policies", response_model=list[WorkspaceRolePolicyResponse])
def get_workspace_role_policies(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_WORKSPACE)),
):
    with _workspace_read_errors(db):
        return list_role_policies(db)


@router.get("/role-policies/{role}", response_model=WorkspaceRolePolicyResponse)
def get_workspace_role_policy(
    role: str,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_WORKSPACE)),
):
    with _workspace_read_errors(db):
        return get_role_policy(db, role)


@router.put("/role-policies/{role}", response_model=WorkspaceRolePolicyResponse)
def put_workspace_role_policy(
    role: str,
    payload: WorkspaceRolePolicyWriteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_WORKSPACE)),
):
    actor_id = actor.id
    try:
        locked_actor = _lock_and_reauthorize_actor(
            db,
            request,
            actor,
            required_permission=SCOPE_WRITE_WORKSPACE,
            require_durable=True,
        )
        before = get_role_policy(db, role)
        updated = update_role_policy(
            db,
            role=role,
            payload=payload,
            actor_user_id=locked_actor.id,
        )
        _record_request_audit(
            db,
            request=request,
            actor_user_id=locked_actor.id,
            action="workspace.role_policy.update",
            resource_type="workspace_role_policy",
            resource_id=updated.role,
            metadata={
                "before": before.model_dump(mode="json"),
                "after": updated.model_dump(mode="json"),
            },
        )
        db.commit()
        response.headers["X-Current-Revision"] = str(updated.revision)
        return updated
    except WorkspacePolicyError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor_user_id=actor_id,
            action="workspace.role_policy.update",
            resource_type="workspace_role_policy",
            resource_id=role,
            exc=exc,
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("workspace_role_policy_update_failed role=%s", role)
        raise _storage_error() from exc


@router.post("/role-policies/{role}/reset", response_model=WorkspaceRolePolicyResponse)
def reset_workspace_role_policy(
    role: str,
    payload: WorkspaceRolePolicyResetRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_WORKSPACE)),
):
    actor_id = actor.id
    try:
        locked_actor = _lock_and_reauthorize_actor(
            db,
            request,
            actor,
            required_permission=SCOPE_WRITE_WORKSPACE,
            require_durable=True,
        )
        before = get_role_policy(db, role)
        reset = reset_role_policy(
            db,
            role=role,
            expected_revision=payload.expected_revision,
            actor_user_id=locked_actor.id,
        )
        _record_request_audit(
            db,
            request=request,
            actor_user_id=locked_actor.id,
            action="workspace.role_policy.reset",
            resource_type="workspace_role_policy",
            resource_id=reset.role,
            metadata={
                "before": before.model_dump(mode="json"),
                "after": reset.model_dump(mode="json"),
            },
        )
        db.commit()
        response.headers["X-Current-Revision"] = str(reset.revision)
        return reset
    except WorkspacePolicyError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor_user_id=actor_id,
            action="workspace.role_policy.reset",
            resource_type="workspace_role_policy",
            resource_id=role,
            exc=exc,
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("workspace_role_policy_reset_failed role=%s", role)
        raise _storage_error() from exc


@router.get("/preferences", response_model=WorkspaceUserPreferenceResponse)
def get_my_workspace_preferences(
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_WORKSPACE)),
):
    with _workspace_read_errors(db):
        return get_user_preferences(db, user)


@router.put("/preferences", response_model=WorkspaceUserPreferenceResponse)
def put_my_workspace_preferences(
    payload: WorkspaceUserPreferenceWriteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_WORKSPACE_PREFERENCES)),
):
    actor_id = actor.id
    try:
        locked_actor = _lock_and_reauthorize_actor(
            db,
            request,
            actor,
            required_permission=SCOPE_WRITE_WORKSPACE_PREFERENCES,
        )
        before = get_user_preferences(db, locked_actor)
        updated = update_user_preferences(
            db,
            user=locked_actor,
            payload=payload,
            actor_user_id=locked_actor.id,
        )
        _record_request_audit(
            db,
            request=request,
            actor_user_id=locked_actor.id,
            action="workspace.preferences.update",
            resource_type="workspace_user_preferences",
            resource_id=str(locked_actor.id),
            metadata={
                "before": before.model_dump(mode="json"),
                "after": updated.model_dump(mode="json"),
            },
        )
        db.commit()
        response.headers["X-Current-Revision"] = str(updated.revision)
        return updated
    except WorkspacePolicyError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor_user_id=actor_id,
            action="workspace.preferences.update",
            resource_type="workspace_user_preferences",
            resource_id=str(actor_id),
            exc=exc,
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("workspace_preferences_update_failed user_id=%s", actor_id)
        raise _storage_error() from exc


@router.post("/preferences/reset", response_model=WorkspaceUserPreferenceResponse)
def reset_my_workspace_preferences(
    payload: WorkspaceUserPreferenceResetRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_WORKSPACE_PREFERENCES)),
):
    actor_id = actor.id
    try:
        locked_actor = _lock_and_reauthorize_actor(
            db,
            request,
            actor,
            required_permission=SCOPE_WRITE_WORKSPACE_PREFERENCES,
        )
        before = get_user_preferences(db, locked_actor)
        reset = reset_user_preferences(
            db,
            user=locked_actor,
            expected_revision=payload.expected_revision,
        )
        _record_request_audit(
            db,
            request=request,
            actor_user_id=locked_actor.id,
            action="workspace.preferences.reset",
            resource_type="workspace_user_preferences",
            resource_id=str(locked_actor.id),
            metadata={
                "before": before.model_dump(mode="json"),
                "after": reset.model_dump(mode="json"),
            },
        )
        db.commit()
        response.headers["X-Current-Revision"] = str(reset.revision)
        return reset
    except WorkspacePolicyError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor_user_id=actor_id,
            action="workspace.preferences.reset",
            resource_type="workspace_user_preferences",
            resource_id=str(actor_id),
            exc=exc,
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("workspace_preferences_reset_failed user_id=%s", actor_id)
        raise _storage_error() from exc


@router.get("/effective", response_model=WorkspaceEffectiveResponse)
def get_my_effective_workspace(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_WORKSPACE)),
):
    authorization = get_authorization_context(request)
    if authorization is None:
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Effective workspace access could not be resolved. Retry the request.",
            error_code="workspace_authorization_unavailable",
        )
    with _workspace_read_errors(db):
        return effective_workspace(
            db,
            user=user,
            authorization=authorization,
            feature_flags=runtime_workspace_feature_flags(db),
        )


def _lock_and_reauthorize_actor(
    db: Session,
    request: Request,
    actor: User,
    *,
    required_permission: str,
    require_durable: bool = False,
) -> User:
    try:
        lock_iam_policy_for_mutation(db)
        locked_actor = db.scalar(
            select(User)
            .where(User.id == actor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            locked_actor is None
            or not locked_actor.is_active
            or not locked_actor.is_approved
        ):
            raise _WorkspaceActorAccessChanged(
                "Your account access changed while the workspace update was being authorized. Sign in again and retry."
            )
        previous_context = get_authorization_context(request)
        credential_scopes = (
            previous_context.credential_grants if previous_context is not None else None
        )
        refreshed_context = authorization_context_for_user(
            db,
            locked_actor,
            credential_scopes=credential_scopes,
        )
        if not refreshed_context.has(required_permission):
            raise _WorkspaceActorAccessChanged(
                "Your workspace permission changed while this request was being authorized. Reload and retry."
            )
        if require_durable and not refreshed_context.has_durable(required_permission):
            raise _WorkspaceDurableAuthorityRequired(
                "Workspace role-policy changes require durably assigned authority. Temporary access cannot alter persistent workspace policy."
            )
        request.state.authorization_context = refreshed_context
        return locked_actor
    except AuthorizationStateUnavailable as exc:
        raise WorkspacePolicyUnavailable(
            str(exc), context={"reason": "iam_policy_unavailable"}
        ) from exc


@contextmanager
def _workspace_read_errors(db: Session) -> Iterator[None]:
    try:
        yield
    except WorkspacePolicyError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("workspace_policy_read_failed")
        raise _storage_error() from exc


def _record_request_audit(
    db: Session,
    *,
    request: Request,
    actor_user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata: dict[str, object],
    success: bool = True,
) -> None:
    credential_id = getattr(request.state, "api_token_id", None)
    if credential_id is None:
        credential_id = get_current_auth_session_id(request)
    record_audit(
        db,
        actor_user_id=actor_user_id,
        actor_principal_type="user",
        actor_principal_id=actor_user_id,
        credential_kind=get_auth_credential_kind(request),
        credential_id=credential_id,
        request_id=getattr(request.state, "request_id", None),
        source_ip=resolve_client_ip(request),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=success,
        metadata=metadata,
    )


def _record_rejected_mutation(
    db: Session,
    *,
    request: Request,
    actor_user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None,
    exc: WorkspacePolicyError,
) -> None:
    db.rollback()
    try:
        _record_request_audit(
            db,
            request=request,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=False,
            metadata={
                "reason": exc.code,
                "context": exc.context or {},
            },
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "workspace_rejection_audit_failed action=%s error_code=%s",
            action,
            exc.code,
        )


def _http_error(exc: WorkspacePolicyError) -> ApiHTTPException:
    headers = (
        {"X-Current-Revision": str(exc.current_revision)}
        if exc.current_revision is not None
        else None
    )
    return ApiHTTPException(
        status_code=exc.status_code,
        detail=exc.detail,
        error_code=exc.code,
        error_context=exc.context,
        headers=headers,
    )


def _storage_error() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Workspace policy storage is temporarily unavailable. Retry the request.",
        error_code="workspace_storage_unavailable",
    )


__all__ = ["router"]
