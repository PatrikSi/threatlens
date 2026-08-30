from __future__ import annotations

from fastapi import Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_auth_session_id,
    is_cookie_session_auth,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.auth_sessions import lock_exact_auth_session
from app.services.local_mfa import mfa_status
from app.services.recent_auth import (
    auth_session_has_configured_oidc_mfa_assurance,
    recent_authentication_error_context,
    recent_authentication_state,
)


def require_sensitive_browser_session(
    db: Session,
    *,
    request: Request,
    user: User,
    action: str,
    operation_label: str,
) -> AuthSession:
    """Require a locked, recent browser session with applicable MFA assurance."""
    if not is_cookie_session_auth(request):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{operation_label} requires a recently authenticated browser session. "
                "Personal API tokens and other credential types cannot approve this action."
            ),
            error_code="browser_session_required",
            error_context=recent_authentication_error_context(None, action=action),
        )
    session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    if session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This legacy browser session cannot perform {operation_label}. "
                "Sign out, sign in again, and retry."
            ),
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
                f"Reauthenticate with the identity provider before {operation_label}."
                if session.auth_method == "oidc"
                else f"Confirm your local identity before {operation_label}."
            ),
            error_code=error_code,
            error_context=recent_authentication_error_context(session, action=action),
        )

    if session.auth_method == "oidc":
        if not auth_session_has_configured_oidc_mfa_assurance(session):
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "The identity provider did not assert the configured MFA assurance. "
                    f"Complete MFA during identity-provider reauthentication before {operation_label}."
                ),
                error_code="oidc_mfa_assurance_required",
                error_context=recent_authentication_error_context(
                    session, action=action
                ),
            )
        return session

    local_mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=user.id)
    if local_mfa_enabled and session.mfa_method != "totp":
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Sign in with your configured authenticator before {operation_label}."
            ),
            error_code="mfa_verification_required",
            error_context=recent_authentication_error_context(session, action=action),
        )
    return session


__all__ = ["require_sensitive_browser_session"]
