from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN
from app.models.api_token import ApiToken
from app.models.mfa import UserTOTPCredential
from app.models.user import User
from app.services.auth_sessions import lock_user_auth_states, revoke_all_auth_sessions

ACTIVE_ADMIN_ADVISORY_LOCK_ID = 6072351299479551566
OIDC_PROVIDER_CONFIG_ADVISORY_LOCK_ID = 6072351299479551567


class LastActiveAdminError(ValueError):
    pass


@dataclass(frozen=True)
class CredentialRevocationResult:
    api_tokens: int
    auth_sessions: int
    pending_mfa_enrollments: int = 0


def acquire_active_admin_invariant_lock(db: Session) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.scalar(select(func.pg_advisory_xact_lock(ACTIVE_ADMIN_ADVISORY_LOCK_ID)))
        return
    db.scalars(
        select(User.id)
        .where(
            User.role == ROLE_ADMIN,
            User.is_active.is_(True),
            User.is_approved.is_(True),
        )
        .with_for_update()
    ).all()


def acquire_oidc_provider_config_lock(db: Session) -> None:
    """Serialize primary OIDC configuration reads that precede row locking.

    The advisory lock also fences compare-and-create when no provider row exists.
    Callers that can change the final active administrator must acquire the active
    administrator invariant lock before this lock.
    """

    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.scalar(
            select(func.pg_advisory_xact_lock(OIDC_PROVIDER_CONFIG_ADVISORY_LOCK_ID))
        )


def acquire_oidc_provider_config_read_lock(db: Session) -> None:
    """Fence a callback against provider writes without serializing callbacks."""

    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.scalar(
            select(
                func.pg_advisory_xact_lock_shared(
                    OIDC_PROVIDER_CONFIG_ADVISORY_LOCK_ID
                )
            )
        )


def load_user_for_access_update(db: Session, user_id: uuid.UUID) -> User | None:
    return db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_users_for_security_change(
    db: Session,
    user_ids: set[uuid.UUID] | list[uuid.UUID] | tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, User]:
    """Lock user rows for a change that can affect the final-admin invariant."""

    acquire_active_admin_invariant_lock(db)
    return lock_user_auth_states(db, user_ids)


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
        raise LastActiveAdminError(
            "At least one active approved admin user is required"
        )


def revoke_user_credentials(
    db: Session, user: User, *, now: datetime | None = None
) -> int:
    return revoke_user_credentials_with_counts(db, user, now=now).api_tokens


def revoke_user_credentials_with_counts(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
) -> CredentialRevocationResult:
    revoked_at = now or datetime.now(timezone.utc)
    revoked_api_tokens = (
        db.execute(
            update(ApiToken)
            .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        ).rowcount
        or 0
    )
    revoked_auth_sessions = revoke_all_auth_sessions(
        db,
        user_id=user.id,
        reason="credentials_rotated",
        now=revoked_at,
    )
    cancelled_pending_mfa = int(
        db.execute(
            delete(UserTOTPCredential).where(
                UserTOTPCredential.user_id == user.id,
                UserTOTPCredential.status == "pending",
            )
        ).rowcount
        or 0
    )
    user.auth_token_version = int(user.auth_token_version or 0) + 1
    db.add(user)
    return CredentialRevocationResult(
        api_tokens=int(revoked_api_tokens),
        auth_sessions=revoked_auth_sessions,
        pending_mfa_enrollments=cancelled_pending_mfa,
    )
