from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_current_user, is_cookie_session_auth, require_token_scopes, resolve_client_ip
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token_claims,
    generate_csrf_token,
    set_auth_cookies,
    verify_password,
)
from app.core.token_scopes import SCOPE_READ_USERS, SCOPE_WRITE_USERS
from app.db.session import get_db
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import User
from app.schemas.oidc import (
    OIDCAccountStatusResponse,
    OIDCProviderResponse,
    OIDCProviderTestResponse,
    OIDCProviderUpdateRequest,
    OIDCPublicSettingsResponse,
    OIDCUnlinkRequest,
)
from app.services.audit import record_audit
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    clear_password_verification_failures,
    record_password_verification_failure,
)
from app.services.oidc_client import (
    OIDCProtocolError,
    build_oidc_authorization_url,
    exchange_oidc_code,
    load_oidc_metadata,
    test_oidc_provider,
    validate_oidc_token_claims,
)
from app.services.oidc_config import (
    OIDCConfigurationError,
    OIDC_PROVIDER_SYSTEM_KEY,
    provider_response,
    validate_oidc_provider_urls,
)
from app.services.oidc_identity import (
    OIDCAuthenticationResult,
    OIDCIdentityError,
    authenticate_oidc_identity,
    link_oidc_identity,
    unlink_oidc_identity,
)
from app.services.oidc_transaction import (
    clear_oidc_transaction_cookie,
    decode_oidc_transaction,
    new_oidc_transaction,
    set_oidc_transaction_cookie,
)
from app.services.secret_storage import encrypt_text

router = APIRouter(prefix="/auth/oidc", tags=["auth", "oidc"])
logger = logging.getLogger("threatlens.oidc")


@router.get("/settings", response_model=OIDCPublicSettingsResponse)
def public_oidc_settings(db: Session = Depends(get_db)):
    provider = _load_primary_provider(db)
    if provider is None or not provider.enabled:
        return OIDCPublicSettingsResponse(enabled=False)
    return OIDCPublicSettingsResponse(enabled=True, provider_name=provider.name)


@router.get("/provider", response_model=OIDCProviderResponse)
def get_oidc_provider(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    return provider_response(_load_primary_provider(db))


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
            "identity_count": identity_count,
            "jit_provisioning_enabled": provider.jit_provisioning_enabled,
            "auto_approve_users": provider.auto_approve_users,
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
    provider = _load_primary_provider(db)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC provider is not configured")
    try:
        metadata, key_count = test_oidc_provider(provider)
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
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


@router.get("/login")
def start_oidc_login(db: Session = Depends(get_db)):
    return _start_oidc_flow(db, mode="login")


@router.get("/link")
def start_oidc_link(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC account linking requires a browser session")
    return _start_oidc_flow(db, mode="link", user=user)


@router.get("/callback")
def oidc_callback(
    request: Request,
    state_value: str | None = Query(default=None, alias="state", max_length=2048),
    code: str | None = Query(default=None, max_length=8192),
    provider_error: str | None = Query(default=None, alias="error", max_length=256),
    db: Session = Depends(get_db),
):
    transaction = decode_oidc_transaction(request, state_value)
    provider = _provider_for_transaction(db, transaction.provider_id if transaction else None)
    if transaction is None or provider is None or not provider.enabled:
        return _callback_failure(db, provider, "invalid_state", response_mode="login")
    if provider_error:
        return _callback_failure(db, provider, "provider_rejected", response_mode=transaction.mode)
    if not code:
        return _callback_failure(db, provider, "missing_code", response_mode=transaction.mode)

    try:
        metadata = load_oidc_metadata(provider)
        token = exchange_oidc_code(provider, metadata, code=code, code_verifier=transaction.code_verifier)
        claims = validate_oidc_token_claims(provider, metadata, token, nonce=transaction.nonce)
        if transaction.mode == "link":
            user = _resolve_link_session_user(db, request, transaction.user_id)
            if user is None:
                raise OIDCIdentityError("link_session_expired", "The account-linking session is no longer valid")
            identity = link_oidc_identity(db, provider, user, claims)
            record_audit(
                db,
                actor_user_id=user.id,
                action="oidc.identity.link",
                resource_type="external_identity",
                resource_id=str(identity.id),
                metadata={"provider_id": str(provider.id), "issuer": claims.issuer},
            )
            db.commit()
            return _callback_redirect(provider, "/settings/account", {"oidc_link": "success"})

        result = authenticate_oidc_identity(db, provider, claims)
        _record_oidc_authentication_audit(db, provider, result)
        if not result.user.is_approved:
            record_audit(
                db,
                actor_user_id=result.user.id,
                action="auth.oidc.login",
                resource_type="user",
                resource_id=str(result.user.id),
                success=False,
                metadata={"provider_id": str(provider.id), "error_code": "approval_required"},
            )
            db.commit()
            return _callback_redirect(provider, "/login", {"oidc_error": "approval_required"})
        if not result.user.is_active:
            record_audit(
                db,
                actor_user_id=result.user.id,
                action="auth.oidc.login",
                resource_type="user",
                resource_id=str(result.user.id),
                success=False,
                metadata={"provider_id": str(provider.id), "error_code": "account_inactive"},
            )
            db.commit()
            return _callback_redirect(provider, "/login", {"oidc_error": "account_inactive"})

        record_audit(
            db,
            actor_user_id=result.user.id,
            action="auth.oidc.login",
            resource_type="user",
            resource_id=str(result.user.id),
            metadata={"provider_id": str(provider.id), "provisioned": result.provisioned},
        )
        db.commit()
        response = _callback_redirect(provider, "/", {})
        access_token = create_access_token(
            str(result.user.id),
            token_version=int(result.user.auth_token_version or 0),
        )
        set_auth_cookies(response, access_token, generate_csrf_token())
        return response
    except OIDCIdentityError as exc:
        db.rollback()
        return _callback_failure(db, provider, exc.code, response_mode=transaction.mode, actor_user_id=transaction.user_id)
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
        db.rollback()
        logger.warning("oidc_callback_failed provider_id=%s error_type=%s", provider.id, type(exc).__name__)
        return _callback_failure(db, provider, "authentication_failed", response_mode=transaction.mode)
    except Exception:
        db.rollback()
        logger.exception("oidc_callback_unexpected_failure provider_id=%s", provider.id)
        return _callback_failure(db, provider, "authentication_failed", response_mode=transaction.mode)


@router.get("/account", response_model=OIDCAccountStatusResponse)
def oidc_account_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = _load_primary_provider(db)
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
    provider = _load_primary_provider(db)
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


def _start_oidc_flow(db: Session, *, mode: str, user: User | None = None) -> RedirectResponse:
    provider = _load_primary_provider(db)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC sign-in is not available")
    try:
        metadata = load_oidc_metadata(provider)
        transaction = new_oidc_transaction(
            provider_id=str(provider.id),
            mode="link" if mode == "link" else "login",
            user_id=str(user.id) if user else None,
        )
        authorization_url = build_oidc_authorization_url(
            provider,
            metadata,
            state=transaction.state,
            nonce=transaction.nonce,
            code_verifier=transaction.code_verifier,
        )
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
        record_audit(
            db,
            actor_user_id=user.id if user else None,
            action="auth.oidc.start",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"mode": mode, "error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC sign-in is temporarily unavailable; contact an administrator",
        ) from exc
    except Exception as exc:
        logger.exception("oidc_start_unexpected_failure provider_id=%s mode=%s", provider.id, mode)
        record_audit(
            db,
            actor_user_id=user.id if user else None,
            action="auth.oidc.start",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"mode": mode, "error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC sign-in is temporarily unavailable; contact an administrator",
        ) from exc

    response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
    set_oidc_transaction_cookie(response, transaction)
    response.headers["Cache-Control"] = "no-store"
    return response


def _load_primary_provider(db: Session) -> OIDCProvider | None:
    return db.scalar(select(OIDCProvider).where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY))


def _provider_for_transaction(db: Session, provider_id: str | None) -> OIDCProvider | None:
    if not provider_id:
        return _load_primary_provider(db)
    try:
        parsed_id = uuid.UUID(provider_id)
    except ValueError:
        return None
    return db.scalar(select(OIDCProvider).where(OIDCProvider.id == parsed_id))


def _resolve_link_session_user(db: Session, request: Request, expected_user_id: str | None) -> User | None:
    settings = get_settings()
    session_token = request.cookies.get(settings.auth_cookie_name)
    claims = decode_access_token_claims(session_token) if session_token else None
    if not claims or not expected_user_id or claims.get("sub") != expected_user_id:
        return None
    try:
        user_id = uuid.UUID(expected_user_id)
        token_version = int(claims.get("ver", 0))
    except (TypeError, ValueError):
        return None
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or token_version != int(user.auth_token_version or 0):
        return None
    if not user.is_active or not user.is_approved:
        return None
    return user


def _record_oidc_authentication_audit(
    db: Session,
    provider: OIDCProvider,
    result: OIDCAuthenticationResult,
) -> None:
    if result.provisioned:
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="oidc.user.provision",
            resource_type="user",
            resource_id=str(result.user.id),
            metadata={
                "provider_id": str(provider.id),
                "role": result.user.role,
                "is_approved": result.user.is_approved,
            },
        )
    if result.previous_role is not None:
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="oidc.role.sync",
            resource_type="user",
            resource_id=str(result.user.id),
            metadata={
                "provider_id": str(provider.id),
                "previous_role": result.previous_role,
                "role": result.user.role,
                "revoked_api_tokens": result.revoked_api_tokens,
            },
        )
    if result.role_sync_skipped:
        record_audit(
            db,
            actor_user_id=result.user.id,
            action="oidc.role.sync",
            resource_type="user",
            resource_id=str(result.user.id),
            success=False,
            metadata={"provider_id": str(provider.id), "reason": result.role_sync_skipped},
        )


def _callback_failure(
    db: Session,
    provider: OIDCProvider | None,
    error_code: str,
    *,
    response_mode: str,
    actor_user_id: str | None = None,
) -> RedirectResponse:
    parsed_actor_id: uuid.UUID | None = None
    if actor_user_id:
        try:
            parsed_actor_id = uuid.UUID(actor_user_id)
        except ValueError:
            parsed_actor_id = None
    record_audit(
        db,
        actor_user_id=parsed_actor_id,
        action="auth.oidc.callback",
        resource_type="oidc_provider",
        resource_id=str(provider.id) if provider else None,
        success=False,
        metadata={"error_code": error_code, "mode": response_mode},
    )
    db.commit()
    target_path = "/settings/account" if response_mode == "link" else "/login"
    query_key = "oidc_link" if response_mode == "link" else "oidc_error"
    return _callback_redirect(provider, target_path, {query_key: error_code})


def _callback_redirect(provider: OIDCProvider | None, path: str, query: dict[str, str]) -> RedirectResponse:
    base_url = provider.public_base_url.rstrip("/") if provider else ""
    target = f"{base_url}{path}"
    if query:
        target = f"{target}?{urlencode(query)}"
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    clear_oidc_transaction_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


def _verify_unlink_password(request: Request, user: User, current_password: str) -> None:
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
