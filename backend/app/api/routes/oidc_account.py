from __future__ import annotations

from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_auth_session_id,
    get_current_user,
    is_cookie_session_auth,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.security import generate_csrf_token, set_auth_cookies
from app.db.session import get_db
from app.models.oidc import ExternalIdentity
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.schemas.oidc import OIDCAccountStatusResponse, OIDCUnlinkRequest
from app.services.audit import record_audit
from app.services.auth_sessions import (
    auth_session_cookie_ttl_seconds,
    lock_user_auth_state,
    rotate_user_auth_sessions,
)
from app.services.local_mfa import MFAError, MFAInvalidCodeError, mfa_status
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)
from app.services.oidc_config import load_primary_oidc_provider
from app.services.oidc_identity import OIDCIdentityError, unlink_oidc_identity
from app.services.password_verification import verify_current_password_or_raise

router = APIRouter()


@router.get("/account", response_model=OIDCAccountStatusResponse)
def oidc_account_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = load_primary_oidc_provider(db)
    identity = None
    if provider is not None:
        identity = db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider_id == provider.id,
                ExternalIdentity.user_id == user.id,
            )
        )
    return OIDCAccountStatusResponse(
        available=bool(provider and provider.enabled),
        provider_name=provider.name if provider else None,
        linked=identity is not None,
        linked_email=identity.email_at_link if identity else None,
        linked_at=identity.created_at if identity else None,
        password_login_enabled=user.password_login_enabled,
    )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def unlink_oidc_account(
    payload: OIDCUnlinkRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OIDC account unlinking requires an authenticated browser session.",
            error_code="browser_session_required",
        )
    user = lock_user_auth_state(db, user.id)
    if user is None or not user.is_active or not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account is no longer available for identity changes.",
        )
    provider = load_primary_oidc_provider(db)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC provider is not configured",
        )
    if user.provisioning_source == PROVISIONING_SOURCE_OIDC:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO-provisioned accounts cannot unlink their managed sign-in identity",
        )
    if not user.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set a local password before unlinking the only external sign-in method",
        )
    verify_current_password_or_raise(
        user=user,
        candidate_password=payload.current_password,
        client_ip=resolve_client_ip(request),
    )
    local_mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=user.id)
    mfa_method = None
    if local_mfa_enabled:
        if not payload.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter a current authenticator or recovery code before unlinking SSO.",
            )
        try:
            verification = verify_sensitive_mfa_code(
                db,
                user=user,
                code=payload.code,
                client_ip=resolve_client_ip(request),
            )
        except MFAError as exc:
            _raise_unlink_mfa_error(
                db, user=user, provider_id=str(provider.id), exc=exc
            )
        mfa_method = verification.method
    try:
        identity = unlink_oidc_identity(db, provider, user)
    except OIDCIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    rotated = rotate_user_auth_sessions(
        db,
        user=user,
        current_session_id=get_current_auth_session_id(request),
        reason="oidc_identity_unlinked",
        default_auth_method="local",
        mfa_method=mfa_method,
        preserve_current_auth_method=False,
        client_ip=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="oidc.identity.unlink",
        resource_type="external_identity",
        resource_id=str(identity.id),
        metadata={
            "provider_id": str(provider.id),
            "local_mfa_verified": local_mfa_enabled,
            "mfa_method": mfa_method,
            "revoked_auth_sessions": rotated.revoked_sessions,
            "revoked_other_sessions": rotated.revoked_other_sessions,
            "session_rotated": True,
        },
    )
    db.commit()
    set_auth_cookies(
        response,
        rotated.created.token,
        generate_csrf_token(),
        max_age_seconds=auth_session_cookie_ttl_seconds(rotated.created.session),
    )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_204_NO_CONTENT


def _raise_unlink_mfa_error(
    db: Session,
    *,
    user: User,
    provider_id: str,
    exc: MFAError,
) -> Never:
    user_id = user.id
    db.rollback()
    reason = (
        "rate_limited"
        if isinstance(exc, MFASensitiveActionRateLimitError)
        else "throttle_unavailable"
        if isinstance(exc, MFASensitiveActionThrottleUnavailableError)
        else "invalid_code"
        if isinstance(exc, MFAInvalidCodeError)
        else "verification_unavailable"
    )
    record_audit(
        db,
        actor_user_id=user_id,
        action="oidc.identity.unlink",
        resource_type="user",
        resource_id=str(user_id),
        success=False,
        metadata={"provider_id": provider_id, "reason": reason},
    )
    db.commit()
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
    if isinstance(exc, MFAInvalidCodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MFA verification is temporarily unavailable. Try again later.",
    ) from exc
