import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user
from app.core.rbac import ALL_ROLES
from app.core.security import get_password_hash
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserAdminResponse, UserCreateRequest, UserUpdateRequest
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserAdminResponse])
def list_users(db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
    _ = admin
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    return list(users)


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreateRequest, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)):
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
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.create",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"email": user.email, "role": user.role},
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

    if payload.password is not None:
        user.password_hash = get_password_hash(payload.password)

    db.add(user)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.update",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"role": user.role, "is_active": user.is_active},
    )
    db.commit()
    db.refresh(user)
    return user
