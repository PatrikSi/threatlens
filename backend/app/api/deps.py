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
from app.core.api_errors import ApiHTTPException
from app.core.security import (
    decode_access_token_claims,
    extract_api_token_prefix,
    hash_api_token,
)
from app.core.config import get_settings
from app.core.logging_config import update_log_context, verbose_logging_enabled
from app.core.token_scopes import has_required_scope, normalize_token_scopes
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.services.auth_sessions import resolve_auth_session, touch_auth_session
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_user,
)

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
    request.state.authorization_context = _authorization_context_for_request(
        request, db, user
    )
    _set_authenticated_log_context(request, user)
    return user


def get_optional_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User | None:
    user, _credentials_present = _resolve_authenticated_user(request, db, token)
    if user is not None:
        request.state.authorization_context = _authorization_context_for_request(
            request, db, user
        )
        _set_authenticated_log_context(request, user)
    return user


def get_auth_credential_kind(request: Request) -> str | None:
    return getattr(request.state, "auth_credential_kind", None)


def get_current_auth_session_id(request: Request) -> uuid.UUID | None:
    return getattr(request.state, "auth_session_id", None)


def get_authorization_context(request: Request) -> AuthorizationContext | None:
    return getattr(request.state, "authorization_context", None)


def is_cookie_session_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_SESSION_COOKIE


def is_api_token_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_API_TOKEN


def require_roles(*roles: str):
    def _checker(
        request: Request,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        enforce_required_roles(
            request,
            db,
            user,
            roles=roles,
            detail="Insufficient permissions",
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


def require_permissions(
    *required_permissions: str,
    denial_detail: str | None = None,
):
    def _checker(
        request: Request,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        authorization = get_authorization_context(request)
        if authorization is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Effective access could not be resolved. Retry the request.",
            )
        missing_permissions = [
            permission
            for permission in required_permissions
            if not authorization.has(permission)
        ]
        if missing_permissions:
            denial_reasons = {
                permission: authorization.explanation(permission)["reason"]
                for permission in missing_permissions
            }
            _record_permission_denial(
                db,
                request=request,
                user=user,
                required_permissions=required_permissions,
                missing_permissions=missing_permissions,
                policy_revision=authorization.policy_revision,
                denial_reasons=denial_reasons,
            )
            credential_scope_denial = all(
                reason == "credential_scope_missing"
                for reason in denial_reasons.values()
            )
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Insufficient token scope"
                    if credential_scope_denial
                    else denial_detail
                    or "Your account does not have the required permission."
                ),
                error_code="permission_denied",
                error_context={
                    "required_permissions": list(required_permissions),
                    "missing_permissions": missing_permissions,
                    "denial_reasons": denial_reasons,
                    "policy_revision": authorization.policy_revision,
                },
            )
        return user

    _checker._threatlens_required_scopes = tuple(required_permissions)
    return _checker


def require_permission_roles(
    *required_permissions: str,
    roles: tuple[str, ...],
    detail: str,
    error_code: str = "permission_denied",
):
    permission_checker = require_permissions(
        *required_permissions,
        denial_detail=detail,
    )

    def _checker(
        request: Request,
        user: User = Depends(permission_checker),
        db: Session = Depends(get_db),
    ) -> User:
        enforce_required_roles(
            request,
            db,
            user,
            roles=roles,
            detail=detail,
            error_code=error_code,
        )
        return user

    _checker._threatlens_required_scopes = tuple(required_permissions)
    _checker._threatlens_required_roles = tuple(roles)
    return _checker


def enforce_required_roles(
    request: Request,
    db: Session,
    user: User,
    *,
    roles: tuple[str, ...],
    detail: str,
    error_code: str = "permission_denied",
) -> None:
    if user.role in roles:
        return
    authorization = get_authorization_context(request)
    _record_role_denial(
        db,
        request=request,
        user=user,
        required_roles=roles,
        policy_revision=(
            authorization.policy_revision if authorization is not None else None
        ),
    )
    raise ApiHTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
        error_code=error_code,
        error_context={
            "required_legacy_roles": list(roles),
            "policy_revision": (
                authorization.policy_revision if authorization is not None else None
            ),
        },
    )


def _record_permission_denial(
    db: Session,
    *,
    request: Request,
    user: User,
    required_permissions: tuple[str, ...],
    missing_permissions: list[str],
    policy_revision: int,
    denial_reasons: dict[str, object],
) -> None:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    try:
        record_audit(
            db,
            actor_user_id=user.id,
            action="authorization.permission_denied",
            resource_type="api_route",
            resource_id=request.url.path,
            success=False,
            metadata={
                "method": request.method,
                "route_template": route_path,
                "required_permissions": list(required_permissions),
                "missing_permissions": missing_permissions,
                "denial_reasons": denial_reasons,
                "policy_revision": policy_revision,
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "permission_denial_audit_failed user_id=%s error_type=%s",
            user.id,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )


def _record_role_denial(
    db: Session,
    *,
    request: Request,
    user: User,
    required_roles: tuple[str, ...],
    policy_revision: int | None,
) -> None:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    try:
        record_audit(
            db,
            actor_user_id=user.id,
            action="authorization.role_denied",
            resource_type="api_route",
            resource_id=request.url.path,
            success=False,
            metadata={
                "method": request.method,
                "route_template": route_path,
                "required_legacy_roles": list(required_roles),
                "actual_legacy_role": user.role,
                "policy_revision": policy_revision,
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "role_denial_audit_failed user_id=%s error_type=%s",
            user.id,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )


def _authorization_context_for_request(
    request: Request, db: Session, user: User
) -> AuthorizationContext:
    credential_scopes = getattr(request.state, "token_scopes", None)
    if credential_scopes == [] and get_settings().allow_legacy_unscoped_tokens:
        credential_scopes = None
    try:
        return authorization_context_for_user(
            db,
            user,
            credential_scopes=credential_scopes,
        )
    except AuthorizationStateUnavailable as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            error_code="iam_policy_unavailable",
        ) from exc


def _set_authenticated_log_context(request: Request, user: User) -> None:
    credential_id = getattr(request.state, "api_token_id", None)
    if credential_id is None:
        credential_id = get_current_auth_session_id(request)
    update_log_context(
        actor_principal_type="user",
        actor_principal_id=user.id,
        credential_kind=get_auth_credential_kind(request),
        credential_id=credential_id,
        source_ip=resolve_client_ip(request),
    )


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


def _resolve_api_token_user(
    db: Session, token: str
) -> tuple[User, list[str], uuid.UUID] | None:
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
    return user, scopes, api_token.id


def _resolve_authenticated_user(
    request: Request, db: Session, token: str | None
) -> tuple[User | None, bool]:
    request.state.token_scopes = None
    request.state.auth_via_api_token = False
    request.state.auth_credential_kind = None
    request.state.auth_session_id = None
    request.state.authorization_context = None
    request.state.api_token_id = None
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

    user, scopes, api_token_id = token_result
    _ensure_user_can_authenticate(user)
    request.state.token_scopes = scopes
    request.state.auth_via_api_token = True
    request.state.auth_credential_kind = AUTH_API_TOKEN
    request.state.api_token_id = api_token_id
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
