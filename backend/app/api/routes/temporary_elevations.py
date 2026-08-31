from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_auth_credential_kind,
    get_authorization_context,
    get_current_auth_session_id,
    get_current_user,
    require_permissions,
    resolve_client_ip,
)
from app.api.sensitive_action_auth import require_sensitive_browser_session
from app.core.api_errors import ApiHTTPException
from app.core.logging_config import update_log_context
from app.core.token_scopes import (
    SCOPE_APPROVE_ELEVATIONS,
    SCOPE_READ_ELEVATIONS,
    SCOPE_WRITE_ELEVATIONS,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.temporary_elevation import (
    ElevationCloseRequest,
    ElevationDecisionRequest,
    ElevationRequestCreate,
    TemporaryElevationListResponse,
    TemporaryElevationResponse,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)
from app.services.iam_delegation import IAMDelegationDenied
from app.services.governance_idempotency import (
    GovernanceIdempotencyError,
    GovernanceIdempotencyKeyInvalid,
    build_governance_operation_identity,
    find_governance_operation_replay,
    governance_operation_replay_payload,
    lock_governance_operation_identity,
    record_governance_operation_receipt,
)
from app.services.temporary_elevations import (
    TemporaryElevationConflict,
    TemporaryElevationError,
    TemporaryElevationForbidden,
    TemporaryElevationNotFound,
    close_temporary_elevation,
    create_temporary_elevation,
    decide_temporary_elevation,
    get_temporary_elevation_response,
    list_temporary_elevations,
)


router = APIRouter(prefix="/iam/elevations", tags=["elevations"])
logger = logging.getLogger("threatlens.elevations")


@router.get("", response_model=TemporaryElevationListResponse)
def get_elevations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    target_user_id: uuid.UUID | None = None,
    stored_status: Literal["pending", "approved", "denied", "cancelled", "revoked"]
    | None = None,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_ELEVATIONS)),
) -> TemporaryElevationListResponse:
    try:
        return list_temporary_elevations(
            db,
            page=page,
            page_size=page_size,
            target_user_id=target_user_id,
            stored_status=stored_status,
        )
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "list", exc)


@router.get("/{elevation_id}", response_model=TemporaryElevationResponse)
def get_elevation(
    elevation_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_ELEVATIONS)),
) -> TemporaryElevationResponse:
    try:
        return get_temporary_elevation_response(db, elevation_id)
    except TemporaryElevationError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "get", exc)


@router.post(
    "",
    response_model=TemporaryElevationResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_elevation(
    payload: ElevationRequestCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ELEVATIONS)),
) -> TemporaryElevationResponse:
    try:
        identity = build_governance_operation_identity(
            idempotency_key,
            operation="elevation.create",
            payload=payload.model_dump(mode="json"),
        )
        lock_governance_operation_identity(
            db, actor_user_id=actor.id, identity=identity
        )
        locked_actor, authorization = _lock_and_reauthorize_actor(
            db,
            request=request,
            actor=actor,
            permission=SCOPE_WRITE_ELEVATIONS,
        )
        replay = find_governance_operation_replay(
            db, actor_user_id=locked_actor.id, identity=identity
        )
        if replay is not None:
            return _replay_response(
                response, governance_operation_replay_payload(replay)
            )
        result = create_temporary_elevation(
            db,
            requester=locked_actor,
            payload=payload,
            can_request_for_others=authorization.has_durable(SCOPE_APPROVE_ELEVATIONS),
        )
        rendered = get_temporary_elevation_response(db, result.elevation.id)
        record_governance_operation_receipt(
            db,
            actor_user_id=locked_actor.id,
            identity=identity,
            resource_type="temporary_elevation",
            resource_id=result.elevation.id,
            response_json=rendered.model_dump(mode="json"),
            http_status=status.HTTP_201_CREATED,
        )
        _record_request_audit(
            db,
            request=request,
            actor=locked_actor,
            action="elevations.request.create",
            resource_id=str(result.elevation.id),
            metadata={
                "target_user_id": str(result.elevation.target_user_id),
                "role_id": str(result.elevation.role_id),
                "role_revision": result.elevation.role_revision_snapshot,
                "permission_snapshot": rendered.permission_snapshot,
                "duration_seconds": result.elevation.requested_duration_seconds,
            },
        )
        _commit_mutation(db, action="elevations.request.create")
        response.headers["X-Current-Revision"] = str(result.elevation.revision)
        response.headers["X-ThreatLens-Mutation-Changed"] = "true"
        return rendered
    except (
        TemporaryElevationError,
        IAMDelegationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="elevations.request.create",
            resource_id=None,
            reason=getattr(exc, "code", "iam_delegation_denied"),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "create", exc)


@router.post(
    "/{elevation_id}/decision",
    response_model=TemporaryElevationResponse,
)
def post_elevation_decision(
    elevation_id: uuid.UUID,
    payload: ElevationDecisionRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_APPROVE_ELEVATIONS)),
) -> TemporaryElevationResponse:
    try:
        identity = build_governance_operation_identity(
            idempotency_key,
            operation="elevation.decision",
            payload={
                "elevation_id": str(elevation_id),
                **payload.model_dump(mode="json"),
            },
        )
        lock_governance_operation_identity(
            db, actor_user_id=actor.id, identity=identity
        )
        locked_actor, authorization = _lock_and_reauthorize_actor(
            db,
            request=request,
            actor=actor,
            permission=SCOPE_APPROVE_ELEVATIONS,
        )
        if not authorization.has_durable(SCOPE_APPROVE_ELEVATIONS):
            raise TemporaryElevationForbidden(
                "Elevation approval authority must come from durable access, not another temporary grant."
            )
        replay = find_governance_operation_replay(
            db, actor_user_id=locked_actor.id, identity=identity
        )
        if replay is not None:
            return _replay_response(
                response, governance_operation_replay_payload(replay)
            )
        try:
            require_sensitive_browser_session(
                db,
                request=request,
                user=locked_actor,
                action="elevation_decision",
                operation_label="deciding a temporary elevation request",
            )
        except ApiHTTPException as exc:
            _record_rejected_mutation(
                db,
                request=request,
                actor=actor,
                action="elevations.request.decision",
                resource_id=str(elevation_id),
                reason=exc.error_code or "sensitive_authentication_required",
            )
            raise
        result = decide_temporary_elevation(
            db,
            elevation_id=elevation_id,
            approver=locked_actor,
            approver_authorization=authorization,
            payload=payload,
        )
        rendered = get_temporary_elevation_response(db, elevation_id)
        record_governance_operation_receipt(
            db,
            actor_user_id=locked_actor.id,
            identity=identity,
            resource_type="temporary_elevation",
            resource_id=elevation_id,
            response_json=rendered.model_dump(mode="json"),
            http_status=status.HTTP_200_OK,
        )
        _record_request_audit(
            db,
            request=request,
            actor=locked_actor,
            action=(
                "elevations.request.approve"
                if payload.approve
                else "elevations.request.deny"
            ),
            resource_id=str(elevation_id),
            metadata={
                "target_user_id": str(result.elevation.target_user_id),
                "role_id": (
                    str(result.elevation.role_id)
                    if result.elevation.role_id is not None
                    else None
                ),
                "duration_seconds": result.elevation.requested_duration_seconds,
                "permission_snapshot": rendered.permission_snapshot,
                "grant_expires_at": (
                    result.elevation.grant_expires_at.isoformat()
                    if result.elevation.grant_expires_at is not None
                    else None
                ),
            },
        )
        _commit_mutation(db, action="elevations.request.decision")
        response.headers["X-Current-Revision"] = str(result.elevation.revision)
        response.headers["X-ThreatLens-Mutation-Changed"] = "true"
        return rendered
    except (
        TemporaryElevationError,
        IAMDelegationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="elevations.request.decision",
            resource_id=str(elevation_id),
            reason=getattr(exc, "code", "iam_delegation_denied"),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "decide", exc)


@router.post(
    "/{elevation_id}/close",
    response_model=TemporaryElevationResponse,
)
def post_elevation_close(
    elevation_id: uuid.UUID,
    payload: ElevationCloseRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
) -> TemporaryElevationResponse:
    try:
        identity = build_governance_operation_identity(
            idempotency_key,
            operation="elevation.close",
            payload={
                "elevation_id": str(elevation_id),
                **payload.model_dump(mode="json"),
            },
        )
        lock_governance_operation_identity(
            db, actor_user_id=actor.id, identity=identity
        )
        locked_actor, authorization = _lock_and_reauthorize_actor(
            db,
            request=request,
            actor=actor,
            permission=None,
        )
        replay = find_governance_operation_replay(
            db, actor_user_id=locked_actor.id, identity=identity
        )
        if replay is not None:
            return _replay_response(
                response, governance_operation_replay_payload(replay)
            )
        try:
            require_sensitive_browser_session(
                db,
                request=request,
                user=locked_actor,
                action="elevation_close",
                operation_label="closing a temporary elevation request",
            )
        except ApiHTTPException as exc:
            _record_rejected_mutation(
                db,
                request=request,
                actor=actor,
                action="elevations.request.close",
                resource_id=str(elevation_id),
                reason=exc.error_code or "sensitive_authentication_required",
            )
            raise
        result = close_temporary_elevation(
            db,
            elevation_id=elevation_id,
            actor=locked_actor,
            can_manage_others=authorization.has_durable(SCOPE_APPROVE_ELEVATIONS),
            payload=payload,
        )
        rendered = get_temporary_elevation_response(db, elevation_id)
        record_governance_operation_receipt(
            db,
            actor_user_id=locked_actor.id,
            identity=identity,
            resource_type="temporary_elevation",
            resource_id=elevation_id,
            response_json=rendered.model_dump(mode="json"),
            http_status=status.HTTP_200_OK,
        )
        _record_request_audit(
            db,
            request=request,
            actor=locked_actor,
            action=(
                "elevations.grant.revoke"
                if result.elevation.status == "revoked"
                else "elevations.request.cancel"
            ),
            resource_id=str(elevation_id),
            metadata={
                "target_user_id": str(result.elevation.target_user_id),
                "role_id": (
                    str(result.elevation.role_id)
                    if result.elevation.role_id is not None
                    else None
                ),
                "previous_status": result.previous_status,
            },
        )
        _commit_mutation(db, action="elevations.request.close")
        response.headers["X-Current-Revision"] = str(result.elevation.revision)
        response.headers["X-ThreatLens-Mutation-Changed"] = "true"
        return rendered
    except (
        TemporaryElevationError,
        IAMDelegationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="elevations.request.close",
            resource_id=str(elevation_id),
            reason=getattr(exc, "code", "iam_delegation_denied"),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "close", exc)


def _lock_and_reauthorize_actor(
    db: Session,
    *,
    request: Request,
    actor: User,
    permission: str | None,
) -> tuple[User, AuthorizationContext]:
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
            _record_rejected_mutation(
                db,
                request=request,
                actor=actor,
                action="elevations.authorization.reject",
                resource_id=None,
                reason="actor_missing_or_ineligible",
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account access changed while this governance operation was being authorized. Sign in again and retry.",
                error_code="governance_actor_access_changed",
            )
        previous = get_authorization_context(request)
        credential_scopes = previous.credential_grants if previous is not None else None
        authorization = authorization_context_for_user(
            db,
            locked_actor,
            credential_scopes=credential_scopes,
        )
        if permission is not None and not authorization.has(permission):
            _record_rejected_mutation(
                db,
                request=request,
                actor=locked_actor,
                action="elevations.authorization.reject",
                resource_id=None,
                reason="permission_changed_during_request",
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your governance permission changed while this request was being authorized. Reload and retry.",
                error_code="governance_actor_access_changed",
                error_context={"required_permission": permission},
            )
        request.state.authorization_context = authorization
        authorization_elevation_ids = (
            authorization.authorizing_elevation_ids([permission])
            if permission is not None
            else ()
        )
        request.state.authorization_elevation_ids = authorization_elevation_ids
        update_log_context(
            authorization_elevation_ids=(
                ",".join(str(value) for value in authorization_elevation_ids)
                if authorization_elevation_ids
                else None
            )
        )
        return locked_actor, authorization
    except AuthorizationStateUnavailable as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            error_code="iam_policy_unavailable",
        ) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "authorize", exc)


def _record_request_audit(
    db: Session,
    *,
    request: Request,
    actor: User,
    action: str,
    resource_id: str | None,
    metadata: dict[str, object],
    success: bool = True,
) -> None:
    credential_id = getattr(request.state, "api_token_id", None)
    if credential_id is None:
        credential_id = get_current_auth_session_id(request)
    record_audit(
        db,
        actor_user_id=actor.id,
        actor_principal_type="user",
        actor_principal_id=actor.id,
        credential_kind=get_auth_credential_kind(request),
        credential_id=credential_id,
        request_id=getattr(request.state, "request_id", None),
        source_ip=resolve_client_ip(request),
        action=action,
        resource_type="temporary_elevation",
        resource_id=resource_id,
        success=success,
        metadata=metadata,
    )


def _record_rejected_mutation(
    db: Session,
    *,
    request: Request,
    actor: User,
    action: str,
    resource_id: str | None,
    reason: str,
) -> None:
    db.rollback()
    try:
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action=action,
            resource_id=resource_id,
            success=False,
            metadata={"reason": reason},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "elevation_rejection_audit_failed action=%s actor_id=%s error_type=%s",
            action,
            actor.id,
            type(exc).__name__,
            exc_info=True,
        )


def _commit_mutation(db: Session, *, action: str) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(
            "elevation_commit_outcome_unknown action=%s error_type=%s",
            action,
            type(exc).__name__,
            exc_info=True,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The database did not confirm the governance operation. Its outcome is unknown; reload the elevation request before retrying."
            ),
            error_code="governance_commit_outcome_unknown",
        ) from exc


def _raise_storage_error(db: Session, action: str, exc: SQLAlchemyError) -> None:
    db.rollback()
    constraint_name = getattr(
        getattr(getattr(exc, "orig", None), "diag", None),
        "constraint_name",
        None,
    )
    logger.error(
        "elevation_storage_unavailable action=%s error_type=%s",
        action,
        type(exc).__name__,
        exc_info=True,
    )
    raise ApiHTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Temporary elevation storage is temporarily unavailable. No result can be shown; retry shortly.",
        error_code="temporary_elevation_storage_unavailable",
        error_context={
            "storage_operation": action,
            "error_type": type(exc).__name__,
            "constraint": constraint_name,
        },
    ) from exc


def _http_error(
    exc: TemporaryElevationError | IAMDelegationDenied | GovernanceIdempotencyError,
) -> ApiHTTPException:
    if isinstance(exc, TemporaryElevationNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (TemporaryElevationForbidden, IAMDelegationDenied)):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, GovernanceIdempotencyKeyInvalid):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, (TemporaryElevationConflict, GovernanceIdempotencyError)):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    context: dict[str, object] = {}
    current_revision = getattr(exc, "current_revision", None)
    if current_revision is not None:
        context["current_revision"] = current_revision
    missing_permissions = getattr(exc, "missing_permissions", None)
    if missing_permissions:
        context["missing_permissions"] = list(missing_permissions)
    return ApiHTTPException(
        status_code=status_code,
        detail=str(exc),
        error_code=getattr(exc, "code", "iam_delegation_denied"),
        error_context=context or None,
    )


def _replay_response(
    response: Response, response_json: dict[str, object]
) -> TemporaryElevationResponse:
    rendered = TemporaryElevationResponse.model_validate(response_json)
    response.headers["X-Current-Revision"] = str(rendered.revision)
    response.headers["X-ThreatLens-Mutation-Changed"] = "false"
    return rendered


__all__ = ["router"]
