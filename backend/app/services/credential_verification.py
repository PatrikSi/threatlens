"""Credential-issuance verification shared by API tokens and MCP delegation.

HTTP adapters provide an immutable accepting-credential context. Callers lock the
current user and applicable policy before invoking password/MFA or session checks.
No service imports request authentication dependencies or route orchestration.
"""

from dataclasses import dataclass, field
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.core.credential_types import AUTH_SESSION_COOKIE
from app.core.security import verify_password
from app.core.token_scopes import missing_delegable_scopes, missing_role_token_scopes
from app.models.user import User
from app.services.authorization import AuthorizationContext
from app.services.auth_sessions import lock_exact_auth_session
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    clear_password_verification_failures,
    record_password_verification_failure,
)
from app.services.local_mfa import MFAError, MFAInvalidCodeError, mfa_status
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)
from app.services.recent_auth import (
    auth_session_has_configured_oidc_mfa_assurance,
    recent_authentication_error_context,
    recent_authentication_state,
)

SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL = (
    "Browser sessions must confirm the current password before creating API tokens"
)
SESSION_TOKEN_SCOPE_DETAIL = (
    "Requested token scopes exceed your current durable permissions"
)


@dataclass(frozen=True)
class CredentialVerificationContext:
    credential_kind: str | None
    credential_id: uuid.UUID | None
    authorization: AuthorizationContext | None
    client_ip: str
    session_token: str | None = field(default=None, repr=False)


def enforce_browser_token_step_up(
    db: Session,
    *,
    context: CredentialVerificationContext,
    user: User,
    current_password: str | None,
    mfa_code: str | None,
) -> str | None:
    if context.credential_kind != AUTH_SESSION_COOKIE:
        return None
    action = "api_token_create"
    session_id = context.credential_id
    session_token = context.session_token
    if session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This legacy browser session cannot create API tokens. Sign out, sign in again, and retry.",
            error_code="opaque_session_required",
            error_context=recent_authentication_error_context(None, action=action),
        )
    session = lock_exact_auth_session(
        db,
        token=session_token,
        expected_session_id=session_id,
        user_id=user.id,
        auth_token_version=int(user.auth_token_version or 0),
    )
    if session is None:
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The current browser session is no longer active. Sign in again.",
            error_code="session_inactive",
        )
    if session.auth_method == "oidc":
        recent = recent_authentication_state(session)
        if not recent.valid:
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Reauthenticate with the identity provider before creating an "
                    "API token."
                ),
                error_code="oidc_reauthentication_required",
                error_context=recent_authentication_error_context(
                    session,
                    action=action,
                ),
            )
        if not auth_session_has_configured_oidc_mfa_assurance(session):
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "The identity provider did not assert the configured MFA assurance. "
                    "Complete MFA during identity-provider reauthentication before "
                    "creating an API token."
                ),
                error_code="oidc_mfa_assurance_required",
                error_context=recent_authentication_error_context(
                    session,
                    action=action,
                ),
            )
        return "oidc_recent_authentication"
    if session.auth_method != "local" or not user.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Browser API token creation requires an account with local password authentication",
        )
    if not current_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL,
        )
    client_ip = context.client_ip
    throttle = check_password_verification_throttle(user.email, client_ip)
    if throttle.blocked:
        detail = (
            "Too many failed current password verification attempts. Try again later."
        )
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
    if not verify_password(current_password, user.password_hash):
        record_password_verification_failure(user.email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    clear_password_verification_failures(
        user.email,
        client_ip,
        observed_failure_version=throttle.failure_version,
    )
    mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=user.id)
    if not mfa_enabled:
        return "local_password"
    if not mfa_code:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Enter a current authenticator or recovery code before creating an API token.",
            error_code="mfa_verification_required",
        )
    try:
        verification = verify_sensitive_mfa_code(
            db,
            user=user,
            code=mfa_code,
            client_ip=client_ip,
        )
    except MFASensitiveActionRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds
                else None
            ),
        ) from exc
    except MFAInvalidCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except MFASensitiveActionThrottleUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shared MFA verification throttling is temporarily unavailable. No MFA code was checked; try again shortly.",
            headers={"Retry-After": "5"},
        ) from exc
    except MFAError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MFA verification is temporarily unavailable. Try again later.",
        ) from exc
    return f"local_password_{verification.method}"


def enforce_delegable_token_scopes(
    context: CredentialVerificationContext, user: User, scopes: list[str]
) -> None:
    authorization = context.authorization
    disallowed_scopes = (
        missing_delegable_scopes(authorization.durable_grants, scopes)
        if authorization is not None
        else missing_role_token_scopes(user.role, scopes)
    )
    if disallowed_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{SESSION_TOKEN_SCOPE_DETAIL}: {', '.join(disallowed_scopes)}",
        )
