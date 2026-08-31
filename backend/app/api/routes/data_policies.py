from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthenticatedPrincipal,
    get_auth_credential_kind,
    get_current_auth_session_id,
    require_permissions,
    resolve_client_ip,
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
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import (
    SCOPE_READ_DATA_POLICIES,
    SCOPE_WRITE_DATA_POLICIES,
)
from app.db.session import get_db
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.user import User
from app.schemas.data_policy import (
    DataPolicyModeUpdateRequest,
    DataPolicyModeUpdateResponse,
    DataPolicyOverviewResponse,
    DataPolicyPreflightResponse,
    FeedHandlingLabelAssignmentRequest,
    FeedHandlingLabelAssignmentResponse,
    HandlingLabelCreateRequest,
    HandlingLabelMutationResponse,
    HandlingLabelRoleGrantsRequest,
    HandlingLabelStatusRequest,
    HandlingLabelUpdateRequest,
)
from app.services.data_access_policy import (
    DataPolicyError,
    assign_feed_handling_label,
    create_handling_label,
    current_data_policy_revision,
    data_policy_overview,
    data_policy_preflight,
    get_handling_label,
    replace_handling_label_role_grants,
    set_handling_label_status,
    update_data_policy_mode,
    update_handling_label,
)
from app.services.audit import record_audit
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


logger = logging.getLogger("threatlens.data_policy")
router = APIRouter(prefix="/iam/data-policies", tags=["data policies"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key")]
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)
_DATA_POLICY_RESOURCE_ID = uuid.UUID("00000000-0000-4000-8000-000000000202")
_BROWSER_ONLY = {"x-threatlens-browser-session-only": True}
_MUTATION_RESPONSES = {
    status.HTTP_400_BAD_REQUEST: {"description": "Invalid idempotency key"},
    status.HTTP_403_FORBIDDEN: {
        "description": "Human browser session or durable authority required"
    },
    status.HTTP_404_NOT_FOUND: {"description": "Label or feed not found"},
    status.HTTP_409_CONFLICT: {
        "description": "Revision, idempotency, invariant, or activation conflict"
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "IAM or data-policy storage unavailable"
    },
}
_write_data_policy_permission = require_permissions(SCOPE_WRITE_DATA_POLICIES)


def require_data_policy_writer(
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(_write_data_policy_permission),
) -> User:
    if isinstance(principal, User):
        return principal
    try:
        credential_id = getattr(request.state, "service_account_credential_id", None)
        if credential_id is None:
            credential_id = (
                getattr(request.state, "api_token_id", None)
                or get_current_auth_session_id(request)
            )
        record_audit(
            db,
            actor_user_id=None,
            actor_principal_type="service_account",
            actor_principal_id=principal.id,
            credential_kind=get_auth_credential_kind(request),
            credential_id=credential_id,
            request_id=getattr(request.state, "request_id", None),
            source_ip=resolve_client_ip(request),
            action="data_policy.mutation.authorize",
            resource_type="data_policy",
            resource_id=str(_DATA_POLICY_RESOURCE_ID),
            success=False,
            metadata={"reason": "human_principal_required"},
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.error(
            "data_policy_human_boundary_audit_failed principal_id=%s",
            principal.id,
            exc_info=True,
        )
    raise ApiHTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Data-policy mutations require a human browser session. "
            "Service-account credentials cannot change access policy."
        ),
        error_code="human_principal_required",
    )


require_data_policy_writer._threatlens_required_scopes = (SCOPE_WRITE_DATA_POLICIES,)


@router.get("", response_model=DataPolicyOverviewResponse)
def get_data_policy_overview(
    db: Session = Depends(get_db),
    _reader: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_READ_DATA_POLICIES)
    ),
):
    with _read_errors(db):
        return data_policy_overview(db)


@router.get("/preflight", response_model=DataPolicyPreflightResponse)
def get_data_policy_preflight(
    db: Session = Depends(get_db),
    _reader: AuthenticatedPrincipal = Depends(
        require_permissions(SCOPE_READ_DATA_POLICIES)
    ),
):
    with _read_errors(db):
        return data_policy_preflight(db)


@router.post(
    "/labels",
    response_model=HandlingLabelMutationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_MUTATION_RESPONSES,
    openapi_extra=_BROWSER_ONLY,
)
def post_handling_label(
    payload: HandlingLabelCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_policy_writer),
):
    action = "data_policy.label.create"
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload=payload.model_dump(mode="json"),
        )
        locked_actor, replay = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label="creating a handling label",
            resource_type="handling_label",
            resource_id=None,
        )
        if replay is not None:
            return _replay(db, response, replay, HandlingLabelMutationResponse)
        rendered = create_handling_label(
            db,
            payload=payload,
            actor_user_id=locked_actor.id,
        )
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="handling_label",
            resource_id=rendered.label.id,
            rendered=rendered,
            http_status=status.HTTP_201_CREATED,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="handling_label",
            resource_id=str(rendered.label.id),
            metadata={
                "label_key": rendered.label.key,
                "role_ids": [str(value) for value in rendered.label.role_ids],
                "label_revision": rendered.label.revision,
                "policy_revision": rendered.policy_revision,
                "changed": rendered.changed,
            },
        )
        commit_governance_mutation(db, action=action)
        _set_headers(response, rendered.policy_revision, changed=rendered.changed)
        return rendered
    except (
        DataPolicyError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, "handling_label", None, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="data_policy", operation="create_label", exc=exc
        )


@router.patch(
    "/labels/{label_id}",
    response_model=HandlingLabelMutationResponse,
    responses=_MUTATION_RESPONSES,
    openapi_extra=_BROWSER_ONLY,
)
def patch_handling_label(
    label_id: uuid.UUID,
    payload: HandlingLabelUpdateRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_policy_writer),
):
    return _mutate_label(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        label_id=label_id,
        payload=payload,
        action="data_policy.label.update",
        operation_label="updating a handling label",
        operation="update_label",
    )


@router.put(
    "/labels/{label_id}/role-grants",
    response_model=HandlingLabelMutationResponse,
    responses=_MUTATION_RESPONSES,
    openapi_extra=_BROWSER_ONLY,
)
def put_handling_label_role_grants(
    label_id: uuid.UUID,
    payload: HandlingLabelRoleGrantsRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_policy_writer),
):
    return _mutate_label(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        label_id=label_id,
        payload=payload,
        action="data_policy.label.role_grants.replace",
        operation_label="changing handling-label role grants",
        operation="replace_role_grants",
    )


@router.put(
    "/labels/{label_id}/status",
    response_model=HandlingLabelMutationResponse,
    responses=_MUTATION_RESPONSES,
    openapi_extra=_BROWSER_ONLY,
)
def put_handling_label_status(
    label_id: uuid.UUID,
    payload: HandlingLabelStatusRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_policy_writer),
):
    return _mutate_label(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        label_id=label_id,
        payload=payload,
        action=(
            "data_policy.label.restore"
            if payload.active
            else "data_policy.label.archive"
        ),
        operation_label=(
            "restoring a handling label"
            if payload.active
            else "archiving a handling label"
        ),
        operation="set_label_status",
    )


@router.put(
    "/feeds/{feed_id}",
    response_model=FeedHandlingLabelAssignmentResponse,
    responses=_MUTATION_RESPONSES,
    openapi_extra=_BROWSER_ONLY,
)
def put_feed_handling_label(
    feed_id: uuid.UUID,
    payload: FeedHandlingLabelAssignmentRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_policy_writer),
):
    action = "data_policy.feed.assign"
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload={"feed_id": str(feed_id), **payload.model_dump(mode="json")},
        )
        locked_actor, replay = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label="assigning a feed handling label",
            resource_type="feed",
            resource_id=str(feed_id),
        )
        if replay is not None:
            return _replay(
                db,
                response,
                replay,
                FeedHandlingLabelAssignmentResponse,
            )
        rendered = assign_feed_handling_label(
            db,
            feed_id=feed_id,
            handling_label_id=payload.handling_label_id,
            expected_policy_revision=payload.expected_policy_revision,
            actor_user_id=locked_actor.id,
        )
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="feed",
            resource_id=feed_id,
            rendered=rendered,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="feed",
            resource_id=str(feed_id),
            metadata={
                "previous_handling_label_id": str(rendered.previous_handling_label_id),
                "handling_label_id": str(rendered.handling_label_id),
                "policy_revision": rendered.policy_revision,
                "changed": rendered.changed,
            },
        )
        commit_governance_mutation(db, action=action)
        _set_headers(response, rendered.policy_revision, changed=rendered.changed)
        return rendered
    except (
        DataPolicyError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, "feed", str(feed_id), exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="data_policy", operation="assign_feed_label", exc=exc
        )


@router.put(
    "/mode",
    response_model=DataPolicyModeUpdateResponse,
    responses=_MUTATION_RESPONSES,
    openapi_extra=_BROWSER_ONLY,
)
def put_data_policy_mode(
    payload: DataPolicyModeUpdateRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_policy_writer),
):
    action = "data_policy.mode.update"
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload=payload.model_dump(mode="json"),
        )
        locked_actor, replay = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label="changing data-policy enforcement mode",
            resource_type="data_policy",
            resource_id=str(_DATA_POLICY_RESOURCE_ID),
        )
        if replay is not None:
            return _replay(db, response, replay, DataPolicyModeUpdateResponse)
        previous = data_policy_overview(db).state
        rendered = update_data_policy_mode(
            db,
            mode=payload.mode,
            expected_revision=payload.expected_revision,
            actor_user_id=locked_actor.id,
        )
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="data_policy",
            resource_id=_DATA_POLICY_RESOURCE_ID,
            rendered=rendered,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="data_policy",
            resource_id=str(_DATA_POLICY_RESOURCE_ID),
            metadata={
                "previous_mode": previous.mode,
                "mode": rendered.state.mode,
                "reason": payload.reason,
                "policy_revision": rendered.state.revision,
                "changed": rendered.changed,
            },
        )
        commit_governance_mutation(db, action=action)
        _set_headers(response, rendered.state.revision, changed=rendered.changed)
        return rendered
    except (
        DataPolicyError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(
            db,
            request,
            actor,
            action,
            "data_policy",
            str(_DATA_POLICY_RESOURCE_ID),
            exc,
        )
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="data_policy", operation="update_mode", exc=exc
        )


def _mutate_label(
    db: Session,
    *,
    request: Request,
    response: Response,
    actor: User,
    idempotency_key: str,
    label_id: uuid.UUID,
    payload: HandlingLabelUpdateRequest
    | HandlingLabelRoleGrantsRequest
    | HandlingLabelStatusRequest,
    action: str,
    operation_label: str,
    operation: str,
) -> HandlingLabelMutationResponse:
    try:
        identity = _identity(
            idempotency_key,
            operation=action,
            payload={"label_id": str(label_id), **payload.model_dump(mode="json")},
        )
        locked_actor, replay = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label=operation_label,
            resource_type="handling_label",
            resource_id=str(label_id),
        )
        if replay is not None:
            return _replay(db, response, replay, HandlingLabelMutationResponse)
        before = get_handling_label(db, label_id)
        if operation == "update_label":
            assert isinstance(payload, HandlingLabelUpdateRequest)
            rendered = update_handling_label(
                db,
                label_id=label_id,
                payload=payload,
                actor_user_id=locked_actor.id,
            )
            mutation_metadata: dict[str, object] = {
                "previous_label_revision": before.revision,
                "updated_fields": sorted(
                    payload.model_dump(
                        exclude_unset=True, exclude={"expected_revision"}
                    )
                ),
            }
        elif operation == "replace_role_grants":
            assert isinstance(payload, HandlingLabelRoleGrantsRequest)
            rendered = replace_handling_label_role_grants(
                db,
                label_id=label_id,
                payload=payload,
                actor_user_id=locked_actor.id,
            )
            mutation_metadata = {
                "role_ids": [str(value) for value in rendered.label.role_ids],
                "added_role_ids": [
                    str(value)
                    for value in sorted(
                        set(rendered.label.role_ids) - set(before.role_ids), key=str
                    )
                ],
                "removed_role_ids": [
                    str(value)
                    for value in sorted(
                        set(before.role_ids) - set(rendered.label.role_ids), key=str
                    )
                ],
            }
        else:
            assert isinstance(payload, HandlingLabelStatusRequest)
            rendered = set_handling_label_status(
                db,
                label_id=label_id,
                payload=payload,
                actor_user_id=locked_actor.id,
            )
            mutation_metadata = {
                "previous_active": before.is_active,
                "active": rendered.label.is_active,
            }
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            resource_type="handling_label",
            resource_id=label_id,
            rendered=rendered,
        )
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="handling_label",
            resource_id=str(label_id),
            metadata={
                "label_key": rendered.label.key,
                "label_revision": rendered.label.revision,
                "policy_revision": rendered.policy_revision,
                "changed": rendered.changed,
                **mutation_metadata,
            },
        )
        commit_governance_mutation(db, action=action)
        _set_headers(response, rendered.policy_revision, changed=rendered.changed)
        return rendered
    except (
        DataPolicyError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, "handling_label", str(label_id), exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="data_policy", operation=operation, exc=exc
        )


def _prepare_mutation(
    db: Session,
    *,
    request: Request,
    actor: User,
    identity: GovernanceOperationIdentity,
    action: str,
    operation_label: str,
    resource_type: str,
    resource_id: str | None,
) -> tuple[User, GovernanceOperationReceipt | None]:
    lock_governance_operation_identity(db, actor_user_id=actor.id, identity=identity)
    locked_actor, _authorization = authorize_governance_actor(
        db,
        request=request,
        actor=actor,
        required_permission=SCOPE_WRITE_DATA_POLICIES,
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
    replay = find_governance_operation_replay(
        db,
        actor_user_id=locked_actor.id,
        identity=identity,
    )
    return locked_actor, replay


def _identity(
    idempotency_key: str, *, operation: str, payload: dict[str, object]
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
    http_status: int = status.HTTP_200_OK,
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


def _replay(
    db: Session,
    response: Response,
    receipt: GovernanceOperationReceipt,
    response_model: type[_ResponseModel],
) -> _ResponseModel:
    rendered = response_model.model_validate(
        governance_operation_replay_payload(receipt)
    )
    revision = current_data_policy_revision(db)
    _set_headers(response, revision, changed=False)
    return rendered


@contextmanager
def _read_errors(db: Session) -> Iterator[None]:
    try:
        yield
    except DataPolicyError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("data_policy_read_failed", exc_info=True)
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data policy storage is temporarily unavailable. Retry the request.",
            error_code="data_policy_storage_unavailable",
        ) from exc


def _reject(
    db: Session,
    request: Request,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | None,
    exc: Exception,
) -> None:
    context = dict(getattr(exc, "context", {}) or {})
    if isinstance(exc, GovernanceAuthorizationDenied):
        context["reason"] = exc.reason
        if exc.required_permission is not None:
            context["required_permission"] = exc.required_permission
    record_rejected_governance_mutation(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=str(getattr(exc, "code", "data_policy_error")),
        context=context,
    )


def _http_error(exc: Exception) -> ApiHTTPException:
    if isinstance(exc, GovernanceAuthorizationDenied):
        return governance_authorization_http_error(exc)
    if isinstance(exc, GovernanceIdempotencyKeyInvalid):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, GovernanceIdempotencyError):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = int(getattr(exc, "status_code", 503))
    context = dict(getattr(exc, "context", {}) or {})
    current_revision = getattr(exc, "current_revision", None)
    if current_revision is not None:
        context["current_revision"] = current_revision
    headers = {"X-ThreatLens-Mutation-Changed": "false"}
    if current_revision is not None:
        headers["X-Current-Revision"] = str(current_revision)
    return ApiHTTPException(
        status_code=status_code,
        detail=str(exc),
        error_code=str(getattr(exc, "code", "data_policy_error")),
        error_context=context or None,
        headers=headers,
    )


def _set_headers(response: Response, revision: int | None, *, changed: bool) -> None:
    if revision is not None:
        response.headers["X-Current-Revision"] = str(revision)
    response.headers["X-ThreatLens-Mutation-Changed"] = str(changed).lower()


__all__ = ["router"]
