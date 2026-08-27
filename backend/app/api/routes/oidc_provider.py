from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_admin_user,
    get_current_auth_session_id,
    is_cookie_session_auth,
    require_token_scopes,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
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
from app.services.auth_sessions import lock_exact_auth_session, lock_user_auth_states
from app.services.local_mfa import mfa_status
from app.services.oidc_client import (
    OIDCProtocolError,
    oidc_failure_reason,
    test_oidc_provider,
)
from app.services.oidc_config import (
    OIDCConfigurationError,
    OIDC_PROVIDER_SYSTEM_KEY,
    load_primary_oidc_provider,
    provider_response,
    validate_oidc_provider_urls,
)
from app.services.secret_storage import encrypt_text
from app.services.recent_auth import (
    auth_session_has_configured_oidc_mfa_assurance,
    recent_authentication_error_context,
    recent_authentication_state,
)
from app.services.user_access import (
    LocalBreakGlassAdminRequiredError,
    acquire_active_admin_invariant_lock,
    acquire_oidc_provider_config_lock,
    ensure_viable_local_break_glass_admin_exists,
)

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


@router.put(
    "/provider",
    response_model=OIDCProviderResponse,
    openapi_extra={"x-threatlens-browser-session-only": True},
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": (
                "Requires a recent opaque admin browser session. Stable codes include "
                "`browser_session_required`, `local_reauthentication_required`, and "
                "`oidc_reauthentication_required`; OIDC sessions also require "
                "`oidc_mfa_assurance_required`."
            )
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "Provider identity conflict or `oidc_provider_revision_conflict`; "
                "revision conflicts include the current revision in the response body "
                "and `X-Current-Version`. Disabling OIDC without a viable local "
                "administrator returns `oidc_break_glass_admin_required`."
            )
        },
    },
)
def update_oidc_provider(
    payload: OIDCProviderUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    try:
        validate_oidc_provider_urls(
            issuer_url=payload.issuer_url, public_base_url=payload.public_base_url
        )
    except OIDCConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    acquire_active_admin_invariant_lock(db)
    acquire_oidc_provider_config_lock(db)
    provider = db.scalar(
        select(OIDCProvider)
        .where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY)
        .with_for_update()
    )
    admin = _require_recent_provider_admin(request, db=db, admin=admin)
    identity_count = 0
    if provider is not None:
        if (
            payload.expected_config_revision is not None
            and payload.expected_config_revision != provider.config_revision
        ):
            _raise_provider_revision_conflict(
                expected_revision=payload.expected_config_revision,
                current_revision=int(provider.config_revision or 0),
                message=(
                    "OIDC provider settings changed after they were loaded. "
                    "Reload the settings and apply your changes again."
                ),
            )
        identity_count = int(
            db.scalar(
                select(func.count(ExternalIdentity.id)).where(
                    ExternalIdentity.provider_id == provider.id
                )
            )
            or 0
        )
        identity_key_changed = (
            provider.issuer_url != payload.issuer_url
            or provider.client_id != payload.client_id
        )
        if identity_count and identity_key_changed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Issuer URL and client ID cannot change while OIDC identities are linked",
            )
        if provider.enabled and not payload.enabled:
            _require_viable_local_break_glass_admin(
                db,
                actor_user_id=admin.id,
                provider_id=provider.id,
            )
    else:
        if payload.expected_config_revision not in (None, 0):
            _raise_provider_revision_conflict(
                expected_revision=payload.expected_config_revision,
                current_revision=0,
                message=(
                    "OIDC provider settings are no longer configured. "
                    "Reload the settings and retry."
                ),
            )
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
    provider.role_mappings_json = [
        mapping.model_dump() for mapping in payload.role_mappings
    ]
    provider.default_role = payload.default_role
    provider.jit_provisioning_enabled = payload.jit_provisioning_enabled
    provider.auto_approve_users = payload.auto_approve_users
    provider.require_verified_email = payload.require_verified_email
    provider.sync_roles_on_login = payload.sync_roles_on_login
    provider.config_revision = int(provider.config_revision or 0) + 1
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
            "config_revision": provider.config_revision,
        },
    )
    db.commit()
    db.refresh(provider)
    return provider_response(provider)


def _require_recent_provider_admin(
    request: Request,
    *,
    db: Session,
    admin: User,
) -> User:
    action = "oidc_provider_update"
    if not is_cookie_session_auth(request):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "OIDC provider changes require a recently authenticated administrator "
                "browser session. API tokens cannot perform this operation."
            ),
            error_code="browser_session_required",
            error_context=recent_authentication_error_context(None, action=action),
        )

    locked_admin = lock_user_auth_states(db, [admin.id]).get(admin.id)
    if (
        locked_admin is None
        or locked_admin.role != ROLE_ADMIN
        or not locked_admin.is_active
        or not locked_admin.is_approved
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access changed. Sign in again.",
            error_code="account_security_changed",
        )

    session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    if session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This legacy browser session cannot change OIDC provider settings. "
                "Sign out and sign in again."
            ),
            error_code="opaque_session_required",
        )
    session = lock_exact_auth_session(
        db,
        token=session_token,
        expected_session_id=session_id,
        user_id=locked_admin.id,
        auth_token_version=int(locked_admin.auth_token_version or 0),
    )
    if session is None:
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The administrator browser session is no longer active. Sign in again.",
            error_code="session_inactive",
        )

    recent = recent_authentication_state(session)
    local_mfa_enabled, _confirmed_at, _remaining = mfa_status(
        db,
        user_id=locked_admin.id,
    )
    local_assurance_valid = (
        session.auth_method != "local"
        or not local_mfa_enabled
        or session.mfa_method == "totp"
    )
    if not recent.valid or not local_assurance_valid:
        error_code = (
            "oidc_reauthentication_required"
            if session.auth_method == "oidc"
            else "local_reauthentication_required"
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Reauthenticate with the identity provider before changing OIDC settings."
                if session.auth_method == "oidc"
                else (
                    "Confirm the current local password and authenticator code before "
                    "changing OIDC settings."
                )
            ),
            error_code=error_code,
            error_context=recent_authentication_error_context(session, action=action),
        )
    if (
        session.auth_method == "oidc"
        and not auth_session_has_configured_oidc_mfa_assurance(session)
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The identity provider did not assert the configured MFA assurance. "
                "Complete MFA during identity-provider reauthentication and retry."
            ),
            error_code="oidc_mfa_assurance_required",
            error_context=recent_authentication_error_context(session, action=action),
        )
    return locked_admin


def _require_viable_local_break_glass_admin(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    provider_id: uuid.UUID,
) -> None:
    try:
        ensure_viable_local_break_glass_admin_exists(db)
    except LocalBreakGlassAdminRequiredError:
        pass
    else:
        return
    record_audit(
        db,
        actor_user_id=actor_user_id,
        action="oidc.provider.update",
        resource_type="oidc_provider",
        resource_id=str(provider_id),
        success=False,
        metadata={"reason": "local_break_glass_admin_required"},
    )
    db.commit()
    raise ApiHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "OIDC cannot be disabled until an active, approved administrator with "
            "local password sign-in is available. Verify that break-glass account, "
            "then retry."
        ),
        error_code="oidc_break_glass_admin_required",
        error_context={"viable_local_admin_count": 0},
    )


def _raise_provider_revision_conflict(
    *,
    expected_revision: int,
    current_revision: int,
    message: str,
) -> None:
    raise ApiHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": message,
            "expected_config_revision": expected_revision,
            "current_config_revision": current_revision,
        },
        error_code="oidc_provider_revision_conflict",
        headers={"X-Current-Version": str(current_revision)},
    )


@router.post("/provider/test", response_model=OIDCProviderTestResponse)
def test_configured_oidc_provider(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    provider = load_primary_oidc_provider(db)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC provider is not configured",
        )
    db.expunge(provider)
    db.rollback()
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=reason
        ) from exc
    except Exception as exc:
        logger.exception(
            "oidc_provider_test_unexpected_failure provider_id=%s", provider.id
        )
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
