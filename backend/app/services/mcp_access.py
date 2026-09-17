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

from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_MCP, has_required_scope
from app.models.api_token import ApiToken
from app.models.data_policy import DataPolicyState
from app.models.iam import IAMGroupMembership, IAMPolicyState, IAMUserRoleAssignment
from app.models.service_account import ServiceAccount, ServiceAccountCredential
from app.models.temporary_elevation import TemporaryElevation
from app.models.user import User
from app.services.audit import record_audit
from app.services.authorization import fence_authorization_context
from app.services.data_access_policy import fence_data_access_context
from app.services.export_job_access import (
    ExportJobAccessDenied,
    authorize_export_job,
    fence_export_authorization,
)

if TYPE_CHECKING:
    from app.services.authorization import AuthorizationContext
    from app.services.mcp_read_contracts import MCPReadContext


_FENCE_KEY = "threatlens_mcp_read_fence"
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
    permissions: frozenset[str] = frozenset()
    principal_state: tuple[object, ...] = ()
    nested_transaction: object | None = None


@dataclass(frozen=True)
class _MCPAuditIdentity:
    principal_type: str
    principal_id: uuid.UUID | None
    actor_label: str
    credential_kind: str | None
    credential_id: uuid.UUID | None


def build_mcp_audit_identity(
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


def require_explicit_mcp_scope(
    authorization: AuthorizationContext | None,
    *,
    scopes: object,
) -> None:
    """Require literal credential opt-in as well as current MCP permission."""
    # Existing broad tokens must not silently opt in to a new export channel.
    if (
        not isinstance(scopes, list)
        or SCOPE_READ_MCP not in scopes
        or authorization is None
        or not authorization.has(SCOPE_READ_MCP)
    ):
        raise _missing_mcp_scope()


def bind_mcp_read_context(db: Session, context: MCPReadContext) -> None:
    """Acquire and retain the original transaction for resolved read authority."""
    snapshot_started_at = datetime.now(timezone.utc)
    authorization = _fence_current_access(db, context, required_permissions=())
    transaction = db.get_transaction()
    if transaction is None or not transaction.is_active:
        raise ExportJobAccessDenied("MCP publication requires an active transaction")
    db.info[_FENCE_KEY] = _ReadFence(
        context=context,
        transaction=transaction,
        valid_until=_authorization_expiry(db, context, authorization, snapshot_started_at),
        permissions=authorization.permissions,
        principal_state=_principal_state(context.principal),
        nested_transaction=db.get_nested_transaction(),
    )


def fence_mcp_read_context(
    db: Session,
    context: MCPReadContext,
    *,
    required_permissions: tuple[str, ...] = (),
) -> None:
    """Recheck authority before publication without reopening a lost fence."""
    fence = _require_continuous_transaction(db, context)
    mcp_transfer_timeout_seconds(db, context, maximum=1.0)
    _recheck_retained_authority(db, context, fence, required_permissions)


def authorize_mcp_read_context(
    db: Session,
    context: MCPReadContext,
    *,
    required_permissions: tuple[str, ...],
) -> None:
    """Reuse only this request's continuously held authority, never a lost fence.

    Domain readers may also be called directly without an HTTP-owned fence.
    Those callers still acquire and rebuild the ordinary publication authority.
    A previously bound but ended transaction must fail rather than fall back.
    """
    if _FENCE_KEY in db.info:
        fence_mcp_read_context(db, context, required_permissions=required_permissions)
        return
    principal_type = "user" if isinstance(context.principal, User) else "service_account"
    fence_export_authorization(
        db,
        _BoundRead(principal_type, context.principal.id),
        context.authorization,
        context.data_access,
        snapshot=context.credential_snapshot,
        required_permissions=required_permissions,
    )


def _principal_state(principal: User | ServiceAccount) -> tuple[object, ...]:
    if isinstance(principal, User):
        return (principal.is_active, principal.is_approved, principal.role, principal.auth_token_version)
    return (principal.is_active, principal.revision)


def _recheck_retained_authority(
    db: Session, context: MCPReadContext, fence: _ReadFence,
    required_permissions: tuple[str, ...],
) -> None:
    """Check publication invariants with one query under already retained locks.

    IAM and handling-policy locks prevent concurrent permission changes; owner
    and credential locks prevent account/credential changes until transfer ends.
    Still refresh those rows and revision scalars, so a same-transaction mutation
    cannot accidentally publish with stale authority. Group/role reconstruction
    is unnecessary while these exact locks and their clock expiry remain valid.
    """
    human = isinstance(context.principal, User)
    principal_model = User if human else ServiceAccount
    credential_model = ApiToken if human else ServiceAccountCredential
    owner_column = credential_model.user_id if human else credential_model.service_account_id
    row = db.execute(
        select(principal_model, credential_model, IAMPolicyState.revision, DataPolicyState.revision)
        .join(credential_model, owner_column == principal_model.id)
        .join(IAMPolicyState, IAMPolicyState.id == 1)
        .join(DataPolicyState, DataPolicyState.id == 1)
        .where(
            principal_model.id == context.authorization.principal_id,
            credential_model.id == context.credential_snapshot.credential_id,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise ExportJobAccessDenied("MCP publication authority no longer exists")
    principal, credential, iam_revision, data_revision = row
    if credential.revoked_at is not None:
        raise ExportJobAccessDenied("The accepting credential was revoked")
    expires_at = credential.expires_at
    if expires_at is not None:
        expires_at = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
        if expires_at <= datetime.now(timezone.utc):
            raise ExportJobAccessDenied("The accepting credential expired")
    if SCOPE_READ_MCP not in (credential.scopes or ()):
        raise _missing_mcp_scope()
    if (
        iam_revision != context.authorization.policy_revision
        or data_revision != context.data_access.policy_revision
        or _principal_state(principal) != fence.principal_state
        or any(not context.authorization.has(permission) for permission in required_permissions)
        or any(permission not in fence.permissions for permission in (SCOPE_READ_MCP, *required_permissions))
    ):
        raise ExportJobAccessDenied("MCP authority changed before publication")
    if any(not has_required_scope(set(credential.scopes), permission) for permission in required_permissions):
        raise ExportJobAccessDenied("The accepting credential no longer grants access")


def _require_continuous_transaction(db: Session, context: MCPReadContext) -> _ReadFence:
    fence = db.info.get(_FENCE_KEY)
    transaction = db.get_transaction()
    if (
        not isinstance(fence, _ReadFence)
        or fence.context is not context
        or transaction is not fence.transaction
        or transaction is None
        or not transaction.is_active
        or getattr(db, "get_nested_transaction", lambda: None)() is not fence.nested_transaction
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
) -> AuthorizationContext:
    snapshot = context.credential_snapshot
    if snapshot.credential_kind not in {"api_token", "service_account_token"}:
        raise ExportJobAccessDenied("MCP requires a local API credential")
    principal_type = "user" if isinstance(context.principal, User) else "service_account"
    if (
        context.authorization.principal_type != principal_type
        or context.authorization.principal_id != context.principal.id
        or context.data_access.principal_type != principal_type
        or context.data_access.principal_id != context.principal.id
    ):
        raise ExportJobAccessDenied("MCP principal context does not match")
    fence_authorization_context(db, context.authorization)
    fence_data_access_context(db, context.data_access)
    authorization, access = authorize_export_job(
        db,
        _BoundRead(principal_type=principal_type, principal_id=context.principal.id),
        lock=True,
        snapshot=snapshot,
        required_permissions=tuple(dict.fromkeys((SCOPE_READ_MCP, *required_permissions))),
    )
    if authorization.policy_revision != context.authorization.policy_revision or access != context.data_access:
        raise ExportJobAccessDenied("MCP authorization changed at publication")
    credential = _credential(db, context)
    if credential is None or SCOPE_READ_MCP not in (credential.scopes or ()):
        raise _missing_mcp_scope()
    return authorization


def _credential(db: Session, context: MCPReadContext):
    model = (
        ApiToken
        if context.credential_snapshot.credential_kind == "api_token"
        else ServiceAccountCredential
    )
    return db.get(model, context.credential_snapshot.credential_id)


def _authorization_expiry(
    db: Session,
    context: MCPReadContext,
    authorization: AuthorizationContext,
    snapshot_started_at: datetime,
) -> datetime | None:
    """Bound transfer by clock-based revocation that row locks cannot prevent."""
    credential = _credential(db, context)
    expiries = [credential.expires_at] if credential is not None else []
    if isinstance(context.principal, User):
        for model in (IAMGroupMembership, IAMUserRoleAssignment):
            expiries.append(
                db.scalar(
                    select(func.min(model.oidc_assertion_expires_at)).where(
                        model.user_id == context.principal.id,
                        model.source == "oidc",
                        # Include assertions that expired while the fresh
                        # permission snapshot was being built, not only those
                        # still future-dated when this final query runs.
                        model.oidc_assertion_expires_at > snapshot_started_at,
                    )
                )
            )
        if authorization.elevation_ids:
            expiries.append(
                db.scalar(
                    select(func.min(TemporaryElevation.grant_expires_at)).where(
                        TemporaryElevation.id.in_(authorization.elevation_ids)
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
        identity = build_mcp_audit_identity(
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
