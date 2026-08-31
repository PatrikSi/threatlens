from __future__ import annotations

import logging
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
from app.core.config import get_settings
from app.core.security import generate_csrf_token, set_auth_cookies
from app.db.session import get_db
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.schemas.oidc import OIDCAccountStatusResponse, OIDCUnlinkRequest
from app.services.audit import record_audit
from app.services.auth_sessions import (
    auth_session_cookie_ttl_seconds,
    create_auth_session,
    lock_exact_auth_session,
    lock_user_auth_state,
    rotate_user_auth_sessions,
)
from app.services.authorization import (
    bump_iam_policy_revision,
    lock_iam_policy_for_mutation,
)
from app.services.local_mfa import MFAError, MFAInvalidCodeError, mfa_status
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)
from app.services.oidc_access_lifecycle import (
    OIDCAccessPurgeBlocked,
    provider_oidc_source_keys,
    purge_oidc_access,
)
from app.services.oidc_config import (
    OIDC_PROVIDER_SYSTEM_KEY,
    load_primary_oidc_provider,
)
from app.services.oidc_identity import OIDCIdentityError, unlink_oidc_identity
from app.services.oidc_role_provenance import (
    OIDCRoleReversionBlocked,
    revert_oidc_synchronized_role,
)
from app.services.password_verification import verify_current_password_or_raise
from app.services.user_access import (
    acquire_active_admin_invariant_lock,
    acquire_oidc_provider_config_read_lock,
    revoke_user_credentials_with_counts,
)

router = APIRouter()
logger = logging.getLogger("threatlens.oidc.account")


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
    lock_iam_policy_for_mutation(db)
    acquire_active_admin_invariant_lock(db)
    acquire_oidc_provider_config_read_lock(db)
    provider = db.scalar(
        select(OIDCProvider)
        .where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY)
        .with_for_update(read=True)
    )
    user = lock_user_auth_state(db, user.id)
    if user is None or not user.is_active or not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account is no longer available for identity changes.",
        )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC provider is not configured",
        )
    current_session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    if current_session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This legacy browser session cannot unlink SSO. Sign out, sign in "
                "again, and retry."
            ),
            error_code="opaque_session_required",
        )
    if (
        lock_exact_auth_session(
            db,
            token=session_token,
            expected_session_id=current_session_id,
            user_id=user.id,
            auth_token_version=int(user.auth_token_version or 0),
        )
        is None
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The browser session is no longer active. Sign in again.",
            error_code="session_inactive",
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
    try:
        role_reversion = revert_oidc_synchronized_role(
            db,
            identity=identity,
            user=user,
            actor_user_id=user.id,
            allow_legacy_retention=False,
        )
    except OIDCRoleReversionBlocked as exc:
        _raise_role_reversion_blocked(
            db,
            request=request,
            user_id=user.id,
            provider_id=provider.id,
            exc=exc,
        )
    fixed_role_iam_revision = (
        bump_iam_policy_revision(db) if role_reversion.changed else None
    )
    role_source_keys, group_source_keys = provider_oidc_source_keys(db, provider.id)
    try:
        access_purge = purge_oidc_access(
            db,
            role_source_keys=role_source_keys,
            group_source_keys=group_source_keys,
            user_ids=[user.id],
            actor_user_id=user.id,
            revocation_reason="oidc_identity_unlinked",
        )
    except OIDCAccessPurgeBlocked as exc:
        _raise_access_purge_blocked(
            db,
            request=request,
            user_id=user.id,
            provider_id=provider.id,
            exc=exc,
        )

    role_revocation = (
        revoke_user_credentials_with_counts(
            db,
            user,
            reason="oidc_role_management_removed",
        )
        if role_reversion.changed
        else None
    )
    if user.id in access_purge.access_reduced_user_ids or role_reversion.changed:
        created = create_auth_session(
            db,
            user_id=user.id,
            auth_token_version=int(user.auth_token_version or 0),
            auth_method="local",
            mfa_method=mfa_method,
            client_ip=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        revoked_sessions = access_purge.revoked_auth_sessions + (
            role_revocation.auth_sessions if role_revocation is not None else 0
        )
        revoked_other_sessions = max(
            0, revoked_sessions - (1 if current_session_id is not None else 0)
        )
    else:
        rotated = rotate_user_auth_sessions(
            db,
            user=user,
            current_session_id=current_session_id,
            reason="oidc_identity_unlinked",
            default_auth_method="local",
            mfa_method=mfa_method,
            preserve_current_auth_method=False,
            client_ip=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        created = rotated.created
        revoked_sessions = rotated.revoked_sessions
        revoked_other_sessions = rotated.revoked_other_sessions
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
            "purged_role_assignments": access_purge.removed_role_assignments,
            "purged_group_memberships": access_purge.removed_group_memberships,
            "fixed_role_reverted": role_reversion.changed,
            "fixed_role_before": role_reversion.previous_role,
            "fixed_role_after": role_reversion.resulting_role,
            "fixed_role_manual_override": role_reversion.manual_override,
            "access_reduced": user.id in access_purge.access_reduced_user_ids,
            "revoked_api_tokens": access_purge.revoked_api_tokens
            + (role_revocation.api_tokens if role_revocation is not None else 0),
            "revoked_auth_sessions": revoked_sessions,
            "revoked_other_sessions": revoked_other_sessions,
            "current_session_revoked": True,
            "cancelled_pending_mfa_enrollments": (
                access_purge.cancelled_pending_mfa_enrollments
            ),
            "cleared_investigation_assignments": (
                access_purge.cleared_investigation_assignments
                + role_reversion.cleared_investigation_assignments
            ),
            "session_rotated": True,
            "iam_policy_revision": (
                access_purge.iam_policy_revision or fixed_role_iam_revision
            ),
        },
    )
    db.commit()
    set_auth_cookies(
        response,
        created.token,
        generate_csrf_token(),
        max_age_seconds=auth_session_cookie_ttl_seconds(created.session),
    )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_204_NO_CONTENT


def _raise_role_reversion_blocked(
    db: Session,
    *,
    request: Request,
    user_id,
    provider_id,
    exc: OIDCRoleReversionBlocked,
) -> Never:
    db.rollback()
    try:
        record_audit(
            db,
            actor_user_id=user_id,
            request_id=getattr(request.state, "request_id", None),
            action="oidc.identity.unlink",
            resource_type="external_identity",
            resource_id=None,
            success=False,
            metadata={
                "provider_id": str(provider_id),
                "reason": exc.reason,
                "affected_investigation_count": exc.investigation_count,
            },
        )
        db.commit()
    except Exception as audit_exc:
        db.rollback()
        logger.error(
            "oidc_unlink_role_reversion_audit_failed user_id=%s provider_id=%s error_type=%s",
            user_id,
            provider_id,
            type(audit_exc).__name__,
            exc_info=True,
        )
    raise ApiHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
        error_code=exc.code,
        error_context={
            "reason": exc.reason,
            "affected_investigation_count": exc.investigation_count,
        },
    ) from exc


def _raise_access_purge_blocked(
    db: Session,
    *,
    request: Request,
    user_id,
    provider_id,
    exc: OIDCAccessPurgeBlocked,
) -> Never:
    db.rollback()
    try:
        record_audit(
            db,
            actor_user_id=user_id,
            request_id=getattr(request.state, "request_id", None),
            action="oidc.identity.unlink",
            resource_type="external_identity",
            resource_id=None,
            success=False,
            metadata={
                "provider_id": str(provider_id),
                "reason": "investigation_owner_reassignment_required",
                "affected_investigation_count": len(exc.investigations),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "oidc_unlink_rejection_audit_failed user_id=%s provider_id=%s",
            user_id,
            provider_id,
        )
    raise ApiHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
        error_code="oidc_access_investigation_owner_reassignment_required",
        error_context={
            "affected_investigation_count": len(exc.investigations),
        },
    ) from exc


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
    try:
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
    except Exception as audit_exc:
        db.rollback()
        logger.error(
            "oidc_unlink_mfa_rejection_audit_failed user_id=%s provider_id=%s error_type=%s",
            user_id,
            provider_id,
            type(audit_exc).__name__,
            exc_info=True,
        )
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
