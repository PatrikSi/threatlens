from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_USERS, SCOPE_WRITE_USERS
from app.db.session import get_db
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import User
from app.schemas.oidc import (
    OIDCProviderResponse,
    OIDCProviderTestResponse,
    OIDCProviderUpdateRequest,
    OIDCPublicSettingsResponse,
)
from app.services.audit import record_audit
from app.services.oidc_client import OIDCProtocolError, oidc_failure_reason, test_oidc_provider
from app.services.oidc_config import (
    OIDCConfigurationError,
    OIDC_PROVIDER_SYSTEM_KEY,
    load_primary_oidc_provider,
    provider_response,
    validate_oidc_provider_urls,
)
from app.services.secret_storage import encrypt_text

router = APIRouter()
logger = logging.getLogger("threatlens.oidc")


@router.get("/settings", response_model=OIDCPublicSettingsResponse)
def public_oidc_settings(db: Session = Depends(get_db)):
    provider = load_primary_oidc_provider(db)
    if provider is None or not provider.enabled:
        return OIDCPublicSettingsResponse(enabled=False)
    return OIDCPublicSettingsResponse(enabled=True, provider_name=provider.name)


@router.get("/provider", response_model=OIDCProviderResponse)
def get_oidc_provider(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    return provider_response(load_primary_oidc_provider(db))


@router.put("/provider", response_model=OIDCProviderResponse)
def update_oidc_provider(
    payload: OIDCProviderUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    try:
        validate_oidc_provider_urls(issuer_url=payload.issuer_url, public_base_url=payload.public_base_url)
    except OIDCConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    provider = db.scalar(
        select(OIDCProvider).where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY).with_for_update()
    )
    identity_count = 0
    if provider is not None:
        identity_count = int(
            db.scalar(select(func.count(ExternalIdentity.id)).where(ExternalIdentity.provider_id == provider.id)) or 0
        )
        identity_key_changed = provider.issuer_url != payload.issuer_url or provider.client_id != payload.client_id
        if identity_count and identity_key_changed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Issuer URL and client ID cannot change while OIDC identities are linked",
            )
    else:
        provider = OIDCProvider(
            system_key=OIDC_PROVIDER_SYSTEM_KEY,
            name=payload.name,
            issuer_url=payload.issuer_url,
            client_id=payload.client_id,
            public_base_url=payload.public_base_url,
            scopes=list(payload.scopes),
        )
        db.add(provider)

    existing_secret = provider.client_secret_encrypted
    next_secret = existing_secret
    secret_updated = (
        payload.client_secret is not None
        or payload.clear_client_secret
        or (payload.client_auth_method == "none" and existing_secret is not None)
    )
    if payload.client_auth_method == "none" or payload.clear_client_secret:
        next_secret = None
    if payload.client_secret is not None:
        next_secret = encrypt_text(payload.client_secret)
    if payload.enabled and payload.client_auth_method != "none" and not next_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An OIDC client secret is required for the selected client authentication method",
        )

    provider.name = payload.name
    provider.enabled = payload.enabled
    provider.issuer_url = payload.issuer_url
    provider.client_id = payload.client_id
    provider.client_secret_encrypted = next_secret
    provider.client_auth_method = payload.client_auth_method
    provider.public_base_url = payload.public_base_url
    provider.scopes = list(payload.scopes)
    provider.role_claim = payload.role_claim
    provider.role_mappings_json = [mapping.model_dump() for mapping in payload.role_mappings]
    provider.default_role = payload.default_role
    provider.jit_provisioning_enabled = payload.jit_provisioning_enabled
    provider.auto_approve_users = payload.auto_approve_users
    provider.require_verified_email = payload.require_verified_email
    provider.sync_roles_on_login = payload.sync_roles_on_login
    provider.updated_by_user_id = admin.id
    db.add(provider)
    db.flush()
    record_audit(
        db,
        actor_user_id=admin.id,
        action="oidc.provider.update",
        resource_type="oidc_provider",
        resource_id=str(provider.id),
        metadata={
            "enabled": provider.enabled,
            "issuer_url": provider.issuer_url,
            "client_id": provider.client_id,
            "client_auth_method": provider.client_auth_method,
            "secret_updated": secret_updated,
            "insecure_http": provider.issuer_url.startswith("http://")
            or provider.public_base_url.startswith("http://"),
            "identity_count": identity_count,
            "jit_provisioning_enabled": provider.jit_provisioning_enabled,
            "auto_approve_users": provider.auto_approve_users,
            "require_verified_email": provider.require_verified_email,
            "sync_roles_on_login": provider.sync_roles_on_login,
            "role_claim": provider.role_claim,
            "role_mapping_count": len(provider.role_mappings_json),
            "default_role": provider.default_role,
        },
    )
    db.commit()
    db.refresh(provider)
    return provider_response(provider)


@router.post("/provider/test", response_model=OIDCProviderTestResponse)
def test_configured_oidc_provider(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    provider = load_primary_oidc_provider(db)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC provider is not configured")
    try:
        metadata, key_count = test_oidc_provider(provider)
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
        reason = oidc_failure_reason(exc)
        logger.warning(
            "oidc_provider_test_failed provider_id=%s error_type=%s reason=%s",
            provider.id,
            type(exc).__name__,
            reason,
        )
        record_audit(
            db,
            actor_user_id=admin.id,
            action="oidc.provider.test",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"error_type": type(exc).__name__, "reason": reason},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason) from exc
    except Exception as exc:
        logger.exception("oidc_provider_test_unexpected_failure provider_id=%s", provider.id)
        record_audit(
            db,
            actor_user_id=admin.id,
            action="oidc.provider.test",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OIDC provider test failed unexpectedly",
        ) from exc

    record_audit(
        db,
        actor_user_id=admin.id,
        action="oidc.provider.test",
        resource_type="oidc_provider",
        resource_id=str(provider.id),
        metadata={"issuer": metadata.issuer, "jwks_key_count": key_count},
    )
    db.commit()
    return OIDCProviderTestResponse(
        issuer=metadata.issuer,
        authorization_endpoint=metadata.authorization_endpoint,
        token_endpoint=metadata.token_endpoint,
        jwks_key_count=key_count,
    )
