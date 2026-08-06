import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.core.rbac import ALL_ROLES, ROLE_ADMIN
from app.core.security import get_password_hash
from app.core.token_scopes import SCOPE_READ_USERS, SCOPE_WRITE_USERS
from app.db.session import get_db
from app.models.user import PROVISIONING_SOURCE_LOCAL, PROVISIONING_SOURCE_OIDC, User
from app.schemas.user import UserAdminResponse, UserCreateRequest, UserUpdateRequest
from app.services.audit import record_audit
from app.services.user_access import (
    LastActiveAdminError,
    acquire_active_admin_invariant_lock,
    ensure_active_approved_admin_remains,
    load_user_for_access_update,
    revoke_user_credentials,
)
from app.services.user_directory import (
    UserManagementContext,
    load_user_management_context,
    load_user_management_contexts,
)

router = APIRouter(prefix="/users", tags=["users"])
def _acquire_active_admin_invariant_lock(db: Session) -> None:
    acquire_active_admin_invariant_lock(db)


def _reload_admin_after_invariant_lock(db: Session, admin_id: uuid.UUID) -> User:
    admin = load_user_for_access_update(db, admin_id)
    if admin is None or admin.role != ROLE_ADMIN or not admin.is_active or not admin.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return admin


def _ensure_active_approved_admin_remains(db: Session, user: User, payload: UserUpdateRequest) -> None:
    next_role = payload.role if payload.role is not None else user.role
    next_is_active = payload.is_active if payload.is_active is not None else user.is_active
    next_is_approved = payload.is_approved if payload.is_approved is not None else user.is_approved

    try:
        ensure_active_approved_admin_remains(
            db,
            user,
            next_role=next_role,
            next_is_active=next_is_active,
            next_is_approved=next_is_approved,
        )
    except LastActiveAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("", response_model=list[UserAdminResponse])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    _ = admin
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    contexts = load_user_management_contexts(db, [user.id for user in users])
    return [_user_admin_response(user, contexts.get(user.id, UserManagementContext())) for user in users]


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    if payload.role not in ALL_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")

    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    user = User(
        email=payload.email.lower(),
        password_hash=get_password_hash(payload.password),
        provisioning_source=PROVISIONING_SOURCE_LOCAL,
        role=payload.role,
        is_active=payload.is_active,
        is_approved=payload.is_approved,
        approved_at=datetime.now(timezone.utc) if payload.is_approved else None,
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.create",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "email": user.email,
            "role": user.role,
            "is_approved": user.is_approved,
            "provisioning_source": user.provisioning_source,
        },
    )
    db.commit()
    db.refresh(user)
    return _user_admin_response(user, UserManagementContext())


@router.patch("/{user_id}", response_model=UserAdminResponse)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    _acquire_active_admin_invariant_lock(db)
    admin = _reload_admin_after_invariant_lock(db, admin.id)
    user = load_user_for_access_update(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    management = load_user_management_context(db, user.id)
    _ensure_locally_managed_changes(user, payload, management)
    _ensure_active_approved_admin_remains(db, user, payload)
    should_rotate_auth_tokens = payload.password is not None
    revoked_api_tokens = 0

    if payload.role is not None:
        if payload.role not in ALL_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
        if payload.role != user.role:
            should_rotate_auth_tokens = True
        user.role = payload.role

    if payload.email is not None:
        existing = db.scalar(select(User).where(User.email == payload.email.lower(), User.id != user_id))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
        user.email = payload.email.lower()

    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            should_rotate_auth_tokens = True
        user.is_active = payload.is_active

    if payload.is_approved is not None:
        if payload.is_approved != user.is_approved:
            should_rotate_auth_tokens = True
        user.is_approved = payload.is_approved
        user.approved_at = datetime.now(timezone.utc) if payload.is_approved else None

    if payload.password is not None:
        user.password_hash = get_password_hash(payload.password)
        user.password_login_enabled = True

    if should_rotate_auth_tokens:
        revoked_api_tokens = revoke_user_credentials(db, user)

    db.add(user)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.update",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "role": user.role,
            "is_active": user.is_active,
            "is_approved": user.is_approved,
            "password_updated": payload.password is not None,
            "password_login_enabled": user.password_login_enabled,
            "auth_token_version": user.auth_token_version,
            "revoked_api_tokens": int(revoked_api_tokens),
        },
    )
    db.commit()
    db.refresh(user)
    return _user_admin_response(user, management)


def _ensure_locally_managed_changes(
    user: User,
    payload: UserUpdateRequest,
    management: UserManagementContext,
) -> None:
    provider_name = management.provider.name if management.provider is not None else "the identity provider"
    if payload.password is not None and user.provisioning_source == PROVISIONING_SOURCE_OIDC:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Password is managed by {provider_name} for this SSO-provisioned account",
        )
    if payload.email is not None and payload.email.lower() != user.email and user.provisioning_source == PROVISIONING_SOURCE_OIDC:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email is managed by {provider_name} for this SSO-provisioned account",
        )
    if payload.role is not None and payload.role != user.role and management.role_managed_by == "oidc":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role is managed by {provider_name} and synchronized during SSO sign-in",
        )


def _user_admin_response(user: User, management: UserManagementContext) -> UserAdminResponse:
    identity = management.identity
    return UserAdminResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        is_approved=user.is_approved,
        approved_at=user.approved_at,
        created_at=user.created_at,
        password_login_enabled=user.password_login_enabled,
        provisioning_source=user.provisioning_source,
        authentication_methods=management.authentication_methods(user),
        oidc_provider_name=management.provider.name if management.provider is not None else None,
        oidc_linked_at=identity.created_at if identity is not None else None,
        oidc_last_login_at=identity.last_login_at if identity is not None else None,
        password_managed_by=management.password_managed_by(user),
        role_managed_by=management.role_managed_by,
    )
