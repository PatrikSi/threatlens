from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import NoReturn

from fastapi import Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_auth_credential_kind,
    get_authorization_context,
    get_current_auth_session_id,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.logging_config import remove_log_context, update_log_context
from app.models.user import User
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
)
from app.services.governance_authorization import (
    GovernanceAuthorizationDenied,
    lock_and_authorize_governance_user,
)


logger = logging.getLogger("threatlens.governance")


def authorize_governance_actor(
    db: Session,
    *,
    request: Request,
    actor: User,
    required_permission: str | None,
    durable: bool = False,
) -> tuple[User, AuthorizationContext]:
    previous = get_authorization_context(request)
    credential_scopes = previous.credential_grants if previous is not None else None
    request.state.authorization_elevation_ids = ()
    remove_log_context("authorization_elevation_ids")
    try:
        locked_actor, authorization = lock_and_authorize_governance_user(
            db,
            user_id=actor.id,
            credential_scopes=credential_scopes,
            required_permission=required_permission,
            durable=durable,
        )
    except AuthorizationStateUnavailable as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            error_code="iam_policy_unavailable",
        ) from exc
    request.state.authorization_context = authorization
    set_governance_authorization_context(
        request,
        authorization,
        [required_permission] if required_permission is not None else [],
    )
    return locked_actor, authorization


def set_governance_authorization_context(
    request: Request,
    authorization: AuthorizationContext,
    required_permissions: Iterable[str],
) -> None:
    elevation_ids = authorization.authorizing_elevation_ids(required_permissions)
    request.state.authorization_elevation_ids = elevation_ids
    if elevation_ids:
        update_log_context(
            authorization_elevation_ids=",".join(str(value) for value in elevation_ids)
        )
    else:
        remove_log_context("authorization_elevation_ids")


def record_governance_audit(
    db: Session,
    *,
    request: Request,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata: dict[str, object],
    success: bool = True,
    authorization_approval_id: uuid.UUID | None = None,
    execution_receipt_id: uuid.UUID | None = None,
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
        authorization_approval_id=authorization_approval_id,
        execution_receipt_id=execution_receipt_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=success,
        metadata=metadata,
    )


def record_rejected_governance_mutation(
    db: Session,
    *,
    request: Request,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | None,
    reason: str,
    context: dict[str, object] | None = None,
) -> None:
    db.rollback()
    try:
        record_governance_audit(
            db,
            request=request,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=False,
            metadata={"reason": reason, **(context or {})},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "governance_rejection_audit_failed action=%s actor_id=%s error_type=%s",
            action,
            actor.id,
            type(exc).__name__,
            exc_info=True,
        )


def commit_governance_mutation(db: Session, *, action: str) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(
            "governance_commit_outcome_unknown action=%s error_type=%s",
            action,
            type(exc).__name__,
            exc_info=True,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The database did not confirm the governance operation. Its outcome "
                "is unknown; reload the resource before retrying with the same Idempotency-Key."
            ),
            error_code="governance_commit_outcome_unknown",
        ) from exc


def raise_governance_storage_error(
    db: Session,
    *,
    subsystem: str,
    operation: str,
    exc: SQLAlchemyError,
) -> NoReturn:
    db.rollback()
    constraint_name = getattr(
        getattr(getattr(exc, "orig", None), "diag", None),
        "constraint_name",
        None,
    )
    logger.error(
        "governance_storage_unavailable subsystem=%s operation=%s error_type=%s",
        subsystem,
        operation,
        type(exc).__name__,
        exc_info=True,
    )
    raise ApiHTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"{subsystem.replace('_', ' ').title()} storage is temporarily unavailable. "
            "No result can be confirmed; retry shortly."
        ),
        error_code=f"{subsystem}_storage_unavailable",
        error_context={
            "storage_operation": operation,
            "error_type": type(exc).__name__,
            "constraint": constraint_name,
        },
    ) from exc


def governance_authorization_http_error(
    exc: GovernanceAuthorizationDenied,
) -> ApiHTTPException:
    context: dict[str, object] = {"reason": exc.reason}
    if exc.required_permission is not None:
        context["required_permission"] = exc.required_permission
    return ApiHTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=str(exc),
        error_code=exc.code,
        error_context=context,
    )


__all__ = [
    "authorize_governance_actor",
    "commit_governance_mutation",
    "governance_authorization_http_error",
    "raise_governance_storage_error",
    "record_governance_audit",
    "record_rejected_governance_mutation",
    "set_governance_authorization_context",
]
