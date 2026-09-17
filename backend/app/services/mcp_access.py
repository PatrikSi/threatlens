"""Bearer-only MCP access and response publication fences.

This is the scoped local-token compatibility mode, not an OAuth authorization
server. The caller owns the database session through the bounded response send.
Neither these helpers nor the read services may commit that transaction.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from fastapi import Request
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AUTH_API_TOKEN,
    AUTH_SERVICE_ACCOUNT_TOKEN,
    get_authorization_context,
    get_current_principal,
    get_data_access_context,
)
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_MCP
from app.models.api_token import ApiToken
from app.models.iam import IAMGroupMembership, IAMUserRoleAssignment
from app.models.service_account import ServiceAccount, ServiceAccountCredential
from app.models.temporary_elevation import TemporaryElevation
from app.models.user import User
from app.services.audit import record_audit
from app.services.export_job_access import (
    ExportJobAccessDenied,
    capture_export_authorization,
    fence_export_authorization,
)

if TYPE_CHECKING:
    from app.services.mcp_read_contracts import MCPReadContext


_FENCE_KEY = "threatlens_mcp_read_fence"
_MAX_CREDENTIAL_CHARS = 512
_QUERY_CREDENTIAL_NAMES = frozenset(
    {"access_token", "token", "api_token", "api_key", "authorization"}
)
_SAFE_OPERATION = re.compile(r"[A-Za-z][A-Za-z0-9_./:-]{0,95}\Z")


@dataclass(frozen=True)
class _BoundRead:
    principal_type: str
    principal_id: uuid.UUID
    authorization_encrypted: dict | None = None
    source_encrypted: dict | None = None


@dataclass(frozen=True)
class _ReadFence:
    context: MCPReadContext
    transaction: object
    valid_until: datetime | None


@dataclass(frozen=True)
class _MCPAuditIdentity:
    principal_type: str
    principal_id: uuid.UUID | None
    actor_label: str
    credential_kind: str | None
    credential_id: uuid.UUID | None


def _audit_identity_from_principal(
    principal: object,
    *,
    credential_kind: str | None,
    credential_id: uuid.UUID | None,
) -> _MCPAuditIdentity:
    """Read only identity metadata and already loaded attributes, never SQL."""
    if not isinstance(principal, (User, ServiceAccount)):
        return _MCPAuditIdentity("system", None, "Unauthenticated MCP request", None, None)
    state = inspect(principal)
    principal_id = state.identity[0] if state.identity else state.dict.get("id")
    human = isinstance(principal, User)
    label = state.dict.get("email" if human else "name")
    if not isinstance(label, str) or not label.strip():
        label = "MCP user" if human else "MCP service account"
    return _MCPAuditIdentity(
        "user" if human else "service_account",
        principal_id,
        label,
        credential_kind,
        credential_id,
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
    from app.services.mcp_read_contracts import MCPReadContext

    token = parse_mcp_bearer_token(request)
    principal = get_current_principal(request, db, token)
    kind = getattr(request.state, "auth_credential_kind", None)
    # A failed request rolls back its read session, expiring mapped attributes.
    # Capture primitives before any authorization denial so audit persistence on
    # its separate connection cannot refresh an expired principal implicitly.
    request.state.mcp_audit_identity = _audit_identity_from_principal(
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
    scopes = getattr(request.state, "token_scopes", None)
    # Existing broad tokens must not silently opt in to a new export channel.
    if (
        not isinstance(scopes, list)
        or SCOPE_READ_MCP not in scopes
        or authorization is None
        or not authorization.has(SCOPE_READ_MCP)
    ):
        raise _missing_mcp_scope()
    data_access = get_data_access_context(request, principal, db)
    snapshot = capture_export_authorization(request, authorization, data_access)
    context = MCPReadContext(
        principal=principal,
        authorization=authorization,
        data_access=data_access,
        credential_snapshot=snapshot,
        cursor_secret=cursor_secret,
    )
    _fence_current_access(db, context, required_permissions=())
    transaction = db.get_transaction()
    if transaction is None or not transaction.is_active:
        raise ExportJobAccessDenied("MCP publication requires an active transaction")
    db.info[_FENCE_KEY] = _ReadFence(
        context=context,
        transaction=transaction,
        valid_until=_authorization_expiry(db, context),
    )
    return context


def fence_mcp_read_context(
    db: Session,
    context: MCPReadContext,
    *,
    required_permissions: tuple[str, ...] = (),
) -> None:
    """Recheck authority before publication without reopening a lost fence."""
    _require_continuous_transaction(db, context)
    _fence_current_access(db, context, required_permissions=required_permissions)
    mcp_transfer_timeout_seconds(db, context, maximum=1.0)


def _require_continuous_transaction(db: Session, context: MCPReadContext) -> _ReadFence:
    fence = db.info.get(_FENCE_KEY)
    transaction = db.get_transaction()
    if (
        not isinstance(fence, _ReadFence)
        or fence.context is not context
        or transaction is not fence.transaction
        or transaction is None
        or not transaction.is_active
    ):
        raise ExportJobAccessDenied(
            "MCP authorization transaction ended before response publication"
        )
    return fence


def _fence_current_access(
    db: Session,
    context: MCPReadContext,
    *,
    required_permissions: tuple[str, ...],
) -> None:
    snapshot = context.credential_snapshot
    if snapshot.credential_kind not in {AUTH_API_TOKEN, AUTH_SERVICE_ACCOUNT_TOKEN}:
        raise _unauthenticated()
    principal_type = "user" if isinstance(context.principal, User) else "service_account"
    if (
        context.authorization.principal_type != principal_type
        or context.authorization.principal_id != context.principal.id
        or context.data_access.principal_type != principal_type
        or context.data_access.principal_id != context.principal.id
    ):
        raise ExportJobAccessDenied("MCP principal context does not match")
    fence_export_authorization(
        db,
        _BoundRead(principal_type=principal_type, principal_id=context.principal.id),
        context.authorization,
        context.data_access,
        snapshot=snapshot,
        required_permissions=tuple(dict.fromkeys((SCOPE_READ_MCP, *required_permissions))),
    )
    credential = _credential(db, context)
    if credential is None or SCOPE_READ_MCP not in (credential.scopes or ()):
        raise _missing_mcp_scope()


def _credential(db: Session, context: MCPReadContext):
    model = (
        ApiToken
        if context.credential_snapshot.credential_kind == AUTH_API_TOKEN
        else ServiceAccountCredential
    )
    return db.get(model, context.credential_snapshot.credential_id)


def _authorization_expiry(db: Session, context: MCPReadContext) -> datetime | None:
    """Bound transfer by clock-based revocation that row locks cannot prevent."""
    credential = _credential(db, context)
    expiries = [credential.expires_at] if credential is not None else []
    now = datetime.now(timezone.utc)
    if isinstance(context.principal, User):
        for model in (IAMGroupMembership, IAMUserRoleAssignment):
            expiries.append(
                db.scalar(
                    select(func.min(model.oidc_assertion_expires_at)).where(
                        model.user_id == context.principal.id,
                        model.source == "oidc",
                        model.oidc_assertion_expires_at > now,
                    )
                )
            )
        if context.authorization.elevation_ids:
            expiries.append(
                db.scalar(
                    select(func.min(TemporaryElevation.grant_expires_at)).where(
                        TemporaryElevation.id.in_(context.authorization.elevation_ids)
                    )
                )
            )
    normalized = [
        expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry
        for expiry in expiries
        if expiry is not None
    ]
    return min(normalized) if normalized else None


def mcp_transfer_timeout_seconds(
    db: Session,
    context: MCPReadContext,
    *,
    maximum: float,
) -> float:
    """Return the bounded send allowance while the response still owns locks."""
    fence = _require_continuous_transaction(db, context)
    remaining = float(maximum)
    if fence.valid_until is not None:
        remaining = min(
            remaining,
            (fence.valid_until - datetime.now(timezone.utc)).total_seconds(),
        )
    if remaining <= 0:
        raise ExportJobAccessDenied("MCP authorization expired before publication")
    return remaining


def _missing_mcp_scope() -> ApiHTTPException:
    return ApiHTTPException(
        status_code=403,
        detail="MCP requires an explicit read:mcp credential scope and current permission.",
        error_code="mcp_scope_required",
        headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="read:mcp"'},
    )


def record_mcp_audit(
    db: Session,
    request: Request,
    *,
    context: MCPReadContext | None,
    operation: str,
    outcome: Literal["prepared", "denied", "failed"],
    resource_id: uuid.UUID | None = None,
    label_ids: tuple[uuid.UUID, ...] = (),
) -> None:
    """Stage an audit on a separate session, without arguments or retrieved text.

    A success means a response was prepared; network delivery is not asserted.
    Resource IDs may be passed only after access is proven, with handling labels.
    Team resource IDs are omitted until audit reads support the team boundary.
    The caller commits this audit session independently of publication fences.
    """
    if db.info.get(_FENCE_KEY) is not None:
        raise ValueError("MCP audit persistence requires a separate database session")
    operation = operation if _SAFE_OPERATION.fullmatch(operation) else "invalid_request"
    if outcome not in {"prepared", "denied", "failed"}:
        raise ValueError("Invalid MCP audit outcome")
    identity = getattr(request.state, "mcp_audit_identity", None)
    if not isinstance(identity, _MCPAuditIdentity):
        # Direct service tests may provide a context without authenticating a
        # request first. This fallback also avoids loading expired attributes.
        credential = context.credential_snapshot if context is not None else None
        identity = _audit_identity_from_principal(
            context.principal if context is not None else getattr(request.state, "authenticated_principal", None),
            credential_kind=(credential.credential_kind if credential is not None else getattr(request.state, "auth_credential_kind", None)),
            credential_id=(
                credential.credential_id if credential is not None
                else getattr(request.state, "api_token_id", None)
                or getattr(request.state, "service_account_credential_id", None)
            ),
        )
    # Guessed identifiers on denials must not create an audit side channel.
    audited_resource_id = (
        str(resource_id)
        if resource_id is not None and label_ids and outcome == "prepared"
        else None
    )
    record_audit(
        db,
        actor_user_id=identity.principal_id if identity.principal_type == "user" else None,
        actor_principal_type=identity.principal_type,
        actor_principal_id=identity.principal_id,
        actor_label_snapshot=identity.actor_label,
        credential_kind=identity.credential_kind,
        credential_id=identity.credential_id,
        request_id=getattr(request.state, "request_id", None),
        action="mcp.read",
        resource_type="mcp",
        resource_id=audited_resource_id,
        resource_label_snapshot="MCP read operation",
        success=outcome == "prepared",
        metadata={"operation": operation, "outcome": outcome},
        data_access_governed=bool(audited_resource_id),
        data_access_label_ids=label_ids if audited_resource_id else None,
    )
