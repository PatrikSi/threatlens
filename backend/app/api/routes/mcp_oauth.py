"""Pre-registered MCP OAuth clients and explicit browser consent."""

import asyncio
from datetime import datetime, timezone
from urllib.parse import parse_qsl
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import (
    get_admin_user,
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.api.database_operations import interactive_request as _operation
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.mcp_oauth import MCPDelegation, MCPOAuthClient
from app.models.user import User
from app.schemas.mcp_oauth import (
    OAuthAuthorization,
    OAuthClientCreate,
    OAuthConsent,
    OAuthTokenExchange,
)
from app.services import mcp_oauth as service
from app.services.audit import record_audit
from app.services.mcp_oauth_lifecycle import fence_admin_request
from app.services.authorization import (
    lock_iam_policy_for_mutation,
    bump_iam_policy_revision,
)
from app.services.data_access_policy import DataAccessContext

router = APIRouter(prefix="/mcp/oauth", tags=["MCP authorization"])
discovery_router = APIRouter(tags=["MCP authorization discovery"])
TOKEN_BODY_TIMEOUT_SECONDS = 5.0


def _json(value: dict, status: int = 200, *, headers: dict[str, str] | None = None):
    return JSONResponse(
        value,
        status_code=status,
        headers={**(headers or {}), "Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@discovery_router.get("/.well-known/oauth-authorization-server")
def mcp_authorization_metadata():
    issuer, _ = service.oauth_urls()
    return _json(
        {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/mcp/authorize",
            "token_endpoint": issuer + "/api/v1/mcp/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": sorted(service.OAUTH_SCOPES),
            "authorization_response_iss_parameter_supported": True,
        }
    )


@discovery_router.get("/.well-known/oauth-protected-resource/api/v1/mcp")
def mcp_protected_resource_metadata():
    issuer, resource = service.oauth_urls()
    return _json(
        {
            "resource": resource,
            "authorization_servers": [issuer],
            "scopes_supported": ["read:mcp", "read:items"],
            "bearer_methods_supported": ["header"],
        }
    )


@router.get("/clients")
def list_mcp_oauth_clients(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_permissions("read:tokens")),
):
    service.oauth_urls()
    return [
        {
            "client_id": str(row.id),
            "name": row.name,
            "redirect_uris": row.redirect_uris,
            "revoked_at": row.revoked_at,
        }
        for row in db.scalars(
            select(MCPOAuthClient).order_by(MCPOAuthClient.created_at).limit(100)
        )
    ]


@router.post("/clients", status_code=201)
def register_mcp_oauth_client(
    payload: OAuthClientCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user),
    _scope: User = Depends(require_permissions("write:tokens")),
):
    service.oauth_urls()

    def run():
        lock_iam_policy_for_mutation(db)
        fence_admin_request(db, request, user)
        if (db.scalar(select(func.count()).select_from(MCPOAuthClient)) or 0) >= 100:
            raise service.oauth_error(
                "client_capacity", "At most 100 MCP clients can be registered.", 429
            )
        row = MCPOAuthClient(
            id=uuid.uuid4(),
            name=payload.name.strip(),
            redirect_uris=payload.redirect_uris,
        )
        db.add(row)
        record_audit(
            db,
            actor_user_id=user.id,
            action="mcp.client.create",
            resource_type="mcp_client",
            resource_id=str(row.id),
        )
        return {
            "client_id": str(row.id),
            "name": row.name,
            "redirect_uris": row.redirect_uris,
        }

    return _operation(db, run)


@router.delete("/clients/{client_id}", status_code=204)
def revoke_mcp_oauth_client(
    client_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user),
    _scope: User = Depends(require_permissions("write:tokens")),
):
    def run():
        # Existing MCP reads retain a shared IAM fence through bounded transfer.
        # The exclusive policy fence therefore serializes bulk client revocation.
        lock_iam_policy_for_mutation(db)
        fence_admin_request(db, request, user)
        row = db.get(MCPOAuthClient, client_id)
        if row is None:
            raise service.oauth_error("client_not_found", "MCP client not found.", 404)
        row.revoked_at = row.revoked_at or datetime.now(timezone.utc)
        ids = select(MCPDelegation.token_id).where(MCPDelegation.client_id == client_id)
        db.execute(
            update(ApiToken)
            .where(ApiToken.id.in_(ids), ApiToken.revoked_at.is_(None))
            .values(revoked_at=row.revoked_at)
        )
        bump_iam_policy_revision(db)
        record_audit(
            db,
            actor_user_id=user.id,
            action="mcp.client.revoke",
            resource_type="mcp_client",
            resource_id=str(row.id),
        )

    _operation(db, run)


@router.post("/consent-preview")
def preview_mcp_consent(
    payload: OAuthAuthorization,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permissions("read:mcp", "read:items")),
):
    client, scopes, issuer = service.validate_authorization(db, payload)
    return {
        "client_name": client.name,
        "scopes": scopes,
        "resource": payload.resource,
        "redirect_uri": payload.redirect_uri,
        "issuer": issuer,
        "expires_in": 900,
    }


@router.post("/authorize")
def authorize_mcp_client(
    payload: OAuthConsent,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("write:tokens", "read:mcp", "read:items")),
    access: DataAccessContext = Depends(get_data_access_context),
):
    def run():
        redirect = service.grant_code(
            db,
            request=request,
            payload=payload,
            user=user,
            authorization=get_authorization_context(request),
            access=access,
        )
        return {"redirect_uri": redirect}

    return _json(_operation(db, run))


@router.post("/token")
async def exchange_mcp_authorization_code(
    request: Request, db: Session = Depends(get_db)
):
    # Streaming request cap also protects deployments which bypass the web proxy.
    service.oauth_urls()
    if (
        request.headers.get("content-type", "").split(";", 1)[0].lower()
        != "application/x-www-form-urlencoded"
    ):
        return _json(
            {
                "error": "invalid_request",
                "error_description": "Use application/x-www-form-urlencoded.",
            },
            400,
        )
    body = bytearray()
    try:
        async with asyncio.timeout(TOKEN_BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return _json(
                        {
                            "error": "invalid_request",
                            "error_description": "Token request is too large.",
                        },
                        413,
                    )
    except TimeoutError:
        return _json(
            {
                "error": "invalid_request",
                "error_description": "Token request body did not arrive before the deadline. Retry with a complete request.",
            },
            408,
        )
    try:
        pairs = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=10,
        )
        if len(dict(pairs)) != len(pairs):
            raise ValueError("duplicate parameters")
        payload = OAuthTokenExchange.model_validate(dict(pairs))
    except (ValueError, UnicodeError, ValidationError):
        return _json(
            {
                "error": "invalid_request",
                "error_description": "Invalid token exchange parameters.",
            },
            400,
        )
    from starlette.concurrency import run_in_threadpool
    from app.core.api_errors import ApiHTTPException

    try:
        result = await run_in_threadpool(
            lambda: _operation(db, lambda: service.exchange_code(db, payload))
        )
        return _json(result)
    except ApiHTTPException as exc:
        return _json(
            {
                "error": getattr(exc, "error_code", "invalid_grant"),
                "error_description": str(exc.detail),
            },
            exc.status_code,
            headers={
                key: value
                for key, value in (exc.headers or {}).items()
                if key.lower() in {"retry-after", "www-authenticate"}
            },
        )
