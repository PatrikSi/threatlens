from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.auth_session import AuthSession
from app.models.mfa import UserTOTPCredential
from app.models.user import User

SESSION_TOKEN_MARKER = "tls"
MAX_USER_AGENT_CHARS = 512
MAX_CLIENT_IP_CHARS = 64


@dataclass(frozen=True)
class CreatedAuthSession:
    token: str
    session: AuthSession


@dataclass(frozen=True)
class RotatedAuthSession:
    created: CreatedAuthSession
    revoked_sessions: int
    revoked_other_sessions: int


@dataclass(frozen=True)
class AuthSessionInventory:
    sessions: list[AuthSession]
    active_count: int
    active_truncated: bool
    history_truncated: bool


class AuthSessionStateError(RuntimeError):
    pass


def lock_user_auth_states(
    db: Session,
    user_ids: set[uuid.UUID] | list[uuid.UUID] | tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, User]:
    ordered_ids = sorted(set(user_ids), key=lambda value: value.hex)
    if not ordered_ids:
        return {}
    users = db.scalars(
        select(User)
        .where(User.id.in_(ordered_ids))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {user.id: user for user in users}


def lock_user_auth_state(db: Session, user_id: uuid.UUID) -> User | None:
    return lock_user_auth_states(db, [user_id]).get(user_id)


def create_auth_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    auth_token_version: int = 0,
    auth_method: str,
    mfa_method: str | None,
    identity_acr: str | None = None,
    identity_amr: list[str] | None = None,
    identity_authenticated_at: datetime | None = None,
    authenticated_at: datetime | None = None,
    absolute_expires_at: datetime | None = None,
    client_ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> CreatedAuthSession:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    db.flush()
    locked_user = lock_user_auth_state(db, user_id)
    if locked_user is None:
        raise AuthSessionStateError(
            "The account no longer exists, so a browser session cannot be created."
        )
    if int(locked_user.auth_token_version or 0) != int(auth_token_version):
        raise AuthSessionStateError(
            "Account security changed before the browser session could be created."
        )
    absolute_ttl, idle_ttl, _activity_interval = session_timing_seconds()
    effective_authenticated_at = _as_utc(authenticated_at or current_time)
    effective_absolute_expiry = (
        _as_utc(absolute_expires_at)
        if absolute_expires_at is not None
        else current_time + timedelta(seconds=absolute_ttl)
    )
    if effective_absolute_expiry <= current_time:
        raise AuthSessionStateError(
            "A replacement browser session cannot be created after its absolute expiry."
        )
    session_id = uuid.uuid4()
    token = _build_session_token(session_id)
    session = AuthSession(
        id=session_id,
        user_id=user_id,
        token_hash=hash_session_token(token),
        auth_token_version=int(auth_token_version),
        auth_method=auth_method,
        mfa_method=mfa_method,
        identity_acr=_bounded(identity_acr, 255),
        identity_amr_json=list(identity_amr) if identity_amr else None,
        client_ip=_bounded(client_ip, MAX_CLIENT_IP_CHARS),
        user_agent=_bounded(user_agent, MAX_USER_AGENT_CHARS),
        authenticated_at=effective_authenticated_at,
        identity_authenticated_at=(
            _as_utc(identity_authenticated_at)
            if identity_authenticated_at is not None
            else None
        ),
        last_seen_at=current_time,
        idle_expires_at=min(
            effective_absolute_expiry,
            current_time + timedelta(seconds=idle_ttl),
        ),
        absolute_expires_at=effective_absolute_expiry,
    )
    db.add(session)
    db.flush()
    _enforce_active_session_limit(
        db,
        user_id=user_id,
        auth_token_version=int(auth_token_version),
        newest_session_id=session.id,
        now=current_time,
    )
    return CreatedAuthSession(token=token, session=session)


def resolve_auth_session(
    db: Session,
    token: str,
    *,
    now: datetime | None = None,
) -> AuthSession | None:
    session_id = extract_auth_session_id(token)
    if session_id is None:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.id == session_id))
    if session is None or not hmac.compare_digest(
        session.token_hash, hash_session_token(token)
    ):
        return None
    current_time = _as_utc(now or datetime.now(timezone.utc))
    current_auth_version = db.scalar(
        select(User.auth_token_version).where(User.id == session.user_id)
    )
    if current_auth_version is None or session.auth_token_version != int(
        current_auth_version or 0
    ):
        _mark_revoked(session, now=current_time, reason="auth_generation_changed")
        return None
    if session.revoked_at is not None:
        return None
    if _as_utc(session.absolute_expires_at) <= current_time:
        _mark_revoked(session, now=current_time, reason="absolute_expired")
        return None
    if _as_utc(session.idle_expires_at) <= current_time:
        _mark_revoked(session, now=current_time, reason="idle_expired")
        return None
    return session


def lock_exact_auth_session(
    db: Session,
    *,
    token: str,
    expected_session_id: uuid.UUID,
    user_id: uuid.UUID,
    auth_token_version: int,
    now: datetime | None = None,
) -> AuthSession | None:
    parsed_session_id = extract_auth_session_id(token)
    if parsed_session_id != expected_session_id:
        return None
    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == expected_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current_time = _as_utc(now or datetime.now(timezone.utc))
    if (
        session is None
        or session.user_id != user_id
        or session.auth_token_version != int(auth_token_version)
        or session.revoked_at is not None
        or _as_utc(session.idle_expires_at) <= current_time
        or _as_utc(session.absolute_expires_at) <= current_time
        or not hmac.compare_digest(session.token_hash, hash_session_token(token))
    ):
        return None
    return session


def touch_auth_session(
    db: Session,
    session: AuthSession,
    *,
    now: datetime | None = None,
) -> bool:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    _absolute_ttl, idle_ttl, activity_interval = session_timing_seconds()
    if (
        current_time - _as_utc(session.last_seen_at)
    ).total_seconds() < activity_interval:
        return False
    session.last_seen_at = current_time
    session.idle_expires_at = min(
        _as_utc(session.absolute_expires_at),
        current_time + timedelta(seconds=idle_ttl),
    )
    db.add(session)
    return True


def rotate_user_auth_sessions(
    db: Session,
    *,
    user: User,
    current_session_id: uuid.UUID | None,
    reason: str,
    default_auth_method: str,
    mfa_method: str | None,
    preserve_current_mfa_method: bool = False,
    preserve_current_auth_method: bool = True,
    identity_authenticated_at: datetime | None = None,
    preserve_current_identity_authentication: bool = False,
    identity_acr: str | None = None,
    identity_amr: list[str] | None = None,
    preserve_current_identity_assurance: bool = False,
    preserve_current_timing: bool = False,
    client_ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> RotatedAuthSession:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    locked_user = lock_user_auth_state(db, user.id)
    if locked_user is None:
        raise AuthSessionStateError(
            "The account no longer exists, so its browser sessions cannot be rotated."
        )
    current_session = (
        db.scalar(
            select(AuthSession).where(
                AuthSession.id == current_session_id,
                AuthSession.user_id == locked_user.id,
                AuthSession.revoked_at.is_(None),
            )
        )
        if current_session_id is not None
        else None
    )
    auth_method = (
        current_session.auth_method
        if preserve_current_auth_method and current_session
        else default_auth_method
    )
    effective_mfa_method = (
        current_session.mfa_method
        if preserve_current_mfa_method and current_session is not None
        else mfa_method
    )
    effective_identity_authenticated_at = (
        current_session.identity_authenticated_at
        if preserve_current_identity_authentication and current_session is not None
        else identity_authenticated_at
    )
    effective_identity_acr = (
        current_session.identity_acr
        if preserve_current_identity_assurance and current_session is not None
        else identity_acr
    )
    effective_identity_amr = (
        list(current_session.identity_amr_json or [])
        if preserve_current_identity_assurance and current_session is not None
        else identity_amr
    )
    revoked_sessions = revoke_all_auth_sessions(
        db,
        user_id=locked_user.id,
        reason=reason,
        now=current_time,
    )
    db.execute(
        delete(UserTOTPCredential).where(
            UserTOTPCredential.user_id == locked_user.id,
            UserTOTPCredential.status == "pending",
        )
    )
    locked_user.auth_token_version = int(locked_user.auth_token_version or 0) + 1
    db.add(locked_user)
    created = create_auth_session(
        db,
        user_id=locked_user.id,
        auth_token_version=locked_user.auth_token_version,
        auth_method=auth_method,
        mfa_method=effective_mfa_method,
        identity_acr=effective_identity_acr,
        identity_amr=effective_identity_amr,
        identity_authenticated_at=effective_identity_authenticated_at,
        authenticated_at=(
            current_session.authenticated_at
            if preserve_current_timing and current_session is not None
            else None
        ),
        absolute_expires_at=(
            current_session.absolute_expires_at
            if preserve_current_timing and current_session is not None
            else None
        ),
        client_ip=client_ip,
        user_agent=user_agent,
        now=current_time,
    )
    current_was_revoked = current_session is not None
    return RotatedAuthSession(
        created=created,
        revoked_sessions=revoked_sessions,
        revoked_other_sessions=max(
            0,
            revoked_sessions - (1 if current_was_revoked else 0),
        ),
    )


def rotate_exact_auth_session(
    db: Session,
    *,
    user: User,
    current_session_id: uuid.UUID,
    reason: str,
    auth_method: str,
    mfa_method: str | None,
    authenticated_at: datetime,
    identity_authenticated_at: datetime | None,
    identity_acr: str | None,
    identity_amr: list[str] | None,
    client_ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> RotatedAuthSession:
    """Rotate one already-authenticated browser session without changing account generation."""

    current_time = _as_utc(now or datetime.now(timezone.utc))
    locked_user = lock_user_auth_state(db, user.id)
    current_session = db.scalar(
        select(AuthSession)
        .where(
            AuthSession.id == current_session_id,
            AuthSession.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        locked_user is None
        or current_session is None
        or current_session.revoked_at is not None
        or current_session.auth_token_version
        != int(locked_user.auth_token_version or 0)
        or _as_utc(current_session.idle_expires_at) <= current_time
        or _as_utc(current_session.absolute_expires_at) <= current_time
    ):
        raise AuthSessionStateError(
            "The current browser session changed before it could be rotated."
        )

    _mark_revoked(current_session, now=current_time, reason=_bounded(reason, 64) or reason)
    db.add(current_session)
    created = create_auth_session(
        db,
        user_id=locked_user.id,
        auth_token_version=int(locked_user.auth_token_version or 0),
        auth_method=auth_method,
        mfa_method=mfa_method,
        identity_acr=identity_acr,
        identity_amr=identity_amr,
        identity_authenticated_at=identity_authenticated_at,
        authenticated_at=authenticated_at,
        absolute_expires_at=current_session.absolute_expires_at,
        client_ip=client_ip,
        user_agent=user_agent,
        now=current_time,
    )
    return RotatedAuthSession(
        created=created,
        revoked_sessions=1,
        revoked_other_sessions=0,
    )


def revoke_auth_session(
    db: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> bool:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=current_time, revoked_reason=_bounded(reason, 64))
    )
    return bool(result.rowcount)


def revoke_all_auth_sessions(
    db: Session,
    *,
    user_id: uuid.UUID,
    reason: str,
    except_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    filters = [AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)]
    if except_session_id is not None:
        filters.append(AuthSession.id != except_session_id)
    result = db.execute(
        update(AuthSession)
        .where(*filters)
        .values(revoked_at=current_time, revoked_reason=_bounded(reason, 64))
    )
    return int(result.rowcount or 0)


def list_user_auth_sessions(
    db: Session,
    *,
    user_id: uuid.UUID,
    current_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> AuthSessionInventory:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    max_active = get_settings().auth_max_active_sessions_per_user
    active_filters = (
        AuthSession.user_id == user_id,
        AuthSession.auth_token_version == User.auth_token_version,
        AuthSession.revoked_at.is_(None),
        AuthSession.idle_expires_at > current_time,
        AuthSession.absolute_expires_at > current_time,
    )
    active_count = int(
        db.scalar(
            select(func.count(AuthSession.id))
            .join(User, User.id == AuthSession.user_id)
            .where(*active_filters)
        )
        or 0
    )
    active = list(
        db.scalars(
            select(AuthSession)
            .join(User, User.id == AuthSession.user_id)
            .where(*active_filters)
            .order_by(
                case((AuthSession.id == current_session_id, 1), else_=0).desc(),
                AuthSession.last_seen_at.desc(),
                AuthSession.id.desc(),
            )
            .limit(max_active)
        ).all()
    )
    history = list(
        db.scalars(
            select(AuthSession)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.user_id == user_id,
                or_(
                    AuthSession.auth_token_version != User.auth_token_version,
                    AuthSession.revoked_at.is_not(None),
                    AuthSession.idle_expires_at <= current_time,
                    AuthSession.absolute_expires_at <= current_time,
                ),
            )
            .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
            .limit(201)
        ).all()
    )
    return AuthSessionInventory(
        sessions=[*active, *history[:200]],
        active_count=active_count,
        active_truncated=active_count > len(active),
        history_truncated=len(history) > 200,
    )


def _enforce_active_session_limit(
    db: Session,
    *,
    user_id: uuid.UUID,
    auth_token_version: int,
    newest_session_id: uuid.UUID,
    now: datetime,
) -> int:
    limit = get_settings().auth_max_active_sessions_per_user
    overflow_ids = list(
        db.scalars(
            select(AuthSession.id)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.auth_token_version == auth_token_version,
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now,
                AuthSession.absolute_expires_at > now,
            )
            .order_by(
                case((AuthSession.id == newest_session_id, 1), else_=0).desc(),
                AuthSession.created_at.desc(),
                AuthSession.id.desc(),
            )
            .offset(limit)
        ).all()
    )
    if not overflow_ids:
        return 0
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.id.in_(overflow_ids), AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason="active_session_limit")
    )
    return int(result.rowcount or 0)


def cleanup_auth_sessions(
    db: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
    limit: int = 1_000,
) -> int:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    cutoff = current_time - timedelta(days=max(1, retention_days))
    stale_ids = list(
        db.scalars(
            select(AuthSession.id)
            .where(
                (AuthSession.revoked_at.is_not(None))
                | (AuthSession.absolute_expires_at < current_time),
                func.coalesce(
                    AuthSession.revoked_at,
                    AuthSession.absolute_expires_at,
                )
                < cutoff,
            )
            .order_by(
                func.coalesce(
                    AuthSession.revoked_at,
                    AuthSession.absolute_expires_at,
                ).asc(),
                AuthSession.id,
            )
            .limit(max(1, min(limit, 10_000)))
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not stale_ids:
        return 0
    result = db.execute(
        delete(AuthSession).where(
            AuthSession.id.in_(stale_ids),
            (AuthSession.revoked_at.is_not(None))
            | (AuthSession.absolute_expires_at < current_time),
            func.coalesce(
                AuthSession.revoked_at,
                AuthSession.absolute_expires_at,
            )
            < cutoff,
        )
    )
    return int(result.rowcount or 0)


def count_active_auth_sessions(
    db: Session, *, user_id: uuid.UUID, now: datetime | None = None
) -> int:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    return int(
        db.scalar(
            select(func.count(AuthSession.id))
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.auth_token_version == User.auth_token_version,
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > current_time,
                AuthSession.absolute_expires_at > current_time,
            )
        )
        or 0
    )


def extract_auth_session_id(token: str) -> uuid.UUID | None:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != SESSION_TOKEN_MARKER or not parts[2]:
        return None
    try:
        return uuid.UUID(hex=parts[1])
    except (AttributeError, ValueError):
        return None


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_timing_seconds() -> tuple[int, int, int]:
    settings = get_settings()
    return (
        settings.auth_session_absolute_ttl_seconds,
        settings.auth_session_idle_ttl_seconds,
        settings.auth_session_activity_update_seconds,
    )


def auth_session_cookie_ttl_seconds(session: AuthSession) -> int:
    now = datetime.now(timezone.utc)
    return max(1, int((_as_utc(session.absolute_expires_at) - now).total_seconds()))


def _build_session_token(session_id: uuid.UUID) -> str:
    return f"{SESSION_TOKEN_MARKER}_{session_id.hex}_{secrets.token_urlsafe(32)}"


def _mark_revoked(session: AuthSession, *, now: datetime, reason: str) -> None:
    session.revoked_at = now
    session.revoked_reason = reason


def _bounded(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:limit] or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
