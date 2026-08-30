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
from sqlalchemy.exc import SQLAlchemyError
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
from app.models.service_account import ServiceAccount, ServiceAccountCredential
from app.models.user import User
from app.services.auth_sessions import resolve_auth_session, touch_auth_session
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_service_account,
    authorization_context_for_user,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    data_access_context_for_authorization,
    fence_data_access_context,
)
from app.services.service_accounts import (
    SERVICE_ACCOUNT_TOKEN_MARKER,
    extract_service_account_token_prefix,
    hash_service_account_token,
)

AUTH_SESSION_BEARER = "session_bearer"
AUTH_SESSION_COOKIE = "session_cookie"
AUTH_API_TOKEN = "api_token"
AUTH_SERVICE_ACCOUNT_TOKEN = "service_account_token"

AuthenticatedPrincipal = User | ServiceAccount

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/v1/auth/login",
    auto_error=False,
    description=(
        "Use a scoped personal or `tlsa_` service-account token in the Authorization header. Browser sign-in sessions are created at "
        "`/v1/auth/login`, mirrored through the web proxy at `/api/v1/auth/login`, and carried by HttpOnly cookies."
    ),
)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
logger = logging.getLogger(__name__)
_PROXY_HOST_CACHE_TTL_SECONDS = 30.0
_proxy_host_cache_lock = threading.Lock()
_proxy_host_cache: dict[str, tuple[float, frozenset[str]]] = {}


def get_current_principal(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> AuthenticatedPrincipal:
    try:
        principal, credentials_present = _resolve_authenticated_principal(
            request, db, token
        )
    except SQLAlchemyError as exc:
        _raise_auth_state_unavailable(db, request=request, exc=exc)
    if principal is None:
        if getattr(request.state, "auth_credential_kind", None) == AUTH_SESSION_BEARER:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer auth requires a scoped API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        detail = "Invalid credentials" if credentials_present else "Not authenticated"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    try:
        request.state.authorization_context = _authorization_context_for_request(
            request, db, principal
        )
    except SQLAlchemyError as exc:
        _raise_auth_state_unavailable(db, request=request, exc=exc)
    request.state.authenticated_principal = principal
    _set_authenticated_log_context(request, principal)
    return principal


def get_current_user(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> User:
    if isinstance(principal, ServiceAccount):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This endpoint requires a human account. Service-account "
                "credentials cannot create browser sessions or manage user-owned settings."
            ),
            error_code="human_principal_required",
        )
    return principal


def get_optional_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User | None:
    try:
        principal, _credentials_present = _resolve_authenticated_principal(
            request, db, token
        )
    except SQLAlchemyError as exc:
        _raise_auth_state_unavailable(db, request=request, exc=exc)
    if principal is not None:
        try:
            request.state.authorization_context = _authorization_context_for_request(
                request, db, principal
            )
        except SQLAlchemyError as exc:
            _raise_auth_state_unavailable(db, request=request, exc=exc)
        request.state.authenticated_principal = principal
        _set_authenticated_log_context(request, principal)
    return principal if isinstance(principal, User) else None


def get_auth_credential_kind(request: Request) -> str | None:
    return getattr(request.state, "auth_credential_kind", None)


def get_current_auth_session_id(request: Request) -> uuid.UUID | None:
    return getattr(request.state, "auth_session_id", None)


def _current_credential_id(request: Request) -> uuid.UUID | None:
    return (
        getattr(request.state, "service_account_credential_id", None)
        or getattr(request.state, "api_token_id", None)
        or get_current_auth_session_id(request)
    )


def get_authorization_context(request: Request) -> AuthorizationContext | None:
    return getattr(request.state, "authorization_context", None)


def get_data_access_context(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DataAccessContext:
    cached = getattr(request.state, "data_access_context", None)
    authorization = get_authorization_context(request)
    if authorization is None:
        raise DataPolicyUnavailable(
            "Data access policy could not be evaluated because effective access is missing. Retry authentication."
        )
    try:
        fence_authorization_context(db, authorization)
        context = (
            cached
            if isinstance(cached, DataAccessContext)
            else data_access_context_for_authorization(db, authorization)
        )
        fence_data_access_context(db, context)
    except AuthorizationStateUnavailable as exc:
        raise DataPolicyUnavailable(
            "Data access policy could not be evaluated because effective access changed. Retry the request."
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(
            "data_policy_context_load_failed principal_type=%s principal_id=%s",
            authorization.principal_type,
            authorization.principal_id,
            exc_info=verbose_logging_enabled(get_settings()),
        )
        raise DataPolicyUnavailable(
            "Data access policy could not be loaded. Retry the request."
        ) from exc

    request.state.data_access_context = context
    update_log_context(
        data_policy_mode=context.mode,
        data_policy_revision=context.policy_revision,
        data_policy_coverage_version=context.coverage_version,
    )
    return context


def is_cookie_session_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_SESSION_COOKIE


def is_api_token_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_API_TOKEN


def is_service_account_auth(request: Request) -> bool:
    return get_auth_credential_kind(request) == AUTH_SERVICE_ACCOUNT_TOKEN


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
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ) -> AuthenticatedPrincipal:
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
                principal=principal,
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
        elevation_ids = set(getattr(request.state, "authorization_elevation_ids", ()))
        elevation_ids.update(
            authorization.authorizing_elevation_ids(required_permissions)
        )
        resolved_elevation_ids = tuple(sorted(elevation_ids, key=str))
        request.state.authorization_elevation_ids = resolved_elevation_ids
        update_log_context(
            authorization_elevation_ids=(
                ",".join(str(value) for value in resolved_elevation_ids)
                if resolved_elevation_ids
                else None
            )
        )
        return principal

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
    principal: AuthenticatedPrincipal,
    required_permissions: tuple[str, ...],
    missing_permissions: list[str],
    policy_revision: int,
    denial_reasons: dict[str, object],
) -> None:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    authorization = get_authorization_context(request)
    try:
        record_audit(
            db,
            actor_user_id=principal.id if isinstance(principal, User) else None,
            actor_principal_type=(
                authorization.principal_type
                if authorization is not None
                else "user"
                if isinstance(principal, User)
                else "service_account"
            ),
            actor_principal_id=principal.id,
            credential_kind=get_auth_credential_kind(request),
            credential_id=_current_credential_id(request),
            request_id=getattr(request.state, "request_id", None),
            source_ip=resolve_client_ip(request),
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
            "permission_denial_audit_failed principal_type=%s principal_id=%s error_type=%s",
            type(principal).__name__,
            principal.id,
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
    request: Request, db: Session, principal: AuthenticatedPrincipal
) -> AuthorizationContext:
    credential_scopes = getattr(request.state, "token_scopes", None)
    if credential_scopes == [] and get_settings().allow_legacy_unscoped_tokens:
        credential_scopes = None
    try:
        if isinstance(principal, ServiceAccount):
            credential_id = getattr(
                request.state, "service_account_credential_id", None
            )
            if not isinstance(credential_id, uuid.UUID):
                raise AuthorizationStateUnavailable(
                    "Service-account credential context is missing. Retry authentication."
                )
            return authorization_context_for_service_account(
                db,
                principal,
                credential_id=credential_id,
                credential_scopes=credential_scopes or (),
            )
        return authorization_context_for_user(
            db,
            principal,
            credential_scopes=credential_scopes,
        )
    except AuthorizationStateUnavailable as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            error_code="iam_policy_unavailable",
        ) from exc


def _set_authenticated_log_context(
    request: Request, principal: AuthenticatedPrincipal
) -> None:
    update_log_context(
        actor_principal_type=(
            "user" if isinstance(principal, User) else "service_account"
        ),
        actor_principal_id=principal.id,
        credential_kind=get_auth_credential_kind(request),
        credential_id=_current_credential_id(request),
        source_ip=resolve_client_ip(request),
        authorization_elevation_ids=None,
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


def _resolve_service_account(
    request: Request,
    db: Session,
    token: str,
) -> tuple[ServiceAccount, list[str], uuid.UUID] | None:
    prefix = extract_service_account_token_prefix(token)
    if prefix is None:
        _log_service_account_auth_rejection(request, reason="token_format_invalid")
        return None
    token_hash = hash_service_account_token(token)
    now = datetime.now(timezone.utc)
    credential = db.scalar(
        select(ServiceAccountCredential).where(
            ServiceAccountCredential.token_prefix == prefix,
            ServiceAccountCredential.token_hash == token_hash,
        )
    )
    if credential is None:
        _log_service_account_auth_rejection(request, reason="credential_not_found")
        return None
    if credential.revoked_at is not None:
        _log_service_account_auth_rejection(
            request, reason="credential_revoked", credential=credential
        )
        return None
    expires_at = credential.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        _log_service_account_auth_rejection(
            request, reason="credential_expired", credential=credential
        )
        return None
    account = db.get(ServiceAccount, credential.service_account_id)
    if account is None:
        _log_service_account_auth_rejection(
            request, reason="account_missing", credential=credential
        )
        return None
    if not account.is_active:
        _log_service_account_auth_rejection(
            request,
            reason="account_disabled",
            credential=credential,
            account=account,
        )
        return None

    if not isinstance(credential.scopes, list) or any(
        not isinstance(scope, str) for scope in credential.scopes
    ):
        _log_service_account_auth_rejection(
            request,
            reason="credential_scopes_invalid",
            credential=credential,
            account=account,
            error=True,
        )
        return None

    if _should_update_last_used(credential.last_used_at, now):
        credential.last_used_at = now
        credential.last_used_ip = resolve_client_ip(request)
        user_agent = request.headers.get("user-agent", "").strip()
        credential.last_used_user_agent = user_agent[:512] or None
        db.add(credential)
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "service_account_last_used_update_failed credential_id=%s error_type=%s",
                credential.id,
                type(exc).__name__,
                exc_info=verbose_logging_enabled(get_settings()),
            )
    return account, normalize_token_scopes(credential.scopes), credential.id


def _log_service_account_auth_rejection(
    request: Request,
    *,
    reason: str,
    credential: ServiceAccountCredential | None = None,
    account: ServiceAccount | None = None,
    error: bool = False,
) -> None:
    if error:
        log = logger.error
    elif reason in {"token_format_invalid", "credential_not_found"}:
        log = logger.debug
    else:
        log = logger.info
    log(
        "service_account_auth_rejected reason=%s credential_id=%s "
        "service_account_id=%s source_ip=%s",
        reason,
        credential.id if credential is not None else None,
        account.id
        if account is not None
        else credential.service_account_id
        if credential is not None
        else None,
        resolve_client_ip(request),
    )


def _raise_auth_state_unavailable(
    db: Session,
    *,
    request: Request,
    exc: SQLAlchemyError,
) -> None:
    db.rollback()
    logger.exception(
        "authentication_state_lookup_failed credential_kind=%s source_ip=%s "
        "error_type=%s",
        getattr(request.state, "auth_credential_kind", None),
        resolve_client_ip(request),
        type(exc).__name__,
        exc_info=verbose_logging_enabled(get_settings()),
    )
    raise ApiHTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication state is temporarily unavailable. Retry the request.",
        error_code="authentication_state_unavailable",
    ) from exc


def _resolve_authenticated_principal(
    request: Request, db: Session, token: str | None
) -> tuple[AuthenticatedPrincipal | None, bool]:
    request.state.token_scopes = None
    request.state.auth_via_api_token = False
    request.state.auth_via_service_account = False
    request.state.auth_credential_kind = None
    request.state.auth_session_id = None
    request.state.authorization_context = None
    request.state.authenticated_principal = None
    request.state.api_token_id = None
    request.state.service_account_credential_id = None
    token_source = "header"

    if not token:
        token = _resolve_cookie_token(request)
        token_source = "cookie"
    if not token:
        return None, False

    if token_source == "cookie":
        if token.startswith(f"{SERVICE_ACCOUNT_TOKEN_MARKER}_"):
            request.state.auth_credential_kind = AUTH_SERVICE_ACCOUNT_TOKEN
            return None, True
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

    if token.startswith(f"{SERVICE_ACCOUNT_TOKEN_MARKER}_"):
        request.state.auth_credential_kind = AUTH_SERVICE_ACCOUNT_TOKEN
        service_account_result = _resolve_service_account(request, db, token)
        if service_account_result is None:
            return None, True
        account, scopes, credential_id = service_account_result
        request.state.token_scopes = scopes
        request.state.auth_via_service_account = True
        request.state.service_account_credential_id = credential_id
        return account, True

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
