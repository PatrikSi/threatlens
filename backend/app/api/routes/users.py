import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.core.rbac import ALL_ROLES
from app.core.security import get_password_hash
from app.core.token_scopes import SCOPE_READ_USERS, SCOPE_WRITE_USERS
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserAdminResponse, UserCreateRequest, UserUpdateRequest
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserAdminResponse])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    _ = admin
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    return list(users)


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
        metadata={"email": user.email, "role": user.role, "is_approved": user.is_approved},
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserAdminResponse)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.role is not None:
        if payload.role not in ALL_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
        user.role = payload.role

    if payload.email is not None:
        existing = db.scalar(select(User).where(User.email == payload.email.lower(), User.id != user_id))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
        user.email = payload.email.lower()

    if payload.is_active is not None:
        user.is_active = payload.is_active

    if payload.is_approved is not None:
        user.is_approved = payload.is_approved
        user.approved_at = datetime.now(timezone.utc) if payload.is_approved else None

    if payload.password is not None:
        user.password_hash = get_password_hash(payload.password)
        user.auth_token_version = int(user.auth_token_version or 0) + 1

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
            "auth_token_version": user.auth_token_version,
        },
    )
    db.commit()
    db.refresh(user)
    return user
