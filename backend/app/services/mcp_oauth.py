"""Explicit, short-lived OAuth delegation for MCP without general API authority."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlencode, urlsplit
import uuid

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.models.api_token import ApiToken
from app.models.mcp_oauth import MCPDelegation, MCPOAuthClient, MCPOAuthCode
from app.models.user import User
from app.services.authorization import (
    authorization_context_for_user,
    fence_authorization_context,
)
from app.services.data_access_policy import fence_data_access_context
from app.services.audit import record_audit

OAUTH_SCOPES = frozenset(
    {"read:mcp", "read:items", "read:teams", "read:reports", "read:investigations"}
)


def oauth_error(code: str, message: str, status: int = 400):
    return ApiHTTPException(
        status_code=status,
        error_code=code,
        detail=message,
        headers={"WWW-Authenticate": challenge_header()} if status == 401 else None,
    )


def oauth_urls() -> tuple[str, str]:
    settings = get_settings()
    base = (settings.public_app_url or "").rstrip("/")
    if not settings.mcp_enabled or not settings.mcp_oauth_enabled:
        raise oauth_error(
            "oauth_disabled", "MCP delegated authorization is disabled.", 404
        )
    try:
        parsed = urlsplit(base)
        _ = parsed.port  # Validate malformed and out-of-range ports before discovery.
    except ValueError:
        parsed = None
    if (
        parsed is None
        or "\\" in base
        or any(character.isspace() or ord(character) < 32 for character in base)
        or parsed.path
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise oauth_error(
            "oauth_configuration_invalid",
            "MCP OAuth requires PUBLIC_APP_URL to be the public HTTPS application origin (loopback HTTP is supported for development).",
            503,
        )
    return base, base + "/api/v1/mcp"


def challenge_header() -> str:
    try:
        issuer, _ = oauth_urls()
    except ApiHTTPException:
        return "Bearer"
    return f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource/api/v1/mcp", scope="read:mcp read:items"'


def validate_authorization(db: Session, payload):
    issuer, resource = oauth_urls()
    client = db.get(MCPOAuthClient, payload.client_id)
    if client is None or client.revoked_at is not None:
        raise oauth_error("invalid_client", "This MCP client is unknown or disabled.")
    if payload.redirect_uri not in client.redirect_uris:
        raise oauth_error(
            "invalid_redirect_uri",
            "Redirect URI must exactly match a registered callback.",
        )
    if payload.resource != resource:
        raise oauth_error(
            "invalid_target",
            "The resource must exactly match this installation's MCP endpoint.",
        )
    scopes = set(payload.scope.split())
    if not {"read:mcp", "read:items"} <= scopes or not scopes <= OAUTH_SCOPES:
        raise oauth_error(
            "invalid_scope",
            "Request read:mcp and read:items, plus only supported read scopes.",
        )
    return client, sorted(scopes), issuer


def grant_code(
    db: Session, *, request: Request, payload, user: User, authorization, access
):
    from app.api.deps import AUTH_SESSION_COOKIE
    from app.api.routes.tokens import (
        _enforce_browser_session_step_up,
        _enforce_requested_token_scopes_authorized,
    )
    from app.schemas.token import ApiTokenCreateRequest
    from app.services.auth_sessions import lock_user_auth_states

    if getattr(request.state, "auth_credential_kind", None) != AUTH_SESSION_COOKIE:
        raise oauth_error(
            "browser_consent_required",
            "Approve delegated MCP access from your signed-in browser.",
            403,
        )
    fence_authorization_context(db, authorization)
    fence_data_access_context(db, access)
    user = lock_user_auth_states(db, [user.id]).get(user.id)
    if user is None or not user.is_active or not user.is_approved:
        raise oauth_error(
            "account_changed", "Account access changed. Sign in again.", 401
        )
    client, scopes, issuer = validate_authorization(db, payload)
    # User -> client -> code/token order; client administration starts with the
    # exclusive IAM policy fence, preventing a reverse-order revocation race.
    client = db.scalar(
        select(MCPOAuthClient)
        .where(MCPOAuthClient.id == client.id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if client.revoked_at is not None:
        raise oauth_error("invalid_client", "This MCP client was disabled.")
    if not payload.approve:
        return (
            payload.redirect_uri
            + "?"
            + urlencode(
                {"error": "access_denied", "state": payload.state, "iss": issuer}
            )
        )
    step_up = ApiTokenCreateRequest(
        name="MCP delegation",
        scopes=scopes,
        current_password=payload.current_password,
        code=payload.mfa_code,
    )
    _enforce_browser_session_step_up(request, step_up, user, db)
    _enforce_requested_token_scopes_authorized(request, user, scopes)
    now = datetime.now(timezone.utc)
    from app.services.mcp_oauth_lifecycle import (
        MAX_RETAINED_USER_GRANTS,
        prune_terminal_grants,
    )

    prune_terminal_grants(db, now=now, user_id=user.id)
    retained = (
        db.scalar(
            select(func.count())
            .select_from(ApiToken)
            .join(MCPDelegation, MCPDelegation.token_id == ApiToken.id)
            .where(ApiToken.user_id == user.id)
        )
        or 0
    )
    pending = (
        db.scalar(
            select(func.count())
            .select_from(MCPOAuthCode)
            .where(
                MCPOAuthCode.user_id == user.id,
                MCPOAuthCode.expires_at > now,
                MCPOAuthCode.used_at.is_(None),
            )
        )
        or 0
    )
    if retained + pending >= MAX_RETAINED_USER_GRANTS:
        raise oauth_error(
            "delegation_capacity",
            "Too many retained MCP authorizations. Revoke unused grants and allow terminal credential retention to clear before authorizing again.",
            429,
        )
    expired = (
        select(MCPOAuthCode.code_hash).where(MCPOAuthCode.expires_at < now).limit(100)
    )
    db.execute(delete(MCPOAuthCode).where(MCPOAuthCode.code_hash.in_(expired)))
    count = (
        db.scalar(
            select(func.count())
            .select_from(MCPOAuthCode)
            .where(MCPOAuthCode.user_id == user.id, MCPOAuthCode.expires_at > now)
        )
        or 0
    )
    if count >= 20:
        raise oauth_error(
            "authorization_capacity",
            "Too many recent authorization requests. Wait five minutes and retry.",
            429,
        )
    raw = secrets.token_urlsafe(40)
    db.add(
        MCPOAuthCode(
            code_hash=hashlib.sha256(raw.encode()).hexdigest(),
            client_id=client.id,
            user_id=user.id,
            auth_token_version=user.auth_token_version or 0,
            redirect_uri=payload.redirect_uri,
            resource=payload.resource,
            challenge=payload.code_challenge,
            scopes=scopes,
            label_cap_json={
                "enforced": access.enforced,
                "allowed_label_ids": [str(value) for value in access.allowed_label_ids],
            },
            expires_at=now + timedelta(minutes=5),
        )
    )
    record_audit(
        db,
        actor_user_id=user.id,
        action="mcp.oauth.consent",
        resource_type="mcp_client",
        resource_id=str(client.id),
        metadata={"scopes": scopes},
    )
    return (
        payload.redirect_uri
        + "?"
        + urlencode({"code": raw, "state": payload.state, "iss": issuer})
    )


def exchange_code(db: Session, payload) -> dict:
    _, resource = oauth_urls()
    if payload.resource != resource:
        raise oauth_error("invalid_target", "Incorrect MCP resource.")
    digest = hashlib.sha256(payload.code.encode()).hexdigest()
    initial = db.get(MCPOAuthCode, digest)
    if initial is None:
        raise oauth_error("invalid_grant", "Authorization code is invalid or expired.")
    # Acquire IAM fence before mutable user and one-time code row locks.
    user = db.get(User, initial.user_id)
    if user is None:
        raise oauth_error("invalid_grant", "Authorization is no longer valid.")
    authorization = authorization_context_for_user(
        db, user, credential_scopes=initial.scopes
    )
    fence_authorization_context(db, authorization)
    user = db.scalar(
        select(User)
        .where(User.id == initial.user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    client = db.scalar(
        select(MCPOAuthClient)
        .where(MCPOAuthClient.id == payload.client_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    code = db.scalar(
        select(MCPOAuthCode)
        .where(MCPOAuthCode.code_hash == digest)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(timezone.utc)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(payload.code_verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    if (
        code is None
        or code.used_at is not None
        or code.expires_at <= now
        or client is None
        or client.revoked_at is not None
        or code.client_id != payload.client_id
        or code.redirect_uri != payload.redirect_uri
        or code.resource != payload.resource
        or user is None
        or not user.is_active
        or not user.is_approved
        or (user.auth_token_version or 0) != code.auth_token_version
        or not hmac.compare_digest(code.challenge, challenge)
        or any(not authorization.has(scope) for scope in code.scopes)
    ):
        raise oauth_error(
            "invalid_grant",
            "Authorization code, PKCE verifier or current access is invalid.",
        )
    prefix = "tlmcp_" + secrets.token_hex(8)
    token = prefix + "_" + secrets.token_urlsafe(32)
    credential = ApiToken(
        id=uuid.uuid4(),
        user_id=user.id,
        name=f"MCP: {client.name}",
        token_prefix=prefix,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        scopes=code.scopes,
        expires_at=now + timedelta(minutes=15),
    )
    db.add(credential)
    db.flush()
    db.add(
        MCPDelegation(
            token_id=credential.id,
            client_id=client.id,
            resource=resource,
            label_cap_json=code.label_cap_json,
        )
    )
    code.used_at = now
    record_audit(
        db,
        actor_user_id=user.id,
        action="mcp.oauth.exchange",
        resource_type="api_token",
        resource_id=str(credential.id),
        metadata={"client_id": str(client.id)},
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 900,
        "scope": " ".join(code.scopes),
    }


def resolve_delegated_principal(request: Request, db: Session, raw: str) -> User:
    from app.api.deps import _ensure_user_can_authenticate

    _, resource = oauth_urls()
    row = db.execute(
        select(ApiToken, MCPDelegation, MCPOAuthClient)
        .join(MCPDelegation, MCPDelegation.token_id == ApiToken.id)
        .join(MCPOAuthClient, MCPOAuthClient.id == MCPDelegation.client_id)
        .where(ApiToken.token_hash == hashlib.sha256(raw.encode()).hexdigest())
    ).one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        raise oauth_error("invalid_token", "Invalid MCP access token.", 401)
    credential, delegation, client = row
    if (
        credential.revoked_at
        or credential.expires_at is None
        or credential.expires_at <= now
        or client.revoked_at
        or delegation.resource != resource
    ):
        raise oauth_error(
            "invalid_token", "MCP access token expired or was revoked.", 401
        )
    user = db.get(User, credential.user_id)
    if user is None:
        raise oauth_error("invalid_token", "MCP access is no longer available.", 401)
    _ensure_user_can_authenticate(user)
    request.state.token_scopes = credential.scopes
    request.state.auth_via_api_token = True
    request.state.auth_via_service_account = False
    request.state.auth_credential_kind = "api_token"
    request.state.api_token_id = credential.id
    request.state.authorization_context = authorization_context_for_user(
        db, user, credential_scopes=credential.scopes
    )
    request.state.authenticated_principal = user
    request.state.mcp_label_cap = delegation.label_cap_json
    return user


def cap_delegated_access(access, cap: dict | None):
    if not cap or not cap.get("enforced"):
        return access
    try:
        allowed = frozenset(uuid.UUID(value) for value in cap["allowed_label_ids"])
    except (ValueError, TypeError, KeyError) as exc:
        raise oauth_error(
            "invalid_token", "MCP delegated data boundary is invalid.", 401
        ) from exc
    return replace(
        access, mode="enforced", allowed_label_ids=access.allowed_label_ids & allowed
    )
