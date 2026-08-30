from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access_responses import effective_access_response
from app.api.deps import (
    get_auth_credential_kind,
    get_authorization_context,
    get_current_auth_session_id,
    get_current_user,
    require_permissions,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_IAM, SCOPE_WRITE_IAM
from app.db.session import get_db
from app.models.user import User
from app.schemas.iam import (
    AccessExplanationResponse,
    EffectiveAccessResponse,
    GroupMemberRequest,
    GroupMemberResponse,
    GroupResponse,
    GroupRoleAssignmentResponse,
    GroupRoleRequest,
    GroupUpdateRequest,
    GroupWriteRequest,
    PermissionResponse,
    RoleResponse,
    RoleUpdateRequest,
    RoleWriteRequest,
    UserRoleAssignmentRequest,
    UserRoleAssignmentResponse,
    permission_responses,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationStateUnavailable,
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)
from app.services.iam_groups import (
    IAMGroupConflict,
    IAMGroupError,
    IAMGroupNotFound,
    IAMGroupRoleNotFound,
    IAMGroupRoleRevisionConflict,
    IAMGroupRevisionConflict,
    IAMGroupUserNotFound,
    IAMSystemGroupImmutable,
    add_group_member,
    add_group_role,
    create_group,
    delete_group,
    get_group_response,
    list_group_members,
    list_group_role_assignments,
    list_groups,
    remove_group_member,
    remove_group_role,
    update_group,
)
from app.services.iam_roles import (
    IAMRoleConflict,
    IAMRoleError,
    IAMRoleNotFound,
    IAMRoleRevisionConflict,
    IAMSystemRoleImmutable,
    IAMUserNotFound,
    assign_role_to_user,
    create_role,
    delete_role,
    get_role_response,
    list_roles,
    list_user_role_assignments,
    remove_role_from_user,
    update_role,
)


router = APIRouter(prefix="/iam", tags=["iam"])


def require_iam_mutation_actor(
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_IAM)),
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
            db.rollback()
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account access changed while the IAM operation was being authorized. Sign in again and retry.",
                error_code="iam_actor_access_changed",
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
        if not refreshed_context.has(SCOPE_WRITE_IAM):
            _record_request_audit(
                db,
                request=request,
                actor=locked_actor,
                action="iam.authorization.reject",
                resource_type="iam_policy",
                resource_id=None,
                success=False,
                metadata={"reason": "permission_changed_during_request"},
            )
            db.commit()
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your IAM permission changed while this request was being authorized. Reload and retry.",
                error_code="iam_actor_access_changed",
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


@router.get("/permissions", response_model=list[PermissionResponse])
def get_permissions(
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    return permission_responses()


@router.get("/effective", response_model=EffectiveAccessResponse)
def get_my_effective_access(
    request: Request,
    user: User = Depends(get_current_user),
):
    context = get_authorization_context(request)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Effective access could not be resolved. Retry the request.",
        )
    return effective_access_response(context)


@router.get("/effective/explain", response_model=AccessExplanationResponse)
def explain_my_access(
    request: Request,
    permission: str = Query(min_length=3, max_length=96),
    _user: User = Depends(get_current_user),
):
    context = get_authorization_context(request)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Effective access could not be resolved. Retry the request.",
        )
    return AccessExplanationResponse.model_validate(context.explanation(permission))


@router.get("/users/{user_id}/effective", response_model=EffectiveAccessResponse)
def get_user_effective_access(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return effective_access_response(authorization_context_for_user(db, user))


@router.get("/roles", response_model=list[RoleResponse])
def get_roles(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    return list_roles(db)


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role(
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    try:
        return get_role_response(db, role_id)
    except IAMRoleNotFound as exc:
        raise _http_error(exc) from exc


@router.post("/roles", response_model=RoleResponse, status_code=201)
def post_role(
    payload: RoleWriteRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        role = create_role(db, payload=payload, actor_user_id=admin.id)
        after = get_role_response(db, role.id).model_dump(mode="json")
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.roles.create",
            resource_type="iam_role",
            resource_id=str(role.id),
            metadata={"after": after},
        )
        db.commit()
        return get_role_response(db, role.id)
    except IAMRoleError as exc:
        _record_rejected_mutation(
            db, request=request, actor=admin, action="iam.roles.create", exc=exc
        )
        raise _http_error(exc) from exc


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def patch_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        before = get_role_response(db, role_id).model_dump(mode="json")
        update_role(db, role_id=role_id, payload=payload)
        after = get_role_response(db, role_id).model_dump(mode="json")
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.roles.update",
            resource_type="iam_role",
            resource_id=str(role_id),
            metadata={"before": before, "after": after},
        )
        db.commit()
        return get_role_response(db, role_id)
    except IAMRoleError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.roles.update",
            exc=exc,
            resource_id=str(role_id),
        )
        raise _http_error(exc) from exc


@router.delete("/roles/{role_id}", status_code=204)
def remove_role(
    role_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        before = get_role_response(db, role_id).model_dump(mode="json")
        delete_role(db, role_id=role_id)
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.roles.delete",
            resource_type="iam_role",
            resource_id=str(role_id),
            metadata={"before": before},
        )
        db.commit()
    except IAMRoleError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.roles.delete",
            exc=exc,
            resource_id=str(role_id),
        )
        raise _http_error(exc) from exc


@router.get(
    "/users/{user_id}/role-assignments",
    response_model=list[UserRoleAssignmentResponse],
)
def get_user_roles(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return list_user_role_assignments(db, user_id)


@router.post(
    "/users/{user_id}/role-assignments",
    response_model=UserRoleAssignmentResponse,
    status_code=201,
)
def post_user_role(
    user_id: uuid.UUID,
    payload: UserRoleAssignmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        result = assign_role_to_user(
            db,
            user_id=user_id,
            role_id=payload.role_id,
            actor_user_id=admin.id,
            expected_role_revision=payload.expected_role_revision,
        )
        response = next(
            item
            for item in list_user_role_assignments(db, user_id)
            if item.id == result.assignment.id
        )
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.user_role.assign",
            resource_type="user",
            resource_id=str(user_id),
            metadata={
                "assignment": response.model_dump(mode="json"),
                "created": result.created,
            },
        )
        db.commit()
        return response
    except IAMRoleError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.user_role.assign",
            exc=exc,
            resource_id=str(user_id),
        )
        raise _http_error(exc) from exc


@router.delete("/users/{user_id}/role-assignments/{assignment_id}", status_code=204)
def delete_user_role(
    user_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        assignment = remove_role_from_user(
            db, user_id=user_id, assignment_id=assignment_id
        )
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.user_role.remove",
            resource_type="user",
            resource_id=str(user_id),
            metadata={
                "assignment_id": str(assignment.id),
                "role_id": str(assignment.role_id),
            },
        )
        db.commit()
    except IAMRoleError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.user_role.remove",
            exc=exc,
            resource_id=str(user_id),
        )
        raise _http_error(exc) from exc


@router.get("/groups", response_model=list[GroupResponse])
def get_groups(
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    return list_groups(db)


@router.post("/groups", response_model=GroupResponse, status_code=201)
def post_group(
    payload: GroupWriteRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        group = create_group(db, payload=payload, actor_user_id=admin.id)
        response = get_group_response(db, group.id)
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.groups.create",
            resource_type="iam_group",
            resource_id=str(group.id),
            metadata={"after": response.model_dump(mode="json")},
        )
        db.commit()
        return response
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db, request=request, actor=admin, action="iam.groups.create", exc=exc
        )
        raise _http_error(exc) from exc


@router.patch("/groups/{group_id}", response_model=GroupResponse)
def patch_group(
    group_id: uuid.UUID,
    payload: GroupUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        before = get_group_response(db, group_id).model_dump(mode="json")
        update_group(db, group_id=group_id, payload=payload)
        after = get_group_response(db, group_id).model_dump(mode="json")
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.groups.update",
            resource_type="iam_group",
            resource_id=str(group_id),
            metadata={"before": before, "after": after},
        )
        db.commit()
        return get_group_response(db, group_id)
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.groups.update",
            exc=exc,
            resource_id=str(group_id),
        )
        raise _http_error(exc) from exc


@router.delete("/groups/{group_id}", status_code=204)
def remove_group(
    group_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        before = get_group_response(db, group_id).model_dump(mode="json")
        delete_group(db, group_id=group_id)
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.groups.delete",
            resource_type="iam_group",
            resource_id=str(group_id),
            metadata={"before": before},
        )
        db.commit()
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.groups.delete",
            exc=exc,
            resource_id=str(group_id),
        )
        raise _http_error(exc) from exc


@router.get("/groups/{group_id}/members", response_model=list[GroupMemberResponse])
def get_group_members(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    try:
        return list_group_members(db, group_id)
    except IAMGroupError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/groups/{group_id}/members", response_model=GroupMemberResponse, status_code=201
)
def post_group_member(
    group_id: uuid.UUID,
    payload: GroupMemberRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        result = add_group_member(
            db,
            group_id=group_id,
            user_id=payload.user_id,
            actor_user_id=admin.id,
        )
        response = next(
            member
            for member in list_group_members(db, group_id)
            if member.id == result.membership.id
        )
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.group_member.add",
            resource_type="iam_group",
            resource_id=str(group_id),
            metadata={
                "membership": response.model_dump(mode="json"),
                "created": result.created,
            },
        )
        db.commit()
        return response
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.group_member.add",
            exc=exc,
            resource_id=str(group_id),
        )
        raise _http_error(exc) from exc


@router.delete("/groups/{group_id}/members/{membership_id}", status_code=204)
def delete_group_member(
    group_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        membership = remove_group_member(
            db, group_id=group_id, membership_id=membership_id
        )
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.group_member.remove",
            resource_type="iam_group",
            resource_id=str(group_id),
            metadata={
                "membership_id": str(membership.id),
                "user_id": str(membership.user_id),
            },
        )
        db.commit()
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.group_member.remove",
            exc=exc,
            resource_id=str(group_id),
        )
        raise _http_error(exc) from exc


@router.post(
    "/groups/{group_id}/role-assignments", response_model=GroupResponse, status_code=201
)
def post_group_role(
    group_id: uuid.UUID,
    payload: GroupRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        result = add_group_role(
            db,
            group_id=group_id,
            role_id=payload.role_id,
            actor_user_id=admin.id,
            expected_role_revision=payload.expected_role_revision,
        )
        response = get_group_response(db, group_id)
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.group_role.assign",
            resource_type="iam_group",
            resource_id=str(group_id),
            metadata={
                "assignment_id": str(result.assignment.id),
                "role_id": str(payload.role_id),
                "created": result.created,
            },
        )
        db.commit()
        return response
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.group_role.assign",
            exc=exc,
            resource_id=str(group_id),
        )
        raise _http_error(exc) from exc


@router.get(
    "/groups/{group_id}/role-assignments",
    response_model=list[GroupRoleAssignmentResponse],
)
def get_group_roles(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    try:
        return list_group_role_assignments(db, group_id)
    except IAMGroupError as exc:
        raise _http_error(exc) from exc


@router.delete("/groups/{group_id}/role-assignments/{assignment_id}", status_code=204)
def delete_group_role(
    group_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_iam_mutation_actor),
):
    try:
        assignment = remove_group_role(
            db, group_id=group_id, assignment_id=assignment_id
        )
        _record_request_audit(
            db,
            request=request,
            actor=admin,
            action="iam.group_role.remove",
            resource_type="iam_group",
            resource_id=str(group_id),
            metadata={
                "assignment_id": str(assignment.id),
                "role_id": str(assignment.role_id),
            },
        )
        db.commit()
    except IAMGroupError as exc:
        _record_rejected_mutation(
            db,
            request=request,
            actor=admin,
            action="iam.group_role.remove",
            exc=exc,
            resource_id=str(group_id),
        )
        raise _http_error(exc) from exc


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
    credential_kind = get_auth_credential_kind(request)
    credential_id = getattr(request.state, "api_token_id", None)
    if credential_id is None:
        credential_id = get_current_auth_session_id(request)
    record_audit(
        db,
        actor_user_id=actor.id,
        actor_principal_type="user",
        actor_principal_id=actor.id,
        credential_kind=credential_kind,
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
    exc: IAMRoleError | IAMGroupError,
    resource_id: str | None = None,
) -> None:
    db.rollback()
    _record_request_audit(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type="iam_policy",
        resource_id=resource_id,
        success=False,
        metadata={"reason": exc.code},
    )
    db.commit()


def _http_error(exc: IAMRoleError | IAMGroupError) -> ApiHTTPException:
    not_found = isinstance(
        exc,
        (
            IAMRoleNotFound,
            IAMUserNotFound,
            IAMGroupNotFound,
            IAMGroupUserNotFound,
            IAMGroupRoleNotFound,
        ),
    )
    conflict = isinstance(
        exc,
        (
            IAMRoleConflict,
            IAMRoleRevisionConflict,
            IAMSystemRoleImmutable,
            IAMGroupConflict,
            IAMGroupRevisionConflict,
            IAMGroupRoleRevisionConflict,
            IAMSystemGroupImmutable,
        ),
    )
    status_code = 404 if not_found else 409 if conflict else 400
    context: dict[str, object] = {}
    current_revision = getattr(exc, "current_revision", None)
    if current_revision is not None:
        context["current_revision"] = current_revision
    return ApiHTTPException(
        status_code=status_code,
        detail=str(exc),
        error_code=exc.code,
        error_context=context or None,
    )
