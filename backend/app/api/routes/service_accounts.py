from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
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
    SCOPE_READ_SERVICE_ACCOUNTS,
    SCOPE_WRITE_SERVICE_ACCOUNTS,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.service_account import (
    ServiceAccountCreateRequest,
    ServiceAccountCredentialIssueRequest,
    ServiceAccountCredentialIssueResponse,
    ServiceAccountCredentialListResponse,
    ServiceAccountCredentialRotateResponse,
    ServiceAccountCredentialResponse,
    ServiceAccountResponse,
    ServiceAccountListResponse,
    ServiceAccountRevisionRequest,
    ServiceAccountRoleAssignmentRequest,
    ServiceAccountRoleAssignmentResponse,
    ServiceAccountUpdateRequest,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)
from app.services.service_accounts import (
    ServiceAccountCredentialGenerationFailed,
    ServiceAccountCredentialNotFound,
    ServiceAccountError,
    ServiceAccountNotFound,
    ServiceAccountRoleAssignmentNotFound,
    ServiceAccountRoleNotFound,
    ServiceAccountScopeEscalation,
    ServiceAccountScopeNotAllowed,
    add_role_assignment,
    create_service_account,
    credential_response,
    delete_service_account,
    disable_service_account,
    get_service_account_response,
    issue_credential,
    list_credentials,
    list_role_assignments,
    list_service_accounts,
    remove_role_assignment,
    revoke_credential,
    role_assignment_response,
    rotate_credential,
    update_service_account,
)


router = APIRouter(prefix="/iam/service-accounts", tags=["service-accounts"])
logger = logging.getLogger("threatlens.service_accounts")
NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {"description": "Service-account resource not found"}
}
CONFLICT_RESPONSE = {
    status.HTTP_409_CONFLICT: {
        "description": (
            "Stale revision, service-account policy conflict, or idempotency-key "
            "replay. A committed credential operation returns its credential ID in "
            "the structured error context; the one-time secret is never returned again."
        )
    }
}
MUTATION_RESPONSES = {**NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE}
REVISION_RESPONSE_HEADERS = {
    "X-Current-Revision": {
        "description": "Current service-account revision after the mutation.",
        "schema": {"type": "integer", "minimum": 1},
    }
}
IDEMPOTENT_MUTATION_RESPONSE_HEADERS = {
    **REVISION_RESPONSE_HEADERS,
    "X-ThreatLens-Mutation-Changed": {
        "description": "Whether this idempotent mutation changed persisted state.",
        "schema": {"type": "boolean"},
    },
}
ONE_TIME_SECRET_RESPONSE_HEADERS = {
    **REVISION_RESPONSE_HEADERS,
    "Cache-Control": {
        "description": "One-time secrets are never cacheable.",
        "schema": {"type": "string", "example": "no-store"},
    },
    "Pragma": {
        "description": "Legacy cache prevention for the one-time secret.",
        "schema": {"type": "string", "example": "no-cache"},
    },
}


def require_service_account_mutation_actor(
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_SERVICE_ACCOUNTS)),
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
            _record_actor_access_rejection(
                db,
                request=request,
                actor=actor,
                actor_exists=locked_actor is not None,
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your account access changed while the service-account "
                    "operation was being authorized. Sign in again and retry."
                ),
                error_code="service_account_actor_access_changed",
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
        if not refreshed_context.has(SCOPE_WRITE_SERVICE_ACCOUNTS):
            _record_request_audit(
                db,
                request=request,
                actor=locked_actor,
                action="service_accounts.authorization.reject",
                resource_type="service_account",
                resource_id=None,
                success=False,
                metadata={"reason": "permission_changed_during_request"},
            )
            db.commit()
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your service-account management permission changed while this "
                    "request was being authorized. Reload and retry."
                ),
                error_code="service_account_actor_access_changed",
            )
        request.state.authorization_context = refreshed_context
        return locked_actor
    except AuthorizationStateUnavailable as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            error_code="iam_policy_unavailable",
        ) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "authorize", exc)


@router.get("", response_model=ServiceAccountListResponse)
def get_service_accounts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_SERVICE_ACCOUNTS)),
):
    try:
        items, total = list_service_accounts(db, page=page, page_size=page_size)
        return ServiceAccountListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "list", exc)


@router.post(
    "",
    response_model=ServiceAccountResponse,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT_RESPONSE,
)
def post_service_account(
    payload: ServiceAccountCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        account = create_service_account(db, payload=payload, actor_user_id=actor.id)
        result = get_service_account_response(db, account.id)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.create",
            resource_type="service_account",
            resource_id=str(account.id),
            metadata={"after": result.model_dump(mode="json")},
        )
        db.commit()
        return result
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.create",
            exc=exc,
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "create", exc)


@router.get(
    "/{service_account_id}",
    response_model=ServiceAccountResponse,
    responses=NOT_FOUND_RESPONSE,
)
def get_service_account(
    service_account_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_SERVICE_ACCOUNTS)),
):
    try:
        return get_service_account_response(db, service_account_id)
    except ServiceAccountError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "get", exc)


@router.patch(
    "/{service_account_id}",
    response_model=ServiceAccountResponse,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_200_OK: {"headers": REVISION_RESPONSE_HEADERS},
    },
)
def patch_service_account(
    service_account_id: uuid.UUID,
    payload: ServiceAccountUpdateRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        before = get_service_account_response(db, service_account_id)
        update_service_account(
            db, service_account_id=service_account_id, payload=payload
        )
        result = get_service_account_response(db, service_account_id)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.update",
            resource_type="service_account",
            resource_id=str(service_account_id),
            metadata={
                "before": before.model_dump(mode="json"),
                "after": result.model_dump(mode="json"),
            },
        )
        db.commit()
        _set_revision_header(response, result.revision)
        return result
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.update",
            exc=exc,
            resource_id=str(service_account_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "update", exc)


@router.post(
    "/{service_account_id}/disable",
    response_model=ServiceAccountResponse,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_200_OK: {"headers": IDEMPOTENT_MUTATION_RESPONSE_HEADERS},
    },
)
def post_disable_service_account(
    service_account_id: uuid.UUID,
    payload: ServiceAccountRevisionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        mutation = disable_service_account(
            db,
            service_account_id=service_account_id,
            expected_revision=payload.expected_revision,
            actor_user_id=actor.id,
        )
        result = get_service_account_response(db, service_account_id)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.disable",
            resource_type="service_account",
            resource_id=str(service_account_id),
            metadata={
                "changed": mutation.changed,
                "revision": result.revision,
                "revoked_credentials": mutation.affected_count,
            },
        )
        db.commit()
        _set_mutation_headers(response, mutation)
        return result
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.disable",
            exc=exc,
            resource_id=str(service_account_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "disable", exc)


@router.get(
    "/{service_account_id}/role-assignments",
    response_model=list[ServiceAccountRoleAssignmentResponse],
    responses=NOT_FOUND_RESPONSE,
)
def get_role_assignments(
    service_account_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_SERVICE_ACCOUNTS)),
):
    try:
        return list_role_assignments(db, service_account_id)
    except ServiceAccountError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "list_roles", exc)


@router.post(
    "/{service_account_id}/role-assignments",
    response_model=ServiceAccountRoleAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_201_CREATED: {"headers": REVISION_RESPONSE_HEADERS},
    },
)
def post_role_assignment(
    service_account_id: uuid.UUID,
    payload: ServiceAccountRoleAssignmentRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        assignment = add_role_assignment(
            db,
            service_account_id=service_account_id,
            payload=payload,
            actor_user_id=actor.id,
            actor_authorization=_delegation_context(request),
        )
        result = role_assignment_response(db, assignment)
        account = get_service_account_response(db, service_account_id)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.role.assign",
            resource_type="service_account",
            resource_id=str(service_account_id),
            metadata={
                "assignment": result.model_dump(mode="json"),
                "service_account_revision": account.revision,
            },
        )
        db.commit()
        _set_revision_header(response, account.revision)
        return result
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.role.assign",
            exc=exc,
            resource_id=str(service_account_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "assign_role", exc)


@router.delete(
    "/{service_account_id}/role-assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_204_NO_CONTENT: {"headers": REVISION_RESPONSE_HEADERS},
    },
)
def delete_role_assignment(
    service_account_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    response: Response,
    expected_revision: int = Query(ge=1),
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        assignment = remove_role_assignment(
            db,
            service_account_id=service_account_id,
            assignment_id=assignment_id,
            expected_revision=expected_revision,
        )
        account = get_service_account_response(db, service_account_id)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.role.remove",
            resource_type="service_account",
            resource_id=str(service_account_id),
            metadata={
                "assignment_id": str(assignment.id),
                "role_id": str(assignment.role_id),
                "service_account_revision": account.revision,
            },
        )
        db.commit()
        _set_revision_header(response, account.revision)
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.role.remove",
            exc=exc,
            resource_id=str(service_account_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "remove_role", exc)


@router.get(
    "/{service_account_id}/credentials",
    response_model=ServiceAccountCredentialListResponse,
    responses=NOT_FOUND_RESPONSE,
)
def get_credentials(
    service_account_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_SERVICE_ACCOUNTS)),
):
    try:
        items, total = list_credentials(
            db,
            service_account_id,
            page=page,
            page_size=page_size,
        )
        return ServiceAccountCredentialListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    except ServiceAccountError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "list_credentials", exc)


@router.post(
    "/{service_account_id}/credentials",
    response_model=ServiceAccountCredentialIssueResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_201_CREATED: {"headers": ONE_TIME_SECRET_RESPONSE_HEADERS},
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid credential scope or idempotency key"
        },
    },
)
def post_credential(
    service_account_id: uuid.UUID,
    payload: ServiceAccountCredentialIssueRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=200),
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        issued = issue_credential(
            db,
            service_account_id=service_account_id,
            payload=payload,
            actor_user_id=actor.id,
            actor_authorization=_delegation_context(request),
            idempotency_key=idempotency_key,
        )
        public_credential = credential_response(issued.credential)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.credentials.create",
            resource_type="service_account_credential",
            resource_id=str(issued.credential.id),
            metadata={
                "service_account_id": str(service_account_id),
                "credential": public_credential.model_dump(mode="json"),
                "service_account_revision": issued.account.revision,
            },
        )
        db.commit()
        _set_one_time_secret_headers(response, revision=issued.account.revision)
        return ServiceAccountCredentialIssueResponse(
            token=issued.token, credential=public_credential
        )
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.credentials.create",
            exc=exc,
            resource_id=str(service_account_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "issue_credential", exc)


@router.post(
    "/{service_account_id}/credentials/{credential_id}/revoke",
    response_model=ServiceAccountCredentialResponse,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_200_OK: {"headers": IDEMPOTENT_MUTATION_RESPONSE_HEADERS},
    },
)
def post_revoke_credential(
    service_account_id: uuid.UUID,
    credential_id: uuid.UUID,
    payload: ServiceAccountRevisionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        credential, mutation = revoke_credential(
            db,
            service_account_id=service_account_id,
            credential_id=credential_id,
            expected_revision=payload.expected_revision,
            actor_user_id=actor.id,
        )
        result = credential_response(credential)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.credentials.revoke",
            resource_type="service_account_credential",
            resource_id=str(credential.id),
            metadata={
                "service_account_id": str(service_account_id),
                "token_prefix": credential.token_prefix,
                "changed": mutation.changed,
                "service_account_revision": mutation.account.revision,
            },
        )
        db.commit()
        _set_mutation_headers(response, mutation)
        return result
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.credentials.revoke",
            exc=exc,
            resource_id=str(credential_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "revoke_credential", exc)


@router.post(
    "/{service_account_id}/credentials/{credential_id}/rotate",
    response_model=ServiceAccountCredentialRotateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **MUTATION_RESPONSES,
        status.HTTP_201_CREATED: {"headers": ONE_TIME_SECRET_RESPONSE_HEADERS},
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid credential scope or idempotency key"
        },
    },
)
def post_rotate_credential(
    service_account_id: uuid.UUID,
    credential_id: uuid.UUID,
    payload: ServiceAccountCredentialIssueRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=200),
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        issued = rotate_credential(
            db,
            service_account_id=service_account_id,
            credential_id=credential_id,
            payload=payload,
            actor_user_id=actor.id,
            actor_authorization=_delegation_context(request),
            idempotency_key=idempotency_key,
        )
        public_credential = credential_response(issued.credential)
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.credentials.rotate",
            resource_type="service_account_credential",
            resource_id=str(issued.credential.id),
            metadata={
                "service_account_id": str(service_account_id),
                "rotated_from_credential_id": str(credential_id),
                "previous_credential_revoked": False,
                "previous_credential_expires_at": (
                    issued.previous_credential_expires_at.isoformat()
                    if issued.previous_credential_expires_at
                    else None
                ),
                "credential": public_credential.model_dump(mode="json"),
                "service_account_revision": issued.account.revision,
            },
        )
        db.commit()
        _set_one_time_secret_headers(response, revision=issued.account.revision)
        return ServiceAccountCredentialRotateResponse(
            token=issued.token,
            credential=public_credential,
            previous_credential_id=credential_id,
            previous_credential_revoked=False,
            previous_credential_expires_at=issued.previous_credential_expires_at,
        )
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.credentials.rotate",
            exc=exc,
            resource_id=str(credential_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "rotate_credential", exc)


def _set_one_time_secret_headers(response: Response, *, revision: int) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _set_revision_header(response, revision)


def _set_revision_header(response: Response, revision: int) -> None:
    response.headers["X-Current-Revision"] = str(revision)


def _set_mutation_headers(response: Response, mutation) -> None:
    _set_revision_header(response, mutation.account.revision)
    response.headers["X-ThreatLens-Mutation-Changed"] = str(mutation.changed).lower()


@router.delete(
    "/{service_account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=MUTATION_RESPONSES,
)
def delete_disabled_service_account(
    service_account_id: uuid.UUID,
    request: Request,
    expected_revision: int = Query(ge=1),
    db: Session = Depends(get_db),
    actor: User = Depends(require_service_account_mutation_actor),
):
    try:
        before = get_service_account_response(db, service_account_id)
        delete_service_account(
            db,
            service_account_id=service_account_id,
            expected_revision=expected_revision,
        )
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action="service_accounts.delete",
            resource_type="service_account",
            resource_id=str(service_account_id),
            metadata={"before": before.model_dump(mode="json")},
        )
        db.commit()
    except ServiceAccountError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=actor,
            action="service_accounts.delete",
            exc=exc,
            resource_id=str(service_account_id),
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        _raise_storage_error(db, "delete", exc)


def _delegation_context(request: Request) -> AuthorizationContext:
    authorization = get_authorization_context(request)
    if authorization is None:
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Effective service-account delegation access could not be resolved. "
                "Retry the request."
            ),
            error_code="iam_policy_unavailable",
        )
    return authorization


def _raise_storage_error(
    db: Session,
    operation: str,
    exc: SQLAlchemyError,
) -> None:
    db.rollback()
    logger.exception(
        "service_account_storage_failed operation=%s error_type=%s",
        operation,
        type(exc).__name__,
    )
    raise ApiHTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Service-account state could not be stored safely. No partial change "
            "was committed. Retry the request; if the error persists, inspect the "
            "server logs and database health."
        ),
        error_code="service_account_storage_unavailable",
    ) from exc


def _record_actor_access_rejection(
    db: Session,
    *,
    request: Request,
    actor: User,
    actor_exists: bool,
) -> None:
    db.rollback()
    try:
        credential_id = getattr(request.state, "api_token_id", None)
        if credential_id is None:
            credential_id = get_current_auth_session_id(request)
        record_audit(
            db,
            actor_user_id=actor.id if actor_exists else None,
            actor_principal_type="user",
            actor_principal_id=actor.id,
            credential_kind=get_auth_credential_kind(request),
            credential_id=credential_id,
            request_id=getattr(request.state, "request_id", None),
            source_ip=resolve_client_ip(request),
            action="service_accounts.authorization.reject",
            resource_type="service_account_policy",
            success=False,
            metadata={
                "reason": "actor_missing_or_ineligible",
                "actor_exists": actor_exists,
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "service_account_actor_rejection_audit_failed actor_id=%s error_type=%s",
            actor.id,
            type(exc).__name__,
            exc_info=True,
        )


def _record_request_audit(
    db: Session,
    *,
    request: Request,
    actor: User,
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
        actor_user_id=actor.id,
        actor_principal_type="user",
        actor_principal_id=actor.id,
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
    actor: User,
    action: str,
    exc: ServiceAccountError,
    resource_id: str | None = None,
) -> None:
    db.rollback()
    if ".credentials." in action:
        resource_type = "service_account_credential"
    elif ".role." in action:
        resource_type = "service_account_role_assignment"
    else:
        resource_type = "service_account"
    try:
        _record_request_audit(
            db,
            request=request,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=False,
            metadata={
                "reason": exc.code,
                "current_revision": getattr(exc, "current_revision", None),
                "scopes": list(getattr(exc, "scopes", ())),
                "missing_permissions": list(getattr(exc, "missing_permissions", ())),
                "blocked_permissions": list(getattr(exc, "blocked_permissions", ())),
                "credential_id": (
                    str(exc.credential_id)
                    if getattr(exc, "credential_id", None) is not None
                    else None
                ),
            },
        )
        db.commit()
    except Exception as audit_exc:
        db.rollback()
        logger.error(
            "service_account_rejection_audit_failed action=%s actor_id=%s "
            "error_type=%s",
            action,
            actor.id,
            type(audit_exc).__name__,
            exc_info=True,
        )


def _http_error(exc: ServiceAccountError) -> ApiHTTPException:
    if isinstance(
        exc,
        (
            ServiceAccountNotFound,
            ServiceAccountRoleNotFound,
            ServiceAccountRoleAssignmentNotFound,
            ServiceAccountCredentialNotFound,
        ),
    ):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, ServiceAccountScopeEscalation):
        status_code = status.HTTP_403_FORBIDDEN
    elif getattr(exc, "code", None) == "service_account_delegation_denied":
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, ServiceAccountScopeNotAllowed):
        status_code = status.HTTP_400_BAD_REQUEST
    elif getattr(exc, "code", None) == "service_account_idempotency_key_invalid":
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, ServiceAccountCredentialGenerationFailed):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_409_CONFLICT
    context: dict[str, object] = {}
    current_revision = getattr(exc, "current_revision", None)
    if current_revision is not None:
        context["current_revision"] = current_revision
    scopes = getattr(exc, "scopes", None)
    if scopes is not None:
        context["scopes"] = scopes
    missing_permissions = getattr(exc, "missing_permissions", None)
    if missing_permissions is not None:
        context["missing_permissions"] = list(missing_permissions)
    blocked_permissions = getattr(exc, "blocked_permissions", None)
    if blocked_permissions is not None:
        context["blocked_permissions"] = list(blocked_permissions)
    credential_id = getattr(exc, "credential_id", None)
    if credential_id is not None:
        context["credential_id"] = str(credential_id)
    return ApiHTTPException(
        status_code=status_code,
        detail=str(exc),
        error_code=exc.code,
        error_context=context or None,
    )
