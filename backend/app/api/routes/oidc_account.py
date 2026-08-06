from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, resolve_client_ip
from app.core.security import verify_password
from app.db.session import get_db
from app.models.oidc import ExternalIdentity
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.schemas.oidc import OIDCAccountStatusResponse, OIDCUnlinkRequest
from app.services.audit import record_audit
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    clear_password_verification_failures,
    record_password_verification_failure,
)
from app.services.oidc_config import load_primary_oidc_provider
from app.services.oidc_identity import OIDCIdentityError, unlink_oidc_identity

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
    provider = load_primary_oidc_provider(db)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC provider is not configured")
    _verify_unlink_password(request, user, payload.current_password)
    try:
        identity = unlink_oidc_identity(db, provider, user)
    except OIDCIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_audit(
        db,
        actor_user_id=user.id,
        action="oidc.identity.unlink",
        resource_type="external_identity",
        resource_id=str(identity.id),
        metadata={"provider_id": str(provider.id)},
    )
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT


def _verify_unlink_password(request: Request, user: User, current_password: str) -> None:
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
    client_ip = resolve_client_ip(request)
    throttle = check_password_verification_throttle(user.email, client_ip)
    if throttle.blocked:
        headers = {"Retry-After": str(throttle.retry_after_seconds)} if throttle.retry_after_seconds else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed current password verification attempts. Try again later.",
            headers=headers,
        )
    if not verify_password(current_password, user.password_hash):
        record_password_verification_failure(user.email, client_ip)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    clear_password_verification_failures(user.email, client_ip)
