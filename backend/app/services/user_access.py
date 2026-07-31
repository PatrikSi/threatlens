from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN
from app.models.api_token import ApiToken
from app.models.user import User

ACTIVE_ADMIN_ADVISORY_LOCK_ID = 6072351299479551566


class LastActiveAdminError(ValueError):
    pass


def acquire_active_admin_invariant_lock(db: Session) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.scalar(select(func.pg_advisory_xact_lock(ACTIVE_ADMIN_ADVISORY_LOCK_ID)))
        return
    db.scalars(
        select(User.id)
        .where(User.role == ROLE_ADMIN, User.is_active.is_(True), User.is_approved.is_(True))
        .with_for_update()
    ).all()


def load_user_for_access_update(db: Session, user_id: uuid.UUID) -> User | None:
    return db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def ensure_active_approved_admin_remains(
    db: Session,
    user: User,
    *,
    next_role: str,
    next_is_active: bool,
    next_is_approved: bool,
) -> None:
    if next_role == ROLE_ADMIN and next_is_active and next_is_approved:
        return
    if user.role != ROLE_ADMIN or not user.is_active or not user.is_approved:
        return

    other_admin_count = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.id != user.id,
                User.role == ROLE_ADMIN,
                User.is_active.is_(True),
                User.is_approved.is_(True),
            )
        )
        or 0
    )
    if other_admin_count == 0:
        raise LastActiveAdminError("At least one active approved admin user is required")


def revoke_user_credentials(db: Session, user: User, *, now: datetime | None = None) -> int:
    revoked_at = now or datetime.now(timezone.utc)
    revoked_api_tokens = (
        db.execute(
            update(ApiToken)
            .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        ).rowcount
        or 0
    )
    user.auth_token_version = int(user.auth_token_version or 0) + 1
    db.add(user)
    return int(revoked_api_tokens)
