from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_auth_session_id,
    get_current_user,
    is_cookie_session_auth,
    resolve_client_ip,
)
from app.core.config import get_settings
from app.core.api_errors import ApiHTTPException
from app.core.security import (
    clear_auth_cookies,
    generate_csrf_token,
    set_auth_cookies,
)
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.schemas.auth_security import (
    AuthSessionListResponse,
    AuthSessionResponse,
    RecentAuthenticationRequest,
    RecentAuthenticationResponse,
    SessionBulkRevocationResponse,
    SessionRevocationResponse,
    TOTPConfirmRequest,
    TOTPEnrollmentCancelResponse,
    TOTPDisableResponse,
    TOTPEnrollmentStartRequest,
    TOTPEnrollmentStartResponse,
    TOTPRecoveryCodesResponse,
    TOTPSensitiveActionRequest,
    TOTPStatusResponse,
)
from app.services.audit import record_audit
from app.services.auth_sessions import (
    CreatedAuthSession,
    AuthSessionStateError,
    auth_session_cookie_ttl_seconds,
    list_user_auth_sessions,
    lock_exact_auth_session,
    lock_user_auth_state,
    revoke_auth_session,
    rotate_exact_auth_session,
    rotate_user_auth_sessions,
)
from app.services.local_mfa import (
    MFAConflictError,
    MFAEnrollmentExpiredError,
    MFAError,
    MFAInvalidCodeError,
    confirm_totp_enrollment,
    cancel_pending_totp_enrollment,
    disable_totp,
    mfa_status,
    regenerate_recovery_codes,
    start_totp_enrollment,
)
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)
from app.services.password_verification import verify_current_password_or_raise
from app.services.recent_auth import (
    recent_authentication_error_context,
    recent_authentication_state,
)

router = APIRouter(prefix="/auth/security", tags=["auth", "security"])


@router.get("/sessions", response_model=AuthSessionListResponse)
def list_sessions(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_browser_session(request)
    _set_no_store(response)
    current_session_id = get_current_auth_session_id(request)
    inventory = list_user_auth_sessions(
        db,
        user_id=user.id,
        current_session_id=current_session_id,
    )
    return AuthSessionListResponse(
        sessions=[
            AuthSessionResponse(
                id=session.id,
                current=session.id == current_session_id,
                auth_method=session.auth_method,
                mfa_method=session.mfa_method,
                client_ip=session.client_ip,
                user_agent=session.user_agent,
                authenticated_at=session.authenticated_at,
                last_seen_at=session.last_seen_at,
                idle_expires_at=session.idle_expires_at,
                absolute_expires_at=session.absolute_expires_at,
                revoked_at=session.revoked_at,
                revoked_reason=session.revoked_reason,
            )
            for session in inventory.sessions
        ],
        active_count=inventory.active_count,
        active_truncated=inventory.active_truncated,
        history_truncated=inventory.history_truncated,
    )


@router.delete("/sessions/{session_id}", response_model=SessionRevocationResponse)
def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_browser_session(request)
    user = _lock_current_user_auth_state(db, user)
    current_session = _require_current_opaque_session(db, request=request, user=user)
    current_session_id = current_session.id
    if session_id != current_session_id:
        _require_recent_current_session(
            db,
            request=request,
            user=user,
            current_session=current_session,
        )
    revoked = revoke_auth_session(
        db,
        session_id=session_id,
        user_id=user.id,
        reason="user_revoked",
    )
    current_revoked = revoked and session_id == current_session_id
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.session.revoke",
        resource_type="auth_session",
        resource_id=str(session_id),
        metadata={
            "revoked": revoked,
            "current_session": current_revoked,
            "revoked_session_count": int(revoked),
            "other_sessions_revoked": 0,
            "auth_generation_rotated": False,
        },
    )
    db.commit()
    if current_revoked:
        clear_auth_cookies(response)
    return SessionRevocationResponse(
        revoked=revoked,
        current_session_revoked=current_revoked,
        revoked_session_count=int(revoked),
        other_sessions_revoked=0,
        auth_generation_rotated=False,
    )


@router.post("/sessions/revoke-others", response_model=SessionBulkRevocationResponse)
def revoke_other_sessions(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_browser_session(request)
    user = _lock_current_user_auth_state(db, user)
    current_session = _require_recent_current_session(db, request=request, user=user)
    rotated = rotate_user_auth_sessions(
        db,
        user=user,
        current_session_id=current_session.id,
        reason="user_revoked_others",
        default_auth_method=(
            "oidc" if user.provisioning_source == PROVISIONING_SOURCE_OIDC else "local"
        ),
        mfa_method=None,
        preserve_current_mfa_method=True,
        preserve_current_identity_authentication=True,
        preserve_current_identity_assurance=True,
        preserve_current_timing=True,
        client_ip=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.session.revoke_others",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "revoked_auth_sessions": rotated.revoked_sessions,
            "revoked_other_sessions": rotated.revoked_other_sessions,
            "session_rotated": True,
        },
    )
    db.commit()
    _set_rotated_auth_cookies(response, rotated.created)
    return SessionBulkRevocationResponse(revoked_count=rotated.revoked_other_sessions)


@router.post(
    "/reauthenticate",
    response_model=RecentAuthenticationResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": (
                "`browser_session_required`, `mfa_verification_required`, or an invalid recent-auth proof."
            )
        },
        status.HTTP_409_CONFLICT: {
            "description": "`oidc_reauthentication_required` or `local_reauthentication_unavailable`."
        },
    },
)
def reauthenticate_local_session(
    payload: RecentAuthenticationRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local reauthentication requires an authenticated browser session.",
            error_code="browser_session_required",
            error_context=recent_authentication_error_context(
                None,
                action="local_recent_authentication",
            ),
        )
    user = _lock_current_user_auth_state(db, user)
    current_session = _require_current_opaque_session(db, request=request, user=user)
    if current_session.auth_method != "local":
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This browser session was authenticated by the identity provider. "
                "Use OIDC reauthentication for this security operation."
            ),
            error_code="oidc_reauthentication_required",
            error_context=recent_authentication_error_context(
                current_session,
                action="local_recent_authentication",
            ),
        )
    if (
        user.provisioning_source == PROVISIONING_SOURCE_OIDC
        or not user.password_login_enabled
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Local password reauthentication is not available for this account.",
            error_code="local_reauthentication_unavailable",
            error_context=recent_authentication_error_context(
                current_session,
                action="local_recent_authentication",
            ),
        )

    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )
    mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=user.id)
    verification_method = "password"
    mfa_method = None
    if mfa_enabled:
        if not payload.code:
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Enter a current authenticator code to complete local reauthentication. "
                    "Recovery codes are not accepted for this operation."
                ),
                error_code="mfa_verification_required",
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
            _raise_sensitive_mfa_error(
                db,
                user=user,
                action="auth.reauthenticate.local",
                exc=exc,
            )
        verification_method = "password_totp"
        mfa_method = "totp"

    authenticated_at = datetime.now(timezone.utc)
    try:
        rotated = rotate_exact_auth_session(
            db,
            user=user,
            current_session_id=current_session.id,
            reason="local_reauthenticated",
            auth_method="local",
            mfa_method=mfa_method,
            authenticated_at=authenticated_at,
            identity_authenticated_at=current_session.identity_authenticated_at,
            identity_acr=current_session.identity_acr,
            identity_amr=list(current_session.identity_amr_json or []),
            client_ip=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            now=authenticated_at,
        )
    except AuthSessionStateError as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The browser session changed during reauthentication. Sign in again.",
            error_code="session_inactive",
        ) from exc
    state = recent_authentication_state(
        rotated.created.session,
        now=authenticated_at,
    )
    if state.authenticated_at is None or state.valid_until is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Recent authentication could not be recorded.",
        )
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.reauthenticate.local",
        resource_type="auth_session",
        resource_id=str(rotated.created.session.id),
        metadata={
            "verification_method": verification_method,
            "session_rotated": True,
        },
    )
    db.commit()
    _set_no_store(response)
    _set_rotated_auth_cookies(response, rotated.created)
    return RecentAuthenticationResponse(
        verification_method=verification_method,
        session_id=rotated.created.session.id,
        authenticated_at=state.authenticated_at,
        valid_until=state.valid_until,
    )


@router.get("/mfa", response_model=TOTPStatusResponse)
def get_mfa_status(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_browser_session(request)
    _set_no_store(response)
    enabled, confirmed_at, recovery_codes_remaining = mfa_status(db, user_id=user.id)
    available = (
        user.provisioning_source != PROVISIONING_SOURCE_OIDC
        and user.password_login_enabled
    )
    return TOTPStatusResponse(
        local_mfa_available=available,
        managed_by="local" if available else "identity_provider",
        enabled=enabled,
        confirmed_at=confirmed_at,
        recovery_codes_remaining=recovery_codes_remaining,
    )


@router.post("/mfa/enroll", response_model=TOTPEnrollmentStartResponse)
def enroll_totp(
    payload: TOTPEnrollmentStartRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user, current_session = _lock_local_mfa_account(request, db, user)
    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )
    try:
        enrollment = start_totp_enrollment(
            db,
            user=user,
            enrollment_session_id=current_session.id,
            enrollment_auth_token_version=current_session.auth_token_version,
            issuer=get_settings().auth_totp_issuer,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="auth.mfa.enrollment.start",
            resource_type="user",
            resource_id=str(user.id),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Another authenticator enrollment started at the same time. "
                "Refresh security settings and use the newest enrollment."
            ),
        ) from exc
    except MFAError as exc:
        db.rollback()
        _raise_mfa_http_error(exc)
    _set_no_store(response)
    return TOTPEnrollmentStartResponse(
        secret=enrollment.secret,
        provisioning_uri=enrollment.provisioning_uri,
    )


@router.post("/mfa/confirm", response_model=TOTPRecoveryCodesResponse)
def confirm_totp(
    payload: TOTPConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user, current_session = _lock_local_mfa_account(request, db, user)
    try:
        confirmation = confirm_totp_enrollment(
            db,
            user_id=user.id,
            code=payload.code,
            enrollment_session_id=current_session.id,
            enrollment_auth_token_version=current_session.auth_token_version,
        )
    except MFAEnrollmentExpiredError as exc:
        db.commit()
        _raise_mfa_http_error(exc)
    except MFAError as exc:
        db.rollback()
        _raise_mfa_http_error(exc)
    generated_at = confirmation.credential.recovery_codes_generated_at
    if generated_at is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Recovery codes could not be generated. MFA enrollment was not enabled.",
        )
    rotated = rotate_user_auth_sessions(
        db,
        user=user,
        current_session_id=current_session.id,
        reason="mfa_enabled",
        default_auth_method="local",
        mfa_method="totp",
        preserve_current_identity_authentication=True,
        client_ip=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.mfa.enable",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "revoked_auth_sessions": rotated.revoked_sessions,
            "revoked_other_sessions": rotated.revoked_other_sessions,
            "session_rotated": True,
        },
    )
    db.commit()
    _set_no_store(response)
    _set_rotated_auth_cookies(response, rotated.created)
    return TOTPRecoveryCodesResponse(
        recovery_codes=confirmation.recovery_codes,
        generated_at=generated_at,
    )


@router.delete(
    "/mfa/enrollment",
    response_model=TOTPEnrollmentCancelResponse,
)
def cancel_totp_enrollment(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user, _current_session = _lock_local_mfa_account(request, db, user)
    cancelled = cancel_pending_totp_enrollment(db, user_id=user.id)
    if cancelled:
        record_audit(
            db,
            actor_user_id=user.id,
            action="auth.mfa.enrollment.cancel",
            resource_type="user",
            resource_id=str(user.id),
        )
    db.commit()
    return TOTPEnrollmentCancelResponse(cancelled=cancelled)


@router.post("/mfa/recovery-codes", response_model=TOTPRecoveryCodesResponse)
def replace_recovery_codes(
    payload: TOTPSensitiveActionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user, _current_session = _lock_local_mfa_account(request, db, user)
    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )
    try:
        verification = verify_sensitive_mfa_code(
            db,
            user=user,
            code=payload.code,
            client_ip=resolve_client_ip(request),
            allow_recovery_code=False,
        )
        confirmation = regenerate_recovery_codes(db, user_id=user.id)
    except MFAError as exc:
        _raise_sensitive_mfa_error(
            db,
            user=user,
            action="auth.mfa.recovery_codes.regenerate",
            exc=exc,
        )
    recovery_codes = confirmation.recovery_codes
    generated_at = confirmation.credential.recovery_codes_generated_at
    if generated_at is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Recovery codes could not be generated. Existing recovery codes remain valid.",
        )
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.mfa.recovery_codes.regenerate",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"verification_method": verification.method},
    )
    db.commit()
    _set_no_store(response)
    return TOTPRecoveryCodesResponse(
        recovery_codes=recovery_codes, generated_at=generated_at
    )


@router.delete("/mfa", response_model=TOTPDisableResponse)
def remove_totp(
    payload: TOTPSensitiveActionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user, current_session = _lock_local_mfa_account(request, db, user)
    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )
    try:
        verification = verify_sensitive_mfa_code(
            db,
            user=user,
            code=payload.code,
            client_ip=resolve_client_ip(request),
        )
        disabled = disable_totp(db, user_id=user.id)
    except MFAError as exc:
        _raise_sensitive_mfa_error(
            db,
            user=user,
            action="auth.mfa.disable",
            exc=exc,
        )
    rotated = rotate_user_auth_sessions(
        db,
        user=user,
        current_session_id=current_session.id,
        reason="mfa_disabled",
        default_auth_method="local",
        mfa_method=None,
        preserve_current_identity_authentication=True,
        client_ip=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.mfa.disable",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "disabled": disabled,
            "verification_method": verification.method,
            "revoked_auth_sessions": rotated.revoked_sessions,
            "revoked_other_sessions": rotated.revoked_other_sessions,
            "session_rotated": True,
        },
    )
    db.commit()
    _set_rotated_auth_cookies(response, rotated.created)
    return TOTPDisableResponse(
        disabled=disabled,
        revoked_sessions=rotated.revoked_other_sessions,
    )


def _require_browser_session(request: Request) -> None:
    if not is_cookie_session_auth(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This security operation requires an authenticated browser session.",
        )


def _lock_current_user_auth_state(db: Session, user: User) -> User:
    locked_user = lock_user_auth_state(db, user.id)
    if locked_user is None or not locked_user.is_active or not locked_user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account is no longer available for this security operation.",
        )
    return locked_user


def _lock_local_mfa_account(
    request: Request, db: Session, user: User
) -> tuple[User, AuthSession]:
    _require_browser_session(request)
    user = _lock_current_user_auth_state(db, user)
    if (
        user.provisioning_source == PROVISIONING_SOURCE_OIDC
        or not user.password_login_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA for this SSO-managed account is controlled by the identity provider.",
        )
    return user, _require_current_opaque_session(db, request=request, user=user)


def _require_current_opaque_session(
    db: Session,
    *,
    request: Request,
    user: User,
) -> AuthSession:
    current_session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    if current_session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This legacy browser session cannot perform this security operation. "
                "Sign out, sign in again, and retry."
            ),
            error_code="opaque_session_required",
        )
    session = lock_exact_auth_session(
        db,
        token=session_token,
        expected_session_id=current_session_id,
        user_id=user.id,
        auth_token_version=int(user.auth_token_version or 0),
    )
    if session is None:
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The current browser session is no longer active. Sign in again.",
            error_code="session_inactive",
        )
    return session


def _require_recent_current_session(
    db: Session,
    *,
    request: Request,
    user: User,
    current_session: AuthSession | None = None,
) -> AuthSession:
    session = current_session or _require_current_opaque_session(
        db, request=request, user=user
    )
    recent = recent_authentication_state(session)
    if not recent.valid:
        error_code = (
            "oidc_reauthentication_required"
            if session.auth_method == "oidc"
            else "local_reauthentication_required"
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Recent identity-provider authentication is required."
                if session.auth_method == "oidc"
                else "Recent local authentication is required."
            ),
            error_code=error_code,
            error_context=recent_authentication_error_context(
                session,
                action="session_revocation",
            ),
        )
    return session


def _raise_mfa_http_error(exc: MFAError) -> None:
    if isinstance(exc, MFASensitiveActionThrottleUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Shared MFA verification throttling is temporarily unavailable. "
                "No MFA code was checked; try again shortly."
            ),
            headers={"Retry-After": "5"},
        ) from exc
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
    if isinstance(exc, MFAEnrollmentExpiredError):
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code="mfa_enrollment_restart_required",
        ) from exc
    if isinstance(exc, MFAConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if isinstance(exc, MFAInvalidCodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MFA settings are temporarily unavailable. Contact an administrator if the problem continues.",
    ) from exc


def _raise_sensitive_mfa_error(
    db: Session,
    *,
    user: User,
    action: str,
    exc: MFAError,
) -> Never:
    db.rollback()
    record_audit(
        db,
        actor_user_id=user.id,
        action=action,
        resource_type="user",
        resource_id=str(user.id),
        success=False,
        metadata={
            "reason": (
                "rate_limited"
                if isinstance(exc, MFASensitiveActionRateLimitError)
                else "throttle_unavailable"
                if isinstance(exc, MFASensitiveActionThrottleUnavailableError)
                else "invalid_code"
                if isinstance(exc, MFAInvalidCodeError)
                else "verification_unavailable"
            )
        },
    )
    db.commit()
    _raise_mfa_http_error(exc)


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _set_rotated_auth_cookies(
    response: Response,
    created_session: CreatedAuthSession,
) -> None:
    set_auth_cookies(
        response,
        created_session.token,
        generate_csrf_token(),
        max_age_seconds=auth_session_cookie_ttl_seconds(created_session.session),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
