"""Compose MCP request credentials with the application's HTTP authentication."""

from fastapi import Request
from sqlalchemy.orm import Session

from app.api.deps import (
    AUTH_API_TOKEN,
    AUTH_SERVICE_ACCOUNT_TOKEN,
    get_authorization_context,
    get_current_principal,
    get_data_access_context,
)
from app.core.api_errors import ApiHTTPException
from app.services.export_job_access import capture_export_authorization
from app.services.mcp_access import (
    bind_mcp_read_context,
    build_mcp_audit_identity,
    require_explicit_mcp_scope,
)
from app.services.mcp_read_contracts import MCPReadContext


_MAX_CREDENTIAL_CHARS = 512
_QUERY_CREDENTIAL_NAMES = frozenset(
    {"access_token", "token", "api_token", "api_key", "authorization"}
)


def _unauthenticated() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=401,
        detail="MCP requires a scoped personal or service-account bearer token.",
        error_code="mcp_bearer_token_required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def parse_mcp_bearer_token(request: Request) -> str:
    """Reject cookie fallback, ambiguous headers, and URI credentials up front."""
    if any(key.lower() in _QUERY_CREDENTIAL_NAMES for key in request.query_params):
        raise _unauthenticated()
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        raise _unauthenticated()
    parts = headers[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthenticated()
    token = parts[1]
    if len(token) > _MAX_CREDENTIAL_CHARS or not token.startswith(("tlp_", "tlsa_")):
        raise _unauthenticated()
    return token


def resolve_mcp_read_context(
    request: Request,
    db: Session,
    *,
    cursor_secret: bytes,
) -> MCPReadContext:
    """Authenticate every request and capture a current, scoped local principal."""
    token = parse_mcp_bearer_token(request)
    principal = get_current_principal(request, db, token)
    kind = getattr(request.state, "auth_credential_kind", None)
    # Rollback expires mapped attributes. Capture primitives before any scope
    # denial so a separate failure audit never refreshes the read session.
    request.state.mcp_audit_identity = build_mcp_audit_identity(
        principal,
        credential_kind=kind,
        credential_id=(
            getattr(request.state, "api_token_id", None)
            or getattr(request.state, "service_account_credential_id", None)
        ),
    )
    if kind not in {AUTH_API_TOKEN, AUTH_SERVICE_ACCOUNT_TOKEN}:
        raise _unauthenticated()
    authorization = get_authorization_context(request)
    require_explicit_mcp_scope(
        authorization, scopes=getattr(request.state, "token_scopes", None),
    )
    data_access = get_data_access_context(request, principal, db)
    snapshot = capture_export_authorization(request, authorization, data_access)
    context = MCPReadContext(
        principal=principal,
        authorization=authorization,
        data_access=data_access,
        credential_snapshot=snapshot,
        cursor_secret=cursor_secret,
    )
    bind_mcp_read_context(db, context)
    return context
