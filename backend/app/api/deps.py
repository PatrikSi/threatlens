import uuid
from ipaddress import ip_address, ip_network
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.security import decode_access_token_claims, extract_api_token_prefix, hash_api_token
from app.core.config import get_settings
from app.core.token_scopes import has_required_scope, normalize_token_scopes
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}



def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User:
    request.state.token_scopes = None
    token_source = "header"

    if not token:
        token = _resolve_cookie_token(request)
        token_source = "cookie"
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    if token_source == "cookie":
        _enforce_csrf_if_needed(request)

    user = _resolve_jwt_user(db, token)
    if user is not None:
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
        return user

    token_result = _resolve_api_token_user(db, token)
    if token_result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user, scopes = token_result
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    request.state.token_scopes = scopes
    return user



def require_roles(*roles: str):
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _checker


get_operator_user = require_roles(ROLE_ADMIN, ROLE_ANALYST)
get_admin_user = require_roles(ROLE_ADMIN)


def require_token_scopes(*required_scopes: str):
    def _checker(request: Request, user: User = Depends(get_current_user)) -> User:
        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is None:
            return user

        granted = set(token_scopes)
        settings = get_settings()

        if not granted and settings.allow_legacy_unscoped_tokens:
            return user

        for required_scope in required_scopes:
            if not has_required_scope(granted, required_scope):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient token scope")

        return user

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
        except Exception:
            db.rollback()
    return user, scopes


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token")


def resolve_client_ip(request: Request) -> str:
    settings = get_settings()
    remote_ip = request.client.host if request.client and request.client.host else "unknown"
    if remote_ip == "unknown":
        return remote_ip

    if not _is_trusted_proxy(remote_ip, settings.trusted_proxy_cidrs):
        return remote_ip

    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return remote_ip

    first_hop = forwarded_for.split(",")[0].strip()
    if not first_hop:
        return remote_ip

    try:
        ip_address(first_hop)
    except ValueError:
        return remote_ip
    return first_hop


def _is_trusted_proxy(remote_ip: str, trusted_proxy_cidrs: list[str]) -> bool:
    if not trusted_proxy_cidrs:
        return False

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
    return False
