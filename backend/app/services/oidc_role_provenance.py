from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.rbac import ALL_ROLES, ROLE_VIEWER
from app.models.oidc import ExternalIdentity
from app.models.user import PROVISIONING_SOURCE_OIDC, User
from app.services.investigation_ownership import (
    InvestigationOwnerReassignmentRequired,
    reconcile_user_investigation_access_change,
)
from app.services.user_access import (
    LastActiveAdminError,
    ensure_active_approved_admin_remains,
)


@dataclass(frozen=True)
class OIDCRoleReversionResult:
    managed: bool = False
    changed: bool = False
    legacy_provenance: bool = False
    manual_override: bool = False
    previous_role: str | None = None
    resulting_role: str | None = None
    cleared_investigation_assignments: int = 0


class OIDCRoleReversionBlocked(RuntimeError):
    code = "oidc_role_reversion_blocked"

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        investigation_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.investigation_count = investigation_count


def mark_oidc_role_synchronized(
    identity: ExternalIdentity,
    *,
    user: User,
    applied_role: str,
    previous_role: str | None,
) -> None:
    """Record enough provenance to undo a fixed-role synchronization safely."""

    if applied_role not in ALL_ROLES:
        raise ValueError("OIDC synchronized role is invalid")
    if identity.role_sync_provenance is None:
        identity.role_sync_provenance = "tracked"
        identity.role_sync_previous_role = (
            None
            if user.provisioning_source == PROVISIONING_SOURCE_OIDC
            else previous_role or user.role
        )
    # A legacy baseline is unknowable even after a later OIDC transition. Only
    # an explicit administrator role confirmation may clear that state.
    identity.role_sync_applied_role = applied_role
    identity.role_sync_updated_at = datetime.now(timezone.utc)


def clear_oidc_role_provenance(identity: ExternalIdentity) -> None:
    identity.role_sync_provenance = None
    identity.role_sync_previous_role = None
    identity.role_sync_applied_role = None
    identity.role_sync_updated_at = None


def revert_oidc_synchronized_role(
    db: Session,
    *,
    identity: ExternalIdentity,
    user: User,
    actor_user_id: uuid.UUID | None,
    allow_legacy_retention: bool,
) -> OIDCRoleReversionResult:
    provenance = identity.role_sync_provenance
    applied_role = identity.role_sync_applied_role
    if provenance is None or applied_role is None:
        return OIDCRoleReversionResult(resulting_role=user.role)
    if provenance == "legacy":
        if not allow_legacy_retention:
            raise OIDCRoleReversionBlocked(
                "ThreatLens cannot determine which local role preceded this legacy "
                "OIDC synchronization. Ask an administrator to disable role sync, "
                "confirm the intended local role in the user directory, and retry.",
                reason="legacy_role_provenance_missing",
            )
        return OIDCRoleReversionResult(
            managed=True,
            legacy_provenance=True,
            previous_role=user.role,
            resulting_role=user.role,
        )
    if user.role != applied_role:
        previous_role = user.role
        clear_oidc_role_provenance(identity)
        return OIDCRoleReversionResult(
            managed=True,
            manual_override=True,
            previous_role=previous_role,
            resulting_role=user.role,
        )

    target_role = (
        ROLE_VIEWER
        if user.provisioning_source == PROVISIONING_SOURCE_OIDC
        else identity.role_sync_previous_role
    )
    if target_role not in ALL_ROLES:
        raise OIDCRoleReversionBlocked(
            "The recorded pre-SSO role is unavailable. An administrator must confirm "
            "the account role before OIDC role management can be removed.",
            reason="recorded_role_invalid",
        )
    previous_role = user.role
    if target_role == previous_role:
        clear_oidc_role_provenance(identity)
        return OIDCRoleReversionResult(
            managed=True,
            previous_role=previous_role,
            resulting_role=target_role,
        )
    try:
        ensure_active_approved_admin_remains(
            db,
            user,
            next_role=target_role,
            next_is_active=user.is_active,
            next_is_approved=user.is_approved,
        )
    except LastActiveAdminError as exc:
        raise OIDCRoleReversionBlocked(
            "The OIDC-managed administrator role cannot be removed until another "
            "active, approved administrator is available.",
            reason="last_active_admin",
        ) from exc
    try:
        investigation_access = reconcile_user_investigation_access_change(
            db,
            user=user,
            next_role=target_role,
            next_is_active=user.is_active,
            next_is_approved=user.is_approved,
            actor_user_id=actor_user_id,
        )
    except InvestigationOwnerReassignmentRequired as exc:
        raise OIDCRoleReversionBlocked(
            str(exc),
            reason="investigation_owner_reassignment_required",
            investigation_count=len(exc.investigations),
        ) from exc
    user.role = target_role
    clear_oidc_role_provenance(identity)
    return OIDCRoleReversionResult(
        managed=True,
        changed=True,
        previous_role=previous_role,
        resulting_role=target_role,
        cleared_investigation_assignments=(
            investigation_access.cleared_assignment_count
        ),
    )


__all__ = [
    "OIDCRoleReversionBlocked",
    "OIDCRoleReversionResult",
    "clear_oidc_role_provenance",
    "mark_oidc_role_synchronized",
    "revert_oidc_synchronized_role",
]
