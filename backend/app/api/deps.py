import logging
import socket
import threading
import time
import uuid
from ipaddress import ip_address, ip_network
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.security import (
    decode_access_token_claims,
    extract_api_token_prefix,
    hash_api_token,
)
from app.core.config import get_settings
from app.core.logging_config import verbose_logging_enabled
from app.core.token_scopes import has_required_scope, normalize_token_scopes
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.services.auth_sessions import resolve_auth_session, touch_auth_session

AUTH_SESSION_BEARER = "session_bearer"
AUTH_SESSION_COOKIE = "session_cookie"
AUTH_API_TOKEN = "api_token"

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/v1/auth/login",
    auto_error=False,
    description=(
        "Use a scoped API token in the Authorization header. Browser sign-in sessions are created at "
        "`/v1/auth/login`, mirrored through the web proxy at `/api/v1/auth/login`, and carried by HttpOnly cookies."
    ),
)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
logger = logging.getLogger(__name__)
_PROXY_HOST_CACHE_TTL_SECONDS = 30.0
_proxy_host_cache_lock = threading.Lock()
_proxy_host_cache: dict[str, tuple[float, frozenset[str]]] = {}


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User:
    user, credentials_present = _resolve_authenticated_user(request, db, token)
    if user is None:
        if getattr(request.state, "auth_credential_kind", None) == AUTH_SESSION_BEARER:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer auth requires a scoped API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        detail = "Invalid credentials" if credentials_present else "Not authenticated"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    return user


def get_optional_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User | None:
    user, _credentials_present = _resolve_authenticated_user(request, db, token)
    return user


def get_auth_credential_kind(request: Request) -> str | None:
    return getattr(request.state, "auth_credential_kind", None)


def get_current_auth_session_id(request: Request) -> uuid.UUID | None:
    return getattr(request.state, "auth_session_id", None)


def is_cookie_session_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_SESSION_COOKIE


def is_api_token_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_API_TOKEN


def require_roles(*roles: str):
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return user

    return _checker


def _ensure_user_can_authenticate(user: User) -> None:
    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )


get_operator_user = require_roles(ROLE_ADMIN, ROLE_ANALYST)
get_admin_user = require_roles(ROLE_ADMIN)


def require_token_scopes(*required_scopes: str):
    def _checker(request: Request, user: User = Depends(get_current_user)) -> User:
        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is None:
            if get_auth_credential_kind(request) == AUTH_SESSION_BEARER:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Bearer auth requires a scoped API token",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return user

        granted = set(token_scopes)
        if not granted and get_settings().allow_legacy_unscoped_tokens:
            return user

        for required_scope in required_scopes:
            if not has_required_scope(granted, required_scope):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient token scope",
                )

        return user

    _checker._threatlens_required_scopes = tuple(required_scopes)
    return _checker


def _resolve_jwt_user(db: Session, token: str) -> User | None:
    claims = decode_access_token_claims(token)
    if claims is None:
        return None
    subject = claims.get("sub")
    if not subject:
        return None

    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        return None

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None

    try:
        token_version = int(claims.get("ver", 0))
    except (TypeError, ValueError):
        return None
    if token_version != int(user.auth_token_version or 0):
        return None
    return user


def _resolve_api_token_user(db: Session, token: str) -> tuple[User, list[str]] | None:
    prefix = extract_api_token_prefix(token)
    if prefix is None:
        return None

    token_hash = hash_api_token(token)
    now = datetime.now(timezone.utc)

    api_token = db.scalar(
        select(ApiToken).where(
            and_(
                ApiToken.token_prefix == prefix,
                ApiToken.token_hash == token_hash,
                ApiToken.revoked_at.is_(None),
            )
        )
    )
    if api_token is None:
        return None

    if api_token.expires_at is not None:
        expires_at = api_token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            return None

    user = db.scalar(select(User).where(User.id == api_token.user_id))
    if user is None:
        return None

    scopes = normalize_token_scopes(api_token.scopes)
    if _should_update_last_used(api_token.last_used_at, now):
        api_token.last_used_at = now
        db.add(api_token)
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "api_token_last_used_update_failed token_id=%s error_type=%s",
                api_token.id,
                type(exc).__name__,
                exc_info=verbose_logging_enabled(get_settings()),
            )
    return user, scopes


def _resolve_authenticated_user(
    request: Request, db: Session, token: str | None
) -> tuple[User | None, bool]:
    request.state.token_scopes = None
    request.state.auth_via_api_token = False
    request.state.auth_credential_kind = None
    request.state.auth_session_id = None
    token_source = "header"

    if not token:
        token = _resolve_cookie_token(request)
        token_source = "cookie"
    if not token:
        return None, False

    if token_source == "cookie":
        _enforce_csrf_if_needed(request)
        if token.startswith("tls_"):
            session_user = _resolve_opaque_session_user(request, db, token)
            if session_user is None:
                _persist_session_state(db)
                return None, True
            _ensure_user_can_authenticate(session_user)
            request.state.auth_credential_kind = AUTH_SESSION_COOKIE
            return session_user, True
        user = _resolve_jwt_user(db, token)
        if user is None:
            return None, True
        _ensure_user_can_authenticate(user)
        request.state.auth_credential_kind = AUTH_SESSION_COOKIE
        return user, True

    session_user = _resolve_jwt_user(db, token)
    if session_user is not None:
        request.state.auth_credential_kind = AUTH_SESSION_BEARER
        return None, True
    if token.startswith("tls_"):
        request.state.auth_credential_kind = AUTH_SESSION_BEARER
        return None, True

    token_result = _resolve_api_token_user(db, token)
    if token_result is None:
        return None, True

    user, scopes = token_result
    _ensure_user_can_authenticate(user)
    request.state.token_scopes = scopes
    request.state.auth_via_api_token = True
    request.state.auth_credential_kind = AUTH_API_TOKEN
    return user, True


def _resolve_opaque_session_user(
    request: Request, db: Session, token: str
) -> User | None:
    session = resolve_auth_session(db, token)
    if session is None:
        return None
    user = db.scalar(select(User).where(User.id == session.user_id))
    if user is None:
        return None
    request.state.auth_session_id = session.id
    if touch_auth_session(db, session):
        _persist_session_state(db)
    return user


def _persist_session_state(db: Session) -> None:
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(
            "auth_session_state_update_failed error_type=%s",
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )


def _should_update_last_used(last_used_at: datetime | None, now: datetime) -> bool:
    if last_used_at is None:
        return True
    if last_used_at.tzinfo is None:
        last_used_at = last_used_at.replace(tzinfo=timezone.utc)

    settings = get_settings()
    elapsed = (now - last_used_at).total_seconds()
    return elapsed >= settings.api_token_last_used_update_interval_seconds


def _resolve_cookie_token(request: Request) -> str | None:
    settings = get_settings()
    return request.cookies.get(settings.auth_cookie_name)


def _enforce_csrf_if_needed(request: Request) -> None:
    settings = get_settings()
    if not settings.auth_require_csrf:
        return
    if request.method.upper() not in UNSAFE_METHODS:
        return

    csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
    csrf_header = request.headers.get(settings.auth_csrf_header_name)
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid CSRF token",
        )


def resolve_client_ip(request: Request) -> str:
    settings = get_settings()
    candidate_ip = (
        request.client.host if request.client and request.client.host else "unknown"
    )
    if candidate_ip == "unknown":
        return candidate_ip

    trusted_proxy_hosts = getattr(settings, "trusted_proxy_hosts", [])
    if not _is_trusted_proxy(
        candidate_ip, settings.trusted_proxy_cidrs, trusted_proxy_hosts
    ):
        return candidate_ip

    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return candidate_ip

    for forwarded_ip in reversed(_parse_forwarded_for_ips(forwarded_for)):
        if not _is_trusted_proxy(
            candidate_ip, settings.trusted_proxy_cidrs, trusted_proxy_hosts
        ):
            break
        candidate_ip = forwarded_ip

    return candidate_ip


def _parse_forwarded_for_ips(forwarded_for: str) -> list[str]:
    parsed_hops: list[str] = []
    for raw_hop in forwarded_for.split(","):
        candidate = raw_hop.strip()
        if not candidate:
            continue
        try:
            ip_address(candidate)
        except ValueError:
            continue
        parsed_hops.append(candidate)
    return parsed_hops


def _is_trusted_proxy(
    remote_ip: str,
    trusted_proxy_cidrs: list[str],
    trusted_proxy_hosts: list[str] | None = None,
) -> bool:
    try:
        parsed_remote_ip = ip_address(remote_ip)
    except ValueError:
        return False

    for raw_cidr in trusted_proxy_cidrs:
        try:
            network = ip_network(raw_cidr, strict=False)
        except ValueError:
            continue
        if parsed_remote_ip in network:
            return True
    return remote_ip in _trusted_proxy_host_addresses(trusted_proxy_hosts or [])


def _trusted_proxy_host_addresses(hosts: list[str]) -> frozenset[str]:
    addresses: set[str] = set()
    now = time.monotonic()
    for host in hosts:
        normalized = host.strip().lower()
        if not normalized:
            continue
        with _proxy_host_cache_lock:
            cached = _proxy_host_cache.get(normalized)
        if cached is not None and cached[0] > now:
            addresses.update(cached[1])
            continue
        try:
            resolved = frozenset(
                entry[4][0]
                for entry in socket.getaddrinfo(
                    normalized, None, type=socket.SOCK_STREAM
                )
                if entry[4] and entry[4][0]
            )
        except OSError as exc:
            logger.warning(
                "trusted_proxy_host_resolution_failed host=%s error=%s", normalized, exc
            )
            resolved = frozenset()
        with _proxy_host_cache_lock:
            _proxy_host_cache[normalized] = (
                now + _PROXY_HOST_CACHE_TTL_SECONDS,
                resolved,
            )
        addresses.update(resolved)
    return frozenset(addresses)
