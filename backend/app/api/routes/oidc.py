from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, is_cookie_session_auth
from app.api.routes.oidc_account import router as account_router
from app.api.routes.oidc_provider import router as provider_router
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token_claims,
    generate_csrf_token,
    set_auth_cookies,
)
from app.db.session import get_db
from app.models.oidc import OIDCProvider
from app.models.user import User
from app.schemas.oidc import OIDCStartResponse
from app.services.audit import record_audit
from app.services.oidc_client import (
    OIDCClaims,
    OIDCProtocolError,
    build_oidc_authorization_url,
    exchange_oidc_code,
    load_oidc_metadata,
    oidc_failure_reason,
    validate_oidc_token_claims,
)
from app.services.oidc_config import (
    OIDCConfigurationError,
    load_primary_oidc_provider,
)
from app.services.oidc_identity import (
    OIDCAuthenticationResult,
    OIDCIdentityError,
    authenticate_oidc_identity,
    link_oidc_identity,
)
from app.services.oidc_transaction import (
    OIDCTransaction,
    clear_oidc_transaction_cookie,
    decode_oidc_transaction,
    new_oidc_transaction,
    set_oidc_transaction_cookie,
)
session_router = APIRouter()
logger = logging.getLogger("threatlens.oidc")


@session_router.get("/login")
def start_oidc_login(request: Request, db: Session = Depends(get_db)):
    try:
        return _start_oidc_flow(db, mode="login")
    except HTTPException as exc:
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE and _accepts_html(request):
            return _callback_redirect(load_primary_oidc_provider(db), "/login", {"oidc_error": "provider_unavailable"})
        raise


@session_router.post("/link", response_model=OIDCStartResponse)
def start_oidc_link(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_cookie_session_auth(request):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC account linking requires a browser session")
    authorization_url, transaction = _prepare_oidc_flow(db, mode="link", user=user)
    set_oidc_transaction_cookie(response, transaction)
    response.headers["Cache-Control"] = "no-store"
    return OIDCStartResponse(authorization_url=authorization_url)


@session_router.get("/callback")
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

    claims: OIDCClaims | None = None
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
        claim_diagnostics = _identity_claim_diagnostics(claims)
        logger.warning(
            "oidc_identity_failed provider_id=%s mode=%s error_code=%s claim_diagnostics=%s",
            provider.id,
            transaction.mode,
            exc.code,
            claim_diagnostics,
        )
        return _callback_failure(
            db,
            provider,
            exc.code,
            response_mode=transaction.mode,
            actor_user_id=transaction.user_id,
            details={"claim_diagnostics": claim_diagnostics},
        )
    except (OIDCConfigurationError, OIDCProtocolError, ValueError) as exc:
        db.rollback()
        logger.warning("oidc_callback_failed provider_id=%s error_type=%s", provider.id, type(exc).__name__)
        return _callback_failure(db, provider, "authentication_failed", response_mode=transaction.mode)
    except Exception:
        db.rollback()
        logger.exception("oidc_callback_unexpected_failure provider_id=%s", provider.id)
        return _callback_failure(db, provider, "authentication_failed", response_mode=transaction.mode)


def _start_oidc_flow(db: Session, *, mode: str, user: User | None = None) -> RedirectResponse:
    authorization_url, transaction = _prepare_oidc_flow(db, mode=mode, user=user)
    response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
    set_oidc_transaction_cookie(response, transaction)
    response.headers["Cache-Control"] = "no-store"
    return response


def _prepare_oidc_flow(
    db: Session,
    *,
    mode: str,
    user: User | None = None,
) -> tuple[str, OIDCTransaction]:
    provider = load_primary_oidc_provider(db)
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
        reason = oidc_failure_reason(exc)
        logger.warning(
            "oidc_start_failed provider_id=%s mode=%s error_type=%s reason=%s",
            provider.id,
            mode,
            type(exc).__name__,
            reason,
        )
        record_audit(
            db,
            actor_user_id=user.id if user else None,
            action="auth.oidc.start",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            success=False,
            metadata={"mode": mode, "error_type": type(exc).__name__, "reason": reason},
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

    return authorization_url, transaction


def _accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def _provider_for_transaction(db: Session, provider_id: str | None) -> OIDCProvider | None:
    if not provider_id:
        return load_primary_oidc_provider(db)
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
    details: dict[str, object] | None = None,
) -> RedirectResponse:
    parsed_actor_id: uuid.UUID | None = None
    if actor_user_id:
        try:
            parsed_actor_id = uuid.UUID(actor_user_id)
        except ValueError:
            parsed_actor_id = None
    audit_metadata: dict[str, object] = {"error_code": error_code, "mode": response_mode}
    if details:
        audit_metadata.update(details)
    record_audit(
        db,
        actor_user_id=parsed_actor_id,
        action="auth.oidc.callback",
        resource_type="oidc_provider",
        resource_id=str(provider.id) if provider else None,
        success=False,
        metadata=audit_metadata,
    )
    db.commit()
    target_path = "/settings/account" if response_mode == "link" else "/login"
    query_key = "oidc_link" if response_mode == "link" else "oidc_error"
    return _callback_redirect(provider, target_path, {query_key: error_code})


def _identity_claim_diagnostics(claims: OIDCClaims | None) -> dict[str, object]:
    if claims is None:
        return {"claims_available": False}

    email = claims.claims.get("email")
    email_verified = claims.claims.get("email_verified")
    return {
        "claims_available": True,
        "email_claim_present": "email" in claims.claims,
        "email_value_present": isinstance(email, str) and bool(email.strip()),
        "email_claim_type": type(email).__name__ if email is not None else None,
        "email_verified_claim_present": "email_verified" in claims.claims,
        "email_verified": email_verified if isinstance(email_verified, bool) else None,
        "email_verified_claim_type": type(email_verified).__name__ if email_verified is not None else None,
    }


def _callback_redirect(provider: OIDCProvider | None, path: str, query: dict[str, str]) -> RedirectResponse:
    base_url = provider.public_base_url.rstrip("/") if provider else ""
    target = f"{base_url}{path}"
    if query:
        target = f"{target}?{urlencode(query)}"
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    clear_oidc_transaction_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


router = APIRouter(prefix="/auth/oidc", tags=["auth", "oidc"])
router.include_router(provider_router)
router.include_router(session_router)
router.include_router(account_router)
