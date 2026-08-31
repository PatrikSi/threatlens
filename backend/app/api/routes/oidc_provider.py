from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_admin_user,
    get_current_auth_session_id,
    is_api_token_auth,
    is_cookie_session_auth,
    require_token_scopes,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.logging_config import verbose_logging_enabled
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
from app.services.auth_sessions import (
    lock_exact_auth_session,
    lock_user_auth_states,
    revoke_all_auth_sessions,
)
from app.services.authorization import (
    bump_iam_policy_revision,
    lock_iam_policy_for_mutation,
)
from app.services.oidc_access_lifecycle import (
    OIDCAccessPurgeBlocked,
    OIDCAccessPurgeResult,
    oidc_access_affected_user_ids,
    provider_oidc_source_keys,
    purge_oidc_access,
)
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
from app.services.oidc_role_provenance import (
    OIDCRoleReversionBlocked,
    OIDCRoleReversionResult,
    revert_oidc_synchronized_role,
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
    revoke_user_credentials_with_counts,
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
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": (
                "Requires an administrator API token with `write:users`, or a recent "
                "opaque administrator browser session. Browser-session codes include "
                "`local_reauthentication_required` and `oidc_reauthentication_required`; "
                "OIDC sessions also require `oidc_mfa_assurance_required`."
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

    lock_iam_policy_for_mutation(db)
    acquire_active_admin_invariant_lock(db)
    acquire_oidc_provider_config_lock(db)
    provider = db.scalar(
        select(OIDCProvider)
        .where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY)
        .with_for_update()
    )
    previous_enabled = provider.enabled if provider is not None else None
    disabling = bool(provider is not None and provider.enabled and not payload.enabled)
    role_source_keys: set[str] = set()
    group_source_keys: set[str] = set()
    linked_user_ids: tuple[uuid.UUID, ...] = ()
    affected_user_ids: tuple[uuid.UUID, ...] = ()
    if provider is not None and disabling:
        role_source_keys, group_source_keys = provider_oidc_source_keys(db, provider.id)
        affected_user_ids = oidc_access_affected_user_ids(
            db,
            role_source_keys=role_source_keys,
            group_source_keys=group_source_keys,
        )
        linked_user_ids = tuple(
            db.scalars(
                select(ExternalIdentity.user_id)
                .where(ExternalIdentity.provider_id == provider.id)
                .order_by(ExternalIdentity.user_id)
            ).all()
        )
    locked_users = lock_user_auth_states(
        db, [admin.id, *affected_user_ids, *linked_user_ids]
    )
    admin = authorize_oidc_provider_admin(
        request,
        db=db,
        admin=admin,
        locked_admin=locked_users.get(admin.id),
    )
    identity_count = 0
    access_purge = OIDCAccessPurgeResult()
    revoked_oidc_sessions = 0
    revoked_linked_api_tokens = 0
    revoked_linked_auth_sessions = 0
    fixed_role_iam_revision: int | None = None
    role_reversions: list[OIDCRoleReversionResult] = []
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
        if disabling:
            _require_viable_local_break_glass_admin(
                db,
                request=request,
                actor_user_id=admin.id,
                provider_id=provider.id,
            )
            try:
                linked_identities = list(
                    db.scalars(
                        select(ExternalIdentity)
                        .where(ExternalIdentity.provider_id == provider.id)
                        .order_by(ExternalIdentity.user_id, ExternalIdentity.id)
                        .with_for_update()
                    ).all()
                )
                revoked_oidc_sessions = sum(
                    revoke_all_auth_sessions(
                        db,
                        user_id=user_id,
                        reason="oidc_provider_disabled",
                        auth_method="oidc",
                    )
                    for user_id in linked_user_ids
                )
                access_purge = purge_oidc_access(
                    db,
                    role_source_keys=role_source_keys,
                    group_source_keys=group_source_keys,
                    actor_user_id=admin.id,
                    revocation_reason="oidc_provider_disabled",
                )
                for identity in linked_identities:
                    linked_user = locked_users.get(identity.user_id)
                    if linked_user is None:
                        continue
                    role_reversions.append(
                        revert_oidc_synchronized_role(
                            db,
                            identity=identity,
                            user=linked_user,
                            actor_user_id=admin.id,
                            allow_legacy_retention=True,
                        )
                    )
                if any(result.changed for result in role_reversions):
                    fixed_role_iam_revision = bump_iam_policy_revision(db)
                for user_id in linked_user_ids:
                    linked_user = locked_users.get(user_id)
                    if linked_user is None:
                        continue
                    revoked = revoke_user_credentials_with_counts(
                        db,
                        linked_user,
                        reason="oidc_provider_disabled",
                    )
                    revoked_linked_api_tokens += revoked.api_tokens
                    revoked_linked_auth_sessions += revoked.auth_sessions
            except OIDCAccessPurgeBlocked as exc:
                actor_user_id = admin.id
                provider_id = provider.id
                db.rollback()
                _record_provider_rejection(
                    db,
                    request=request,
                    actor_user_id=actor_user_id,
                    provider_id=provider_id,
                    metadata={
                        "reason": (
                            "oidc_access_investigation_owner_reassignment_required"
                        ),
                        "user_id": str(exc.user_id),
                        "affected_investigation_count": len(exc.investigations),
                    },
                )
                raise ApiHTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                    error_code=(
                        "oidc_access_investigation_owner_reassignment_required"
                    ),
                    error_context={
                        "user_id": str(exc.user_id),
                        "affected_investigation_count": len(exc.investigations),
                    },
                ) from exc
            except OIDCRoleReversionBlocked as exc:
                actor_user_id = admin.id
                provider_id = provider.id
                db.rollback()
                _record_provider_rejection(
                    db,
                    request=request,
                    actor_user_id=actor_user_id,
                    provider_id=provider_id,
                    metadata={
                        "reason": exc.reason,
                        "affected_investigation_count": exc.investigation_count,
                    },
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
            "previous_enabled": previous_enabled,
            "purge_trigger": "provider_disabled" if disabling else None,
            "linked_user_count": len(linked_user_ids) if disabling else None,
            "revoked_oidc_sessions": revoked_oidc_sessions,
            "fixed_roles_reverted": sum(
                int(result.changed) for result in role_reversions
            ),
            "fixed_roles_with_legacy_provenance": sum(
                int(result.legacy_provenance) for result in role_reversions
            ),
            "fixed_roles_with_manual_override": sum(
                int(result.manual_override) for result in role_reversions
            ),
            "purged_role_assignments": access_purge.removed_role_assignments,
            "purged_group_memberships": access_purge.removed_group_memberships,
            "affected_user_count": access_purge.affected_user_count,
            "access_reduced_user_count": access_purge.access_reduced_user_count,
            "revoked_api_tokens": (
                access_purge.revoked_api_tokens + revoked_linked_api_tokens
            ),
            "revoked_auth_sessions": (
                access_purge.revoked_auth_sessions
                + revoked_oidc_sessions
                + revoked_linked_auth_sessions
            ),
            "cancelled_pending_mfa_enrollments": (
                access_purge.cancelled_pending_mfa_enrollments
            ),
            "cleared_investigation_assignments": (
                access_purge.cleared_investigation_assignments
                + sum(
                    result.cleared_investigation_assignments
                    for result in role_reversions
                )
            ),
            "iam_policy_revision": (
                fixed_role_iam_revision or access_purge.iam_policy_revision
            ),
            "legacy_role_retained": any(
                result.legacy_provenance for result in role_reversions
            ),
        },
    )
    db.commit()
    db.refresh(provider)
    return provider_response(provider)


def _record_provider_rejection(
    db: Session,
    *,
    request: Request,
    actor_user_id: uuid.UUID,
    provider_id: uuid.UUID,
    metadata: dict[str, object],
) -> None:
    try:
        record_audit(
            db,
            actor_user_id=actor_user_id,
            request_id=getattr(request.state, "request_id", None),
            action="oidc.provider.update",
            resource_type="oidc_provider",
            resource_id=str(provider_id),
            success=False,
            metadata=metadata,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "oidc_provider_rejection_audit_failed provider_id=%s actor_user_id=%s error_type=%s",
            provider_id,
            actor_user_id,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )


def authorize_oidc_provider_admin(
    request: Request,
    *,
    db: Session,
    admin: User,
    locked_admin: User | None = None,
    action: str = "oidc_provider_update",
    operation_label: str = "OIDC provider settings",
) -> User:
    api_token_auth = is_api_token_auth(request)
    if not api_token_auth and not is_cookie_session_auth(request):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Changes to {operation_label} require an administrator API token or a recently "
                "authenticated administrator browser session. The supplied credential "
                "type cannot perform this operation."
            ),
            error_code="browser_session_required",
            error_context=recent_authentication_error_context(None, action=action),
        )

    if locked_admin is None:
        locked_admin = lock_user_auth_states(db, [admin.id]).get(admin.id)
    elif locked_admin.id != admin.id:
        locked_admin = None
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

    if api_token_auth:
        return locked_admin

    session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    if session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This legacy browser session cannot change {operation_label}. "
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
                f"Reauthenticate with the identity provider before changing {operation_label}."
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
    request: Request,
    actor_user_id: uuid.UUID,
    provider_id: uuid.UUID,
) -> None:
    try:
        ensure_viable_local_break_glass_admin_exists(db)
    except LocalBreakGlassAdminRequiredError:
        pass
    else:
        return
    _record_provider_rejection(
        db,
        request=request,
        actor_user_id=actor_user_id,
        provider_id=provider_id,
        metadata={"reason": "local_break_glass_admin_required"},
    )
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
