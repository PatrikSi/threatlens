import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    AUTH_API_TOKEN,
    get_auth_credential_kind,
    get_authorization_context,
    get_current_auth_session_id,
    get_current_user,
    is_cookie_session_auth,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.security import (
    clear_auth_cookies,
    clear_mfa_challenge_cookie,
    decode_access_token_claims,
    generate_csrf_token,
    get_password_hash,
    set_auth_cookies,
    set_mfa_challenge_cookie,
    verify_password_and_update,
)
from app.db.session import get_db
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.models.auth_session import AuthSession
from app.schemas.auth import (
    AppFeaturesResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    CurrentAuthenticationResponse,
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
    RegistrationSettingsResponse,
    TokenResponse,
    UserResponse,
)
from app.schemas.auth_security import MFALoginVerifyRequest
from app.api.access_responses import effective_access_response
from app.api.sensitive_action_auth import sensitive_browser_session_readiness
from app.services.audit import record_audit
from app.services.authorization import bump_iam_policy_revision
from app.services.auth_rate_limit import (
    check_login_throttle,
    check_self_registration_throttle,
    clear_login_failures,
    record_login_failure,
    record_self_registration_attempt,
)
from app.services.auth_sessions import (
    auth_session_cookie_ttl_seconds,
    create_auth_session,
    lock_user_auth_state,
    resolve_auth_session,
    revoke_auth_session,
)
from app.services.local_mfa import (
    MFAChallengeError,
    MFAError,
    MFAInvalidCodeError,
    consume_mfa_challenge,
    create_mfa_challenge,
    invalidate_mfa_challenge,
    mfa_status,
)
from app.services.password_verification import verify_current_password_or_raise
from app.services.recent_auth import (
    auth_session_has_configured_oidc_mfa_assurance,
    recent_authentication_state,
)
from app.services.user_access import revoke_user_credentials_with_counts

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_app_features(db: Session | None = None) -> AppFeaturesResponse:
    settings = get_settings()
    if not settings.ai_enabled:
        return AppFeaturesResponse(
            ai_enabled=False,
            ai_configured=False,
            ai_summary_enabled=False,
            ai_relevance_enabled=False,
            ai_daily_brief_enabled=False,
            ai_reporting_enabled=False,
        )

    ai_summary_enabled = True
    ai_relevance_enabled = True
    ai_daily_brief_enabled = True
    ai_reporting_enabled = True
    ai_configured = False
    if db is not None:
        from app.services.ai_config import load_public_ai_feature_flags

        flags = load_public_ai_feature_flags(db)
        ai_summary_enabled = flags.ai_summary_enabled
        ai_relevance_enabled = flags.ai_relevance_enabled
        ai_daily_brief_enabled = flags.ai_daily_brief_enabled
        ai_reporting_enabled = flags.ai_reporting_enabled
        ai_configured = flags.ai_configured

    return AppFeaturesResponse(
        ai_enabled=True,
        ai_configured=ai_configured,
        ai_summary_enabled=ai_summary_enabled,
        ai_relevance_enabled=ai_relevance_enabled,
        ai_daily_brief_enabled=ai_daily_brief_enabled,
        ai_reporting_enabled=ai_reporting_enabled,
    )


@router.get("/registration-settings", response_model=RegistrationSettingsResponse)
def registration_settings():
    settings = get_settings()
    return RegistrationSettingsResponse(
        allow_self_registration=settings.allow_self_registration,
        ai_enabled=settings.ai_enabled,
    )


@router.post("/register", response_model=UserResponse)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.allow_self_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is disabled",
        )

    email = payload.email.lower()
    client_ip = resolve_client_ip(request)
    throttle = check_self_registration_throttle(email, client_ip)
    if throttle.blocked:
        detail = "Too many self-registration attempts. Try again later."
        headers = (
            {"Retry-After": str(throttle.retry_after_seconds)}
            if throttle.retry_after_seconds
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers=headers,
        )
    record_self_registration_attempt(email, client_ip)

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
        )

    user = User(
        email=email,
        password_hash=get_password_hash(payload.password),
        is_active=True,
        is_approved=False,
        approved_at=None,
    )
    db.add(user)
    try:
        db.flush()
        bump_iam_policy_revision(db)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
        ) from exc
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.register",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"email": user.email, "is_approved": user.is_approved},
    )
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse, response_model_exclude_none=True)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Create a browser session cookie and return only cookie-session metadata.

    Scoped API access should use dedicated API tokens in the `Authorization` header.
    Browser clients should rely on the HttpOnly session cookie set by this route.
    """
    email = payload.email.lower()
    client_ip = resolve_client_ip(request)
    throttle = check_login_throttle(email, client_ip)
    if throttle.blocked:
        detail = "Too many failed login attempts. Try again later."
        headers = (
            {"Retry-After": str(throttle.retry_after_seconds)}
            if throttle.retry_after_seconds
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers=headers,
        )

    user = db.scalar(select(User).where(User.email == email))
    password_valid = False
    replacement_hash = None
    if user is not None and user.password_login_enabled:
        password_valid, replacement_hash = verify_password_and_update(
            payload.password,
            user.password_hash,
        )
    if user is None or not user.password_login_enabled or not password_valid:
        record_login_failure(email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    verified_password_hash = user.password_hash
    user = lock_user_auth_state(db, user.id)
    if user is None or user.email != email or not user.password_login_enabled:
        record_login_failure(email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if user.password_hash != verified_password_hash:
        password_valid, replacement_hash = verify_password_and_update(
            payload.password,
            user.password_hash,
        )
        if not password_valid:
            record_login_failure(email, client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )

    if replacement_hash is not None:
        user.password_hash = replacement_hash
        db.add(user)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    mfa_enabled, _confirmed_at, _recovery_codes = mfa_status(db, user_id=user.id)
    if mfa_enabled:
        challenge = create_mfa_challenge(
            db,
            user_id=user.id,
            auth_token_version=user.auth_token_version,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent"),
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="auth.login.mfa_challenge",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"email": user.email},
        )
        db.commit()
        clear_auth_cookies(response)
        set_mfa_challenge_cookie(response, challenge.token)
        return TokenResponse(mfa_required=True)

    created_session = create_auth_session(
        db,
        user_id=user.id,
        auth_token_version=user.auth_token_version,
        auth_method="local",
        mfa_method=None,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    csrf_token = generate_csrf_token()
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "email": user.email,
            "auth_method": "local",
            "session_id": str(created_session.session.id),
        },
    )
    db.commit()
    clear_login_failures(
        email,
        client_ip,
        observed_failure_version=throttle.failure_version,
    )
    set_auth_cookies(
        response,
        created_session.token,
        csrf_token,
        max_age_seconds=auth_session_cookie_ttl_seconds(created_session.session),
    )
    return TokenResponse(csrf_token=csrf_token)


@router.post(
    "/mfa/verify", response_model=TokenResponse, response_model_exclude_none=True
)
def verify_mfa_login(
    payload: MFALoginVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    challenge_token = request.cookies.get(settings.auth_mfa_challenge_cookie_name)
    if not challenge_token:
        raise _mfa_login_http_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The MFA sign-in challenge is missing or expired. Start sign-in again.",
            error_code="mfa_challenge_missing",
        )

    try:
        challenge, verification = consume_mfa_challenge(
            db,
            token=challenge_token,
            code=payload.code,
        )
    except MFAInvalidCodeError as exc:
        failed_user = (
            db.scalar(select(User).where(User.id == exc.user_id))
            if exc.user_id
            else None
        )
        client_ip = resolve_client_ip(request)
        if failed_user is not None:
            record_login_failure(failed_user.email, client_ip)
        throttle = (
            check_login_throttle(failed_user.email, client_ip)
            if failed_user is not None
            else None
        )
        throttled = bool(throttle and throttle.blocked)
        if throttled:
            invalidate_mfa_challenge(db, token=challenge_token)
        record_audit(
            db,
            actor_user_id=exc.user_id,
            action="auth.login.mfa_verify",
            resource_type="user",
            resource_id=str(exc.user_id) if exc.user_id else None,
            success=False,
            metadata={
                "attempts_remaining": exc.attempts_remaining,
                "throttled": throttled,
            },
        )
        db.commit()
        remaining = exc.attempts_remaining
        if throttled:
            headers = (
                {"Retry-After": str(throttle.retry_after_seconds)}
                if throttle.retry_after_seconds
                else None
            )
            raise _mfa_login_http_error(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed sign-in attempts. Start sign-in again after the lockout expires.",
                headers=headers,
                error_code="mfa_login_rate_limited",
            ) from exc
        if remaining == 0:
            raise _mfa_login_http_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Too many failed MFA attempts. Start sign-in again.",
                error_code="mfa_challenge_attempts_exhausted",
            ) from exc
        suffix = (
            f" {remaining} attempt{'s' if remaining != 1 else ''} remain."
            if remaining is not None
            else ""
        )
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"The authenticator or recovery code is invalid or has already been used.{suffix}",
            error_code="mfa_code_invalid",
        ) from exc
    except MFAChallengeError as exc:
        db.commit()
        raise _mfa_login_http_error(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            error_code=exc.code,
        ) from exc
    except MFAError as exc:
        db.rollback()
        invalidate_mfa_challenge(db, token=challenge_token)
        db.commit()
        raise _mfa_login_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MFA verification is temporarily unavailable. Contact an administrator if the problem continues.",
            error_code="mfa_verification_unavailable",
        ) from exc

    user = db.scalar(select(User).where(User.id == challenge.user_id))
    if (
        user is None
        or not user.is_active
        or not user.is_approved
        or not user.password_login_enabled
    ):
        record_audit(
            db,
            actor_user_id=user.id if user else None,
            action="auth.login.mfa_verify",
            resource_type="user",
            resource_id=str(challenge.user_id),
            success=False,
            metadata={"reason": "account_unavailable"},
        )
        db.commit()
        raise _mfa_login_http_error(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account is no longer available for local sign-in.",
            error_code="account_unavailable",
        )

    created_session = create_auth_session(
        db,
        user_id=user.id,
        auth_token_version=user.auth_token_version,
        auth_method="local",
        mfa_method=verification.method,
        client_ip=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    csrf_token = generate_csrf_token()
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "email": user.email,
            "auth_method": "local",
            "mfa_method": verification.method,
            "session_id": str(created_session.session.id),
            "recovery_codes_remaining": verification.recovery_codes_remaining,
        },
    )
    db.commit()
    success_throttle = check_login_throttle(user.email, resolve_client_ip(request))
    clear_login_failures(
        user.email,
        resolve_client_ip(request),
        observed_failure_version=success_throttle.failure_version,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    clear_mfa_challenge_cookie(response)
    set_auth_cookies(
        response,
        created_session.token,
        csrf_token,
        max_age_seconds=auth_session_cookie_ttl_seconds(created_session.session),
    )
    return TokenResponse(csrf_token=csrf_token)


def _mfa_login_http_error(
    *,
    status_code: int,
    detail: str,
    headers: dict[str, str] | None = None,
    error_code: str | None = None,
) -> HTTPException:
    cookie_response = Response()
    clear_mfa_challenge_cookie(cookie_response)
    error_headers = dict(headers or {})
    set_cookie = cookie_response.headers.get("set-cookie")
    if set_cookie:
        error_headers["Set-Cookie"] = set_cookie
    if error_code:
        return ApiHTTPException(
            status_code=status_code,
            detail=detail,
            error_code=error_code,
            headers=error_headers or None,
        )
    return HTTPException(status_code=status_code, detail=detail, headers=error_headers or None)


@router.get("/me", response_model=CurrentUserResponse)
def me(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorization = get_authorization_context(request)
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Effective access could not be resolved. Retry the request.",
        )
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        is_approved=user.is_approved,
        approved_at=user.approved_at,
        created_at=user.created_at,
        password_login_enabled=user.password_login_enabled,
        provisioning_source=user.provisioning_source,
        features=_resolve_app_features(db),
        authentication=_current_authentication_response(request, db, user),
        access=effective_access_response(authorization),
    )


def _current_authentication_response(
    request: Request,
    db: Session,
    user: User,
) -> CurrentAuthenticationResponse:
    if get_auth_credential_kind(request) == AUTH_API_TOKEN:
        return CurrentAuthenticationResponse(
            credential_kind="api_token",
            sensitive_actions_blocker="browser_session_required",
        )

    session_id = get_current_auth_session_id(request)
    if session_id is None:
        return CurrentAuthenticationResponse(
            credential_kind="legacy_session",
            sensitive_actions_blocker="opaque_session_required",
        )
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user.id,
            AuthSession.auth_token_version == int(user.auth_token_version or 0),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None:
        return CurrentAuthenticationResponse(
            credential_kind="legacy_session",
            sensitive_actions_blocker="opaque_session_required",
        )
    recent = recent_authentication_state(session)
    sensitive_readiness = sensitive_browser_session_readiness(
        db,
        user=user,
        session=session,
    )
    return CurrentAuthenticationResponse(
        credential_kind="opaque_session",
        session_id=session.id,
        session_auth_method=session.auth_method,
        mfa_method=session.mfa_method,
        recently_authenticated=recent.valid,
        recent_authentication_valid=recent.valid,
        recent_authentication_expires_at=recent.valid_until,
        identity_provider_mfa_asserted=auth_session_has_configured_oidc_mfa_assurance(
            session
        ),
        reauthentication_endpoint=recent.reauthentication_endpoint,
        security_actions_supported=True,
        sensitive_actions_ready=sensitive_readiness.ready,
        sensitive_actions_blocker=sensitive_readiness.blocker,
    )


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    status_code=status.HTTP_200_OK,
)
def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changing a password requires an authenticated browser session.",
        )
    user = lock_user_auth_state(db, user.id)
    if user is None or not user.is_active or not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account is no longer available for password changes.",
        )
    if user.provisioning_source == PROVISIONING_SOURCE_OIDC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password is managed by the identity provider for this SSO-provisioned account",
        )
    if not user.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local password authentication is not configured for this account",
        )
    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )

    user.password_hash = get_password_hash(payload.new_password)
    user.password_login_enabled = True
    revoked = revoke_user_credentials_with_counts(db, user)
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.change_password",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "revoked_api_tokens": revoked.api_tokens,
            "revoked_auth_sessions": revoked.auth_sessions,
        },
    )
    db.commit()
    clear_auth_cookies(response)
    return ChangePasswordResponse(
        revoked_api_tokens=revoked.api_tokens,
        revoked_auth_sessions=revoked.auth_sessions,
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    auth_cookie = request.cookies.get(settings.auth_cookie_name)
    if auth_cookie and settings.auth_require_csrf:
        csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
        csrf_header = request.headers.get(settings.auth_csrf_header_name)
        if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing or invalid CSRF token",
            )

    is_opaque_cookie = bool(auth_cookie and auth_cookie.startswith("tls_"))
    opaque_session = resolve_auth_session(db, auth_cookie) if is_opaque_cookie else None
    user = None
    if opaque_session is not None:
        user = lock_user_auth_state(db, opaque_session.user_id)
        opaque_session = resolve_auth_session(db, auth_cookie or "")
    if user is not None and opaque_session is not None:
        revoked = revoke_auth_session(
            db,
            session_id=opaque_session.id,
            user_id=user.id,
            reason="logout",
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="auth.logout",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"session_id": str(opaque_session.id), "session_revoked": revoked},
        )
        db.commit()
    elif not is_opaque_cookie:
        candidate = _resolve_logout_session_user(db, auth_cookie)
        if candidate is not None:
            lock_user_auth_state(db, candidate.id)
            user = _resolve_logout_session_user(db, auth_cookie)
    if user is not None and not is_opaque_cookie:
        user.auth_token_version = int(user.auth_token_version or 0) + 1
        db.add(user)
        record_audit(
            db,
            actor_user_id=user.id,
            action="auth.logout",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"legacy_sessions_revoked": True},
        )
        db.commit()
    if opaque_session is None and is_opaque_cookie:
        db.commit()

    clear_auth_cookies(response)
    clear_mfa_challenge_cookie(response)
    return {"status": "ok"}


def _resolve_logout_session_user(db: Session, auth_cookie: str | None) -> User | None:
    if not auth_cookie:
        return None

    claims = decode_access_token_claims(auth_cookie)
    if claims is None:
        return None

    subject = claims.get("sub")
    if not subject:
        return None

    try:
        user_id = uuid.UUID(subject)
        token_version = int(claims.get("ver", 0))
    except (TypeError, ValueError):
        return None

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None
    if token_version != int(user.auth_token_version or 0):
        return None
    return user
