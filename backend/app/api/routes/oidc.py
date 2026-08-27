from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
import hmac
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_auth_session_id,
    get_current_user,
    is_cookie_session_auth,
    resolve_client_ip,
)
from app.api.routes.oidc_account import router as account_router
from app.api.routes.oidc_provider import router as provider_router
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.security import generate_csrf_token, set_auth_cookies
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import User
from app.schemas.oidc import (
    OIDCLinkStartRequest,
    OIDCReauthenticationStartResponse,
    OIDCStartResponse,
)
from app.services.audit import record_audit
from app.services.auth_sessions import (
    AuthSessionStateError,
    auth_session_cookie_ttl_seconds,
    create_auth_session,
    extract_auth_session_id,
    lock_exact_auth_session,
    lock_user_auth_state,
    rotate_exact_auth_session,
    rotate_user_auth_sessions,
)
from app.services.oidc_client import (
    OIDCClaims,
    OIDCProtocolError,
    build_oidc_authorization_url,
    exchange_oidc_code,
    load_oidc_metadata,
    oidc_failure_reason,
    oidc_identity_authenticated_at,
    validate_oidc_reauthentication,
    validate_oidc_token_claims,
)
from app.services.oidc_config import (
    OIDCConfigurationError,
    load_primary_oidc_provider,
)
from app.services.oidc_identity import (
    OIDCAuthenticationResult,
    OIDCIdentityError,
    authenticate_oidc_identity,
    link_oidc_identity,
)
from app.services.oidc_transaction import (
    OIDCTransaction,
    clear_oidc_transaction_cookie,
    decode_oidc_transaction,
    new_oidc_transaction,
    oidc_session_binding,
    set_oidc_transaction_cookie,
)
from app.services.auth_rate_limit import (
    check_oidc_callback_throttle,
    record_invalid_oidc_callback,
)
from app.services.local_mfa import MFAError, MFAInvalidCodeError, mfa_status
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    verify_sensitive_mfa_code,
)
from app.services.password_verification import verify_current_password_or_raise
from app.services.recent_auth import (
    configured_oidc_mfa_assurance_matches,
    recent_authentication_error_context,
)
from app.services.user_access import (
    acquire_active_admin_invariant_lock,
    acquire_oidc_provider_config_read_lock,
)

session_router = APIRouter()
logger = logging.getLogger("threatlens.oidc")


@session_router.get("/login")
def start_oidc_login(request: Request, db: Session = Depends(get_db)):
    try:
        return _start_oidc_flow(db, mode="login")
    except HTTPException as exc:
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE and _accepts_html(
            request
        ):
            return _callback_redirect(
                load_primary_oidc_provider(db),
                "/login",
                {"oidc_error": "provider_unavailable"},
            )
        raise


@session_router.post("/link", response_model=OIDCStartResponse)
def start_oidc_link(
    request: Request,
    response: Response,
    payload: OIDCLinkStartRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC account linking requires a browser session",
        )
    if not user.password_login_enabled or not payload or not payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter your current ThreatLens password before linking an SSO identity.",
        )
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    current_session_id = get_current_auth_session_id(request)
    if not session_token or current_session_id is None:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This legacy browser session cannot link an SSO identity. "
                "Sign out, sign in again, and retry."
            ),
            error_code="opaque_session_required",
        )
    locked_user = lock_user_auth_state(db, user.id)
    resolved_session = (
        lock_exact_auth_session(
            db,
            token=session_token,
            expected_session_id=current_session_id,
            user_id=locked_user.id,
            auth_token_version=int(locked_user.auth_token_version or 0),
        )
        if locked_user is not None
        else None
    )
    if (
        locked_user is None
        or not locked_user.is_active
        or not locked_user.is_approved
        or resolved_session is None
        or resolved_session.user_id != locked_user.id
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account security changed while linking started. Sign in and try again.",
            error_code="session_inactive",
        )
    user = locked_user
    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )
    local_mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=user.id)
    if local_mfa_enabled:
        if not payload.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter a current 6-digit authenticator code before linking an SSO identity.",
            )
        try:
            verify_sensitive_mfa_code(
                db,
                user=user,
                code=payload.code,
                client_ip=resolve_client_ip(request),
                allow_recovery_code=False,
            )
        except MFAError as exc:
            _raise_oidc_link_mfa_error(db, user=user, exc=exc)
    verified_auth_token_version = int(user.auth_token_version or 0)
    db.commit()
    authorization_url, transaction = _prepare_oidc_flow(
        db,
        mode="link",
        user=user,
        session_binding=oidc_session_binding(session_token),
        auth_token_version=verified_auth_token_version,
    )
    locked_user = lock_user_auth_state(db, user.id)
    resolved_session = (
        lock_exact_auth_session(
            db,
            token=session_token,
            expected_session_id=current_session_id,
            user_id=user.id,
            auth_token_version=verified_auth_token_version,
        )
        if locked_user is not None
        and int(locked_user.auth_token_version or 0) == verified_auth_token_version
        else None
    )
    if resolved_session is None:
        db.rollback()
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account security changed while linking started. Sign in and try again.",
            error_code="session_inactive",
        )
    record_audit(
        db,
        actor_user_id=user.id,
        action="oidc.identity.link.start",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "session_id": (
                str(get_current_auth_session_id(request))
                if get_current_auth_session_id(request)
                else None
            ),
            "local_mfa_verified": local_mfa_enabled,
            "provider_reauthentication_required": True,
        },
    )
    db.commit()
    set_oidc_transaction_cookie(response, transaction)
    response.headers["Cache-Control"] = "no-store"
    return OIDCStartResponse(authorization_url=authorization_url)


@session_router.post(
    "/reauth",
    response_model=OIDCReauthenticationStartResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "`browser_session_required` or `opaque_session_required`."
        },
        status.HTTP_409_CONFLICT: {
            "description": "`oidc_session_required` when the current opaque session was not authenticated with OIDC."
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "`oidc_provider_unavailable` when no enabled provider can perform reauthentication."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "`oidc_reauthentication_start_failed` when the provider flow cannot be prepared."
        },
    },
)
def start_oidc_reauthentication(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OIDC reauthentication requires an authenticated browser session.",
            error_code="browser_session_required",
            error_context=recent_authentication_error_context(
                None,
                action="oidc_reauthentication",
            ),
        )
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    session_id = get_current_auth_session_id(request)
    if not session_token or session_id is None:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This legacy browser session cannot start OIDC reauthentication. Sign out and sign in again.",
            error_code="opaque_session_required",
        )
    locked_user = lock_user_auth_state(db, user.id)
    session = (
        lock_exact_auth_session(
            db,
            token=session_token,
            expected_session_id=session_id,
            user_id=user.id,
            auth_token_version=int(locked_user.auth_token_version or 0),
        )
        if locked_user is not None
        else None
    )
    if session is None or session.auth_method != "oidc":
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sign in with OIDC before starting identity-provider reauthentication.",
            error_code="oidc_session_required",
            error_context=recent_authentication_error_context(
                session,
                action="oidc_reauthentication",
            ),
        )
    auth_token_version = session.auth_token_version
    error_context = recent_authentication_error_context(
        session,
        action="oidc_reauthentication",
    )
    db.rollback()
    try:
        authorization_url, transaction = _prepare_oidc_flow(
            db,
            mode="reauth",
            user=user,
            session_binding=oidc_session_binding(session_token),
            auth_token_version=auth_token_version,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise ApiHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="OIDC reauthentication is not available because no provider is enabled.",
                error_code="oidc_provider_unavailable",
                error_context=error_context,
            ) from exc
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            raise ApiHTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc.detail),
                error_code="oidc_reauthentication_start_failed",
                error_context=error_context,
                headers=exc.headers,
            ) from exc
        raise
    locked_user = lock_user_auth_state(db, user.id)
    session = (
        lock_exact_auth_session(
            db,
            token=session_token,
            expected_session_id=session_id,
            user_id=user.id,
            auth_token_version=auth_token_version,
        )
        if locked_user is not None
        and int(locked_user.auth_token_version or 0) == auth_token_version
        else None
    )
    if session is None or session.auth_method != "oidc":
        db.rollback()
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account security changed while reauthentication started. Sign in again.",
            error_code="session_inactive",
        )
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.oidc.reauthenticate.start",
        resource_type="auth_session",
        resource_id=str(session.id),
        metadata={"provider_reauthentication_required": True},
    )
    db.commit()
    set_oidc_transaction_cookie(response, transaction)
    response.headers["Cache-Control"] = "no-store"
    return OIDCReauthenticationStartResponse(authorization_url=authorization_url)


@session_router.get(
    "/callback",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    responses={
        status.HTTP_302_FOUND: {
            "description": (
                "Redirects with `oidc_error`, `oidc_link`, or `oidc_reauth`. "
                "The typed code inventory is returned by `/auth/oidc/settings`."
            )
        }
    },
)
def oidc_callback(
    request: Request,
    state_value: str | None = Query(default=None, alias="state", max_length=2048),
    code: str | None = Query(default=None, max_length=8192),
    provider_error: str | None = Query(default=None, alias="error", max_length=256),
    db: Session = Depends(get_db),
):
    client_ip = resolve_client_ip(request)
    transaction = decode_oidc_transaction(request, state_value)
    callback_throttle = check_oidc_callback_throttle(client_ip)
    if callback_throttle.blocked:
        provider = _provider_for_transaction(
            db,
            transaction.provider_id if transaction else None,
        )
        provider = _detach_provider(db, provider)
        return _callback_failure(
            db,
            provider,
            "callback_rate_limited",
            response_mode=transaction.mode if transaction else "login",
            persist_audit=False,
        )
    provider = _provider_for_transaction(
        db, transaction.provider_id if transaction else None
    )
    if transaction is None:
        record_invalid_oidc_callback(client_ip)
        provider = _detach_provider(db, provider)
        return _callback_failure(
            db,
            provider,
            "invalid_state",
            response_mode="login",
            persist_audit=False,
        )
    if (
        provider is None
        or not provider.enabled
        or transaction.provider_config_revision is None
        or provider.config_revision != transaction.provider_config_revision
    ):
        provider = _detach_provider(db, provider)
        return _callback_failure(
            db,
            provider,
            "provider_configuration_changed",
            response_mode=transaction.mode,
        )
    provider = _detach_provider(db, provider)
    if provider_error:
        return _callback_failure(
            db, provider, "provider_rejected", response_mode=transaction.mode
        )
    if not code:
        return _callback_failure(
            db, provider, "missing_code", response_mode=transaction.mode
        )

    claims: OIDCClaims | None = None
    try:
        metadata = load_oidc_metadata(provider)
        token = exchange_oidc_code(
            provider, metadata, code=code, code_verifier=transaction.code_verifier
        )
        claims = validate_oidc_token_claims(
            provider, metadata, token, nonce=transaction.nonce
        )
        role_sync_lock_held = bool(
            transaction.mode == "login" and provider.sync_roles_on_login
        )
        if role_sync_lock_held:
            acquire_active_admin_invariant_lock(db)
        acquire_oidc_provider_config_read_lock(db)
        fenced_provider = _lock_provider_for_callback(db, transaction)
        if fenced_provider is None:
            raise OIDCIdentityError(
                "provider_configuration_changed",
                "OIDC provider settings changed while sign-in was in progress",
            )
        provider = fenced_provider
        identity_authenticated_at = oidc_identity_authenticated_at(claims)
        identity_acr, identity_amr = _oidc_identity_assurance(claims)
        external_mfa_asserted = configured_oidc_mfa_assurance_matches(
            identity_acr=identity_acr,
            identity_amr=identity_amr,
        )
        if transaction.earliest_auth_time is not None:
            try:
                identity_authenticated_at = validate_oidc_reauthentication(
                    claims,
                    earliest_auth_time=transaction.earliest_auth_time,
                )
            except OIDCProtocolError as exc:
                raise OIDCIdentityError(
                    "reauthentication_failed",
                    "The identity provider did not complete the required recent authentication",
                ) from exc
        if transaction.mode == "link":
            user = _resolve_bound_opaque_session_user(
                db,
                request,
                transaction.user_id,
                expected_session_binding=transaction.session_binding,
                expected_auth_token_version=transaction.auth_token_version,
            )
            if user is None:
                raise OIDCIdentityError(
                    "link_session_expired",
                    "The account-linking session is no longer valid",
                )
            identity = link_oidc_identity(db, provider, user, claims)
            session_token = request.cookies.get(get_settings().auth_cookie_name) or ""
            current_session_id = extract_auth_session_id(session_token)
            current_session = (
                db.get(AuthSession, current_session_id)
                if current_session_id is not None
                else None
            )
            linked_mfa_method = (
                "external"
                if external_mfa_asserted
                else current_session.mfa_method
                if current_session is not None
                else None
            )
            rotated = rotate_user_auth_sessions(
                db,
                user=user,
                current_session_id=current_session_id,
                reason="oidc_identity_linked",
                default_auth_method="local",
                mfa_method=linked_mfa_method,
                identity_authenticated_at=identity_authenticated_at,
                identity_acr=identity_acr,
                identity_amr=identity_amr,
                client_ip=resolve_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            record_audit(
                db,
                actor_user_id=user.id,
                action="oidc.identity.link",
                resource_type="external_identity",
                resource_id=str(identity.id),
                metadata={
                    "provider_id": str(provider.id),
                    "issuer": claims.issuer,
                    "provider_reauthenticated": identity_authenticated_at is not None,
                    "external_mfa_asserted": linked_mfa_method == "external",
                    "revoked_auth_sessions": rotated.revoked_sessions,
                    "session_rotated": True,
                },
            )
            db.commit()
            response = _callback_redirect(
                provider, "/settings/account", {"oidc_link": "success"}
            )
            set_auth_cookies(
                response,
                rotated.created.token,
                generate_csrf_token(),
                max_age_seconds=auth_session_cookie_ttl_seconds(
                    rotated.created.session
                ),
            )
            return response

        if transaction.mode == "reauth":
            user = _resolve_bound_opaque_session_user(
                db,
                request,
                transaction.user_id,
                expected_session_binding=transaction.session_binding,
                expected_auth_token_version=transaction.auth_token_version,
            )
            if user is None:
                raise OIDCIdentityError(
                    "reauth_session_expired",
                    "The reauthentication session is no longer valid",
                )
            _verify_reauthentication_identity(db, provider, user, claims)
            session_token = request.cookies.get(get_settings().auth_cookie_name) or ""
            current_session_id = extract_auth_session_id(session_token)
            if current_session_id is None or identity_authenticated_at is None:
                raise OIDCIdentityError(
                    "reauth_session_expired",
                    "The reauthentication session is no longer valid",
                )
            try:
                rotated = rotate_exact_auth_session(
                    db,
                    user=user,
                    current_session_id=current_session_id,
                    reason="oidc_reauthenticated",
                    auth_method="oidc",
                    mfa_method=("external" if external_mfa_asserted else None),
                    authenticated_at=identity_authenticated_at,
                    identity_authenticated_at=identity_authenticated_at,
                    identity_acr=identity_acr,
                    identity_amr=identity_amr,
                    client_ip=resolve_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                )
            except AuthSessionStateError as exc:
                raise OIDCIdentityError(
                    "reauth_session_expired",
                    "The reauthentication session is no longer valid",
                    user_id=str(user.id),
                ) from exc
            record_audit(
                db,
                actor_user_id=user.id,
                action="auth.oidc.reauthenticate",
                resource_type="auth_session",
                resource_id=str(rotated.created.session.id),
                metadata={
                    "provider_id": str(provider.id),
                    "external_mfa_asserted": external_mfa_asserted,
                    "acr": identity_acr,
                    "amr": identity_amr,
                    "revoked_auth_sessions": rotated.revoked_sessions,
                    "session_rotated": True,
                },
            )
            db.commit()
            response = _callback_redirect(
                provider, "/settings/account", {"oidc_reauth": "success"}
            )
            set_auth_cookies(
                response,
                rotated.created.token,
                generate_csrf_token(),
                max_age_seconds=auth_session_cookie_ttl_seconds(
                    rotated.created.session
                ),
            )
            return response

        result = authenticate_oidc_identity(
            db,
            provider,
            claims,
            active_admin_invariant_locked=role_sync_lock_held,
        )
        _record_oidc_authentication_audit(db, provider, result)
        if not result.user.is_approved:
            record_audit(
                db,
                actor_user_id=result.user.id,
                action="auth.oidc.login",
                resource_type="user",
                resource_id=str(result.user.id),
                success=False,
                metadata={
                    "provider_id": str(provider.id),
                    "error_code": "approval_required",
                },
            )
            db.commit()
            return _callback_redirect(
                provider, "/login", {"oidc_error": "approval_required"}
            )
        if not result.user.is_active:
            record_audit(
                db,
                actor_user_id=result.user.id,
                action="auth.oidc.login",
                resource_type="user",
                resource_id=str(result.user.id),
                success=False,
                metadata={
                    "provider_id": str(provider.id),
                    "error_code": "account_inactive",
                },
            )
            db.commit()
            return _callback_redirect(
                provider, "/login", {"oidc_error": "account_inactive"}
            )

        created_session = create_auth_session(
            db,
            user_id=result.user.id,
            auth_token_version=result.user.auth_token_version,
            auth_method="oidc",
            mfa_method=("external" if external_mfa_asserted else None),
            identity_authenticated_at=identity_authenticated_at,
            identity_acr=identity_acr,
            identity_amr=identity_amr,
            client_ip=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="auth.oidc.login",
            resource_type="user",
            resource_id=str(result.user.id),
            metadata={
                "provider_id": str(provider.id),
                "provisioned": result.provisioned,
                "auth_method": "oidc",
                "session_id": str(created_session.session.id),
            },
        )
        db.commit()
        response = _callback_redirect(provider, "/", {})
        set_auth_cookies(
            response,
            created_session.token,
            generate_csrf_token(),
            max_age_seconds=auth_session_cookie_ttl_seconds(created_session.session),
        )
        return response
    except OIDCIdentityError as exc:
        db.rollback()
        claim_diagnostics = _identity_claim_diagnostics(claims)
        logger.warning(
            "oidc_identity_failed provider_id=%s mode=%s error_code=%s claim_diagnostics=%s",
            provider.id,
            transaction.mode,
            exc.code,
            claim_diagnostics,
        )
        failure_details: dict[str, object] = {"claim_diagnostics": claim_diagnostics}
        failure_details.update(exc.details)
        return _callback_failure(
            db,
            provider,
            exc.code,
            response_mode=transaction.mode,
            actor_user_id=exc.user_id or transaction.user_id,
            details=failure_details,
        )
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
        db.rollback()
        logger.warning(
            "oidc_callback_failed provider_id=%s error_type=%s",
            provider.id,
            type(exc).__name__,
        )
        return _callback_failure(
            db, provider, "authentication_failed", response_mode=transaction.mode
        )
    except Exception:
        db.rollback()
        logger.exception("oidc_callback_unexpected_failure provider_id=%s", provider.id)
        return _callback_failure(
            db, provider, "authentication_failed", response_mode=transaction.mode
        )


def _start_oidc_flow(
    db: Session, *, mode: str, user: User | None = None
) -> RedirectResponse:
    authorization_url, transaction = _prepare_oidc_flow(db, mode=mode, user=user)
    response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
    set_oidc_transaction_cookie(response, transaction)
    response.headers["Cache-Control"] = "no-store"
    return response


def _raise_oidc_link_mfa_error(
    db: Session,
    *,
    user: User,
    exc: MFAError,
) -> None:
    db.rollback()
    reason = (
        "rate_limited"
        if isinstance(exc, MFASensitiveActionRateLimitError)
        else "invalid_code"
        if isinstance(exc, MFAInvalidCodeError)
        else "verification_unavailable"
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="oidc.identity.link.start",
        resource_type="user",
        resource_id=str(user.id),
        success=False,
        metadata={"reason": reason},
    )
    db.commit()
    if isinstance(exc, MFASensitiveActionRateLimitError):
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=headers,
        ) from exc
    if isinstance(exc, MFAInvalidCodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MFA verification is temporarily unavailable. Try again later.",
    ) from exc


def _prepare_oidc_flow(
    db: Session,
    *,
    mode: str,
    user: User | None = None,
    session_binding: str | None = None,
    auth_token_version: int | None = None,
) -> tuple[str, OIDCTransaction]:
    actor_user_id = user.id if user is not None else None
    provider = load_primary_oidc_provider(db)
    if provider is None or not provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC sign-in is not available",
        )
    provider = _detach_provider(db, provider)
    try:
        metadata = load_oidc_metadata(provider)
        current_time = datetime.now(timezone.utc)
        requires_reauthentication = mode in {"link", "reauth"}
        max_age_seconds = 0 if requires_reauthentication else None
        earliest_auth_time = (
            int(current_time.timestamp()) if requires_reauthentication else None
        )
        transaction = new_oidc_transaction(
            provider_id=str(provider.id),
            provider_config_revision=provider.config_revision,
            mode=(
                "link" if mode == "link" else "reauth" if mode == "reauth" else "login"
            ),
            user_id=str(actor_user_id) if actor_user_id else None,
            session_binding=session_binding,
            auth_token_version=auth_token_version,
            earliest_auth_time=earliest_auth_time,
        )
        authorization_url = build_oidc_authorization_url(
            provider,
            metadata,
            state=transaction.state,
            nonce=transaction.nonce,
            code_verifier=transaction.code_verifier,
            max_age_seconds=max_age_seconds,
            prompt="login" if requires_reauthentication else None,
        )
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
        reason = oidc_failure_reason(exc)
        logger.warning(
            "oidc_start_failed provider_id=%s mode=%s error_type=%s reason=%s",
            provider.id,
            mode,
            type(exc).__name__,
            reason,
        )
        record_audit(
            db,
            actor_user_id=actor_user_id,
            action="auth.oidc.start",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"mode": mode, "error_type": type(exc).__name__, "reason": reason},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"OIDC sign-in could not start: {reason}",
        ) from exc
    except Exception as exc:
        logger.exception(
            "oidc_start_unexpected_failure provider_id=%s mode=%s", provider.id, mode
        )
        record_audit(
            db,
            actor_user_id=actor_user_id,
            action="auth.oidc.start",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"mode": mode, "error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC sign-in is temporarily unavailable; contact an administrator",
        ) from exc

    return authorization_url, transaction


def _accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def _provider_for_transaction(
    db: Session, provider_id: str | None
) -> OIDCProvider | None:
    if not provider_id:
        return load_primary_oidc_provider(db)
    try:
        parsed_id = uuid.UUID(provider_id)
    except ValueError:
        return None
    return db.scalar(select(OIDCProvider).where(OIDCProvider.id == parsed_id))


def _detach_provider(db: Session, provider: OIDCProvider | None) -> OIDCProvider | None:
    if provider is not None:
        db.expunge(provider)
    db.rollback()
    return provider


def _detached_primary_provider(db: Session) -> OIDCProvider | None:
    return _detach_provider(db, load_primary_oidc_provider(db))


def _lock_provider_for_callback(
    db: Session, transaction: OIDCTransaction
) -> OIDCProvider | None:
    try:
        provider_id = uuid.UUID(transaction.provider_id)
    except (TypeError, ValueError):
        return None
    provider = db.scalar(
        select(OIDCProvider)
        .where(OIDCProvider.id == provider_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        provider is None
        or not provider.enabled
        or transaction.provider_config_revision is None
        or provider.config_revision != transaction.provider_config_revision
    ):
        return None
    return provider


def _resolve_bound_opaque_session_user(
    db: Session,
    request: Request,
    expected_user_id: str | None,
    *,
    expected_session_binding: str | None,
    expected_auth_token_version: int | None,
) -> User | None:
    settings = get_settings()
    session_token = request.cookies.get(settings.auth_cookie_name)
    if (
        not session_token
        or not expected_session_binding
        or not expected_user_id
        or expected_auth_token_version is None
        or not hmac.compare_digest(
            oidc_session_binding(session_token), expected_session_binding
        )
    ):
        return None
    try:
        user_id = uuid.UUID(expected_user_id)
    except (TypeError, ValueError):
        return None
    user = lock_user_auth_state(db, user_id)
    if (
        user is None
        or not user.is_active
        or not user.is_approved
        or int(user.auth_token_version or 0) != expected_auth_token_version
    ):
        return None
    session_id = extract_auth_session_id(session_token)
    if session_id is None:
        return None
    session = lock_exact_auth_session(
        db,
        token=session_token,
        expected_session_id=session_id,
        user_id=user.id,
        auth_token_version=expected_auth_token_version,
    )
    if session is None:
        return None
    return user


def _record_oidc_authentication_audit(
    db: Session,
    provider: OIDCProvider,
    result: OIDCAuthenticationResult,
) -> None:
    if result.provisioned:
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="oidc.user.provision",
            resource_type="user",
            resource_id=str(result.user.id),
            metadata={
                "provider_id": str(provider.id),
                "role": result.user.role,
                "is_approved": result.user.is_approved,
            },
        )
    if result.previous_role is not None:
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="oidc.role.sync",
            resource_type="user",
            resource_id=str(result.user.id),
            metadata={
                "provider_id": str(provider.id),
                "previous_role": result.previous_role,
                "role": result.user.role,
                "revoked_api_tokens": result.revoked_api_tokens,
                "revoked_auth_sessions": result.revoked_auth_sessions,
                "cleared_investigation_assignments": result.cleared_investigation_assignments,
            },
        )
    if result.role_sync_skipped:
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="oidc.role.sync",
            resource_type="user",
            resource_id=str(result.user.id),
            success=False,
            metadata={
                "provider_id": str(provider.id),
                "reason": result.role_sync_skipped,
            },
        )


def _callback_failure(
    db: Session,
    provider: OIDCProvider | None,
    error_code: str,
    *,
    response_mode: str,
    actor_user_id: str | None = None,
    details: dict[str, object] | None = None,
    persist_audit: bool = True,
) -> RedirectResponse:
    parsed_actor_id: uuid.UUID | None = None
    if actor_user_id:
        try:
            parsed_actor_id = uuid.UUID(actor_user_id)
        except ValueError:
            parsed_actor_id = None
    audit_metadata: dict[str, object] = {
        "error_code": error_code,
        "mode": response_mode,
    }
    if details:
        audit_metadata.update(details)
    if persist_audit:
        record_audit(
            db,
            actor_user_id=parsed_actor_id,
            action="auth.oidc.callback",
            resource_type="oidc_provider",
            resource_id=str(provider.id) if provider else None,
            success=False,
            metadata=audit_metadata,
        )
        db.commit()
    target_path = (
        "/settings/account" if response_mode in {"link", "reauth"} else "/login"
    )
    query_key = (
        "oidc_link"
        if response_mode == "link"
        else "oidc_reauth"
        if response_mode == "reauth"
        else "oidc_error"
    )
    return _callback_redirect(provider, target_path, {query_key: error_code})


def _oidc_identity_assurance(claims: OIDCClaims) -> tuple[str | None, list[str]]:
    acr_claim = claims.claims.get("acr")
    acr = acr_claim.strip()[:255] if isinstance(acr_claim, str) else None
    amr_claim = claims.claims.get("amr")
    amr = (
        list(
            dict.fromkeys(
                value.strip().lower()
                for value in amr_claim
                if isinstance(value, str) and value.strip()
            )
        )[:32]
        if isinstance(amr_claim, list)
        else []
    )
    return acr or None, amr


def _verify_reauthentication_identity(
    db: Session,
    provider: OIDCProvider,
    user: User,
    claims: OIDCClaims,
) -> None:
    identity = db.scalar(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.provider_id == provider.id,
            ExternalIdentity.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        identity is None
        or identity.issuer != claims.issuer
        or identity.subject != claims.subject
    ):
        raise OIDCIdentityError(
            "reauth_identity_mismatch",
            "Reauthentication must use the same linked identity as the current account",
            user_id=str(user.id),
        )


def _identity_claim_diagnostics(claims: OIDCClaims | None) -> dict[str, object]:
    if claims is None:
        return {"claims_available": False}

    email = claims.claims.get("email")
    email_verified = claims.claims.get("email_verified")
    return {
        "claims_available": True,
        "email_claim_present": "email" in claims.claims,
        "email_value_present": isinstance(email, str) and bool(email.strip()),
        "email_claim_type": type(email).__name__ if email is not None else None,
        "email_verified_claim_present": "email_verified" in claims.claims,
        "email_verified": email_verified if isinstance(email_verified, bool) else None,
        "email_verified_claim_type": type(email_verified).__name__
        if email_verified is not None
        else None,
    }


def _callback_redirect(
    provider: OIDCProvider | None, path: str, query: dict[str, str]
) -> RedirectResponse:
    base_url = provider.public_base_url.rstrip("/") if provider else ""
    target = f"{base_url}{path}"
    if query:
        target = f"{target}?{urlencode(query)}"
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    clear_oidc_transaction_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


router = APIRouter(prefix="/auth/oidc", tags=["auth", "oidc"])
router.include_router(provider_router)
router.include_router(session_router)
router.include_router(account_router)
