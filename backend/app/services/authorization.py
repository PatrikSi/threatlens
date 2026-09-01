from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import (
    SERVICE_ACCOUNT_PERMISSION_IDS,
    SYSTEM_ROLE_IDS,
    WILDCARD_PERMISSION_IDS,
    expand_permission_grants,
)
from app.core.token_scopes import (
    get_role_api_token_scope_grants,
    has_required_scope,
    normalize_token_scopes,
)
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMPolicyState,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.service_account import (
    ServiceAccount,
    ServiceAccountCredential,
    ServiceAccountRoleAssignment,
)
from app.models.temporary_elevation import (
    TemporaryElevation,
    TemporaryElevationPermission,
)
from app.models.user import User


_AUTHORIZATION_SNAPSHOT_ATTEMPTS = 3


class AuthorizationStateUnavailable(RuntimeError):
    """Raised when IAM state cannot be read without risking a fail-open result."""


@dataclass(frozen=True)
class EffectiveRole:
    id: uuid.UUID | None
    key: str
    name: str
    source: str


@dataclass(frozen=True)
class AuthorizationContext:
    principal_type: str
    principal_id: uuid.UUID
    legacy_role: str | None
    account_eligible: bool
    roles: tuple[EffectiveRole, ...]
    groups: tuple[str, ...]
    grants: frozenset[str]
    credential_grants: frozenset[str] | None
    permissions: frozenset[str]
    provenance: dict[str, tuple[str, ...]]
    policy_revision: int
    elevation_ids: tuple[uuid.UUID, ...] = ()
    durable_grants: frozenset[str] = frozenset()
    elevation_grants: dict[uuid.UUID, frozenset[str]] = field(default_factory=dict)

    @property
    def credential_limited(self) -> bool:
        return self.credential_grants is not None

    def has(self, permission: str) -> bool:
        if not self.account_eligible or not has_required_scope(
            set(self.grants), permission
        ):
            return False
        if self.credential_grants is None:
            return True
        return has_required_scope(set(self.credential_grants), permission)

    def has_durable(self, permission: str) -> bool:
        if not self.account_eligible or not has_required_scope(
            set(self.durable_grants), permission
        ):
            return False
        if self.credential_grants is None:
            return True
        return has_required_scope(set(self.credential_grants), permission)

    def authorizing_elevation_ids(
        self, required_permissions: Iterable[str]
    ) -> tuple[uuid.UUID, ...]:
        contributing: set[uuid.UUID] = set()
        for permission in required_permissions:
            if self.has_durable(permission):
                continue
            for elevation_id, grants in self.elevation_grants.items():
                if has_required_scope(set(grants), permission):
                    contributing.add(elevation_id)
        return tuple(sorted(contributing, key=str))

    def explanation(self, permission: str) -> dict[str, object]:
        principal_allowed = has_required_scope(set(self.grants), permission)
        credential_allowed = self.credential_grants is None or has_required_scope(
            set(self.credential_grants), permission
        )
        allowed = self.account_eligible and principal_allowed and credential_allowed
        if not self.account_eligible:
            reason = "account_ineligible"
        elif not principal_allowed:
            reason = "permission_not_granted"
        elif not credential_allowed:
            reason = "credential_scope_missing"
        else:
            reason = "permission_granted"
        return {
            "permission": permission,
            "allowed": allowed,
            "grant_sources": list(self.provenance.get(permission, ())),
            "policy_revision": self.policy_revision,
            "reason": reason,
        }


def authorization_context_for_user(
    db: Session,
    user: User,
    *,
    credential_scopes: Iterable[str] | None = None,
) -> AuthorizationContext:
    credential_grants = (
        frozenset(normalize_token_scopes(credential_scopes))
        if credential_scopes is not None
        else None
    )
    for _attempt in range(_AUTHORIZATION_SNAPSHOT_ATTEMPTS):
        revision_before = _policy_revision(db)
        current_user = db.scalar(
            select(User)
            .where(User.id == user.id)
            .execution_options(populate_existing=True)
        )
        if current_user is None:
            raise AuthorizationStateUnavailable(
                "The user disappeared while effective access was evaluated. Retry authentication."
            )
        snapshot = _authorization_snapshot_for_user(
            db,
            current_user,
            credential_grants=credential_grants,
            policy_revision=revision_before,
        )
        revision_after = _policy_revision(db)
        if revision_before == revision_after:
            return snapshot
    raise AuthorizationStateUnavailable(
        "Access policy changed repeatedly while permissions were evaluated. Retry the request."
    )


def authorization_context_for_service_account(
    db: Session,
    account: ServiceAccount,
    *,
    credential_id: uuid.UUID,
    credential_scopes: Iterable[str],
) -> AuthorizationContext:
    credential_grants = frozenset(normalize_token_scopes(credential_scopes))
    for _attempt in range(_AUTHORIZATION_SNAPSHOT_ATTEMPTS):
        revision_before = _policy_revision(db)
        snapshot = _authorization_snapshot_for_service_account(
            db,
            account,
            credential_id=credential_id,
            credential_grants=credential_grants,
            policy_revision=revision_before,
        )
        revision_after = _policy_revision(db)
        if revision_before == revision_after:
            return snapshot
    raise AuthorizationStateUnavailable(
        "Access policy changed repeatedly while service-account permissions were evaluated. Retry the request."
    )


def _authorization_snapshot_for_user(
    db: Session,
    user: User,
    *,
    credential_grants: frozenset[str] | None,
    policy_revision: int,
) -> AuthorizationContext:
    clock = database_clock(db)
    base_grants = set(get_role_api_token_scope_grants(user.role))
    account_eligible = bool(user.is_active and user.is_approved)
    roles: list[EffectiveRole] = [
        EffectiveRole(
            id=SYSTEM_ROLE_IDS.get(user.role),
            key=user.role,
            name=_legacy_role_name(user.role),
            source="built-in",
        )
    ]
    groups: set[str] = {"all-users"} if account_eligible else set()
    grants = set(base_grants)
    grant_sources: dict[str, set[str]] = {
        permission: {f"built-in role: {user.role}"}
        for permission in expand_permission_grants(base_grants)
    }

    direct_rows = db.execute(
        select(
            IAMRole.id,
            IAMRole.key,
            IAMRole.name,
            IAMRolePermission.permission,
            IAMUserRoleAssignment.source,
        )
        .join(IAMUserRoleAssignment, IAMUserRoleAssignment.role_id == IAMRole.id)
        .outerjoin(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMUserRoleAssignment.user_id == user.id,
            IAMRole.is_system.is_(False),
            or_(
                IAMUserRoleAssignment.source != "oidc",
                IAMUserRoleAssignment.oidc_assertion_expires_at > clock,
            ),
        )
    ).all()
    seen_role_ids: set[uuid.UUID] = set()
    for row in direct_rows:
        source_label = f"assigned role: {row.name}"
        if row.permission is not None:
            grants.add(row.permission)
            _record_grant_source(grant_sources, row.permission, source_label)
        if row.id not in seen_role_ids:
            seen_role_ids.add(row.id)
            roles.append(
                EffectiveRole(
                    id=row.id,
                    key=row.key,
                    name=row.name,
                    source=row.source,
                )
            )

    membership_rows = db.scalars(
        select(IAMGroup.key)
        .join(IAMGroupMembership, IAMGroupMembership.group_id == IAMGroup.id)
        .where(
            IAMGroupMembership.user_id == user.id,
            or_(
                IAMGroupMembership.source != "oidc",
                IAMGroupMembership.oidc_assertion_expires_at > clock,
            ),
        )
    ).all()
    groups.update(membership_rows)

    group_rows = db.execute(
        select(
            IAMGroup.key.label("group_key"),
            IAMRole.id.label("role_id"),
            IAMRole.key.label("role_key"),
            IAMRole.name.label("role_name"),
            IAMRolePermission.permission,
        )
        .join(
            IAMGroupRoleAssignment,
            IAMGroupRoleAssignment.group_id == IAMGroup.id,
        )
        .join(IAMRole, IAMRole.id == IAMGroupRoleAssignment.role_id)
        .outerjoin(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .join(IAMGroupMembership, IAMGroupMembership.group_id == IAMGroup.id)
        .where(
            IAMGroupMembership.user_id == user.id,
            IAMRole.is_system.is_(False),
            or_(
                IAMGroupMembership.source != "oidc",
                IAMGroupMembership.oidc_assertion_expires_at > clock,
            ),
        )
    ).all()
    for row in group_rows:
        source_label = f"group {row.group_key}: {row.role_name}"
        if row.permission is not None:
            grants.add(row.permission)
            _record_grant_source(grant_sources, row.permission, source_label)
        if row.role_id not in seen_role_ids:
            seen_role_ids.add(row.role_id)
            roles.append(
                EffectiveRole(
                    id=row.role_id,
                    key=row.role_key,
                    name=row.role_name,
                    source=f"group:{row.group_key}",
                )
            )

    durable_grants = frozenset(grants)

    elevation_rows = db.execute(
        select(
            TemporaryElevation.id.label("elevation_id"),
            TemporaryElevation.role_id,
            TemporaryElevation.role_key_snapshot.label("role_key"),
            TemporaryElevation.role_name_snapshot.label("role_name"),
            TemporaryElevationPermission.permission,
        )
        .outerjoin(
            TemporaryElevationPermission,
            TemporaryElevationPermission.elevation_id == TemporaryElevation.id,
        )
        .where(
            TemporaryElevation.target_user_id == user.id,
            TemporaryElevation.status == "approved",
            TemporaryElevation.grant_started_at <= clock,
            TemporaryElevation.grant_expires_at > clock,
        )
    ).all()
    active_elevation_ids: set[uuid.UUID] = set()
    elevation_grants: dict[uuid.UUID, set[str]] = {}
    for row in elevation_rows:
        active_elevation_ids.add(row.elevation_id)
        elevation_grants.setdefault(row.elevation_id, set())
        source_label = f"temporary elevation {row.elevation_id}: {row.role_name}"
        if row.permission is not None:
            grants.add(row.permission)
            elevation_grants[row.elevation_id].add(row.permission)
            _record_grant_source(grant_sources, row.permission, source_label)
        if row.role_id not in seen_role_ids:
            seen_role_ids.add(row.role_id)
            roles.append(
                EffectiveRole(
                    id=row.role_id,
                    key=row.role_key,
                    name=row.role_name,
                    source=f"elevation:{row.elevation_id}",
                )
            )

    principal_permissions = expand_permission_grants(grants)
    provenance = {
        permission: tuple(
            sorted(_sources_for_permission(grant_sources, grants, permission))
        )
        for permission in principal_permissions
    }
    if not account_eligible:
        permissions = frozenset()
    elif credential_grants is None:
        permissions = principal_permissions
    else:
        permissions = frozenset(
            permission
            for permission in principal_permissions
            if has_required_scope(set(credential_grants), permission)
        )
    return AuthorizationContext(
        principal_type="user",
        principal_id=user.id,
        legacy_role=user.role,
        account_eligible=account_eligible,
        roles=tuple(roles),
        groups=tuple(sorted(groups)),
        grants=frozenset(grants),
        credential_grants=credential_grants,
        permissions=permissions,
        provenance=provenance,
        policy_revision=policy_revision,
        elevation_ids=tuple(sorted(active_elevation_ids, key=str)),
        durable_grants=durable_grants,
        elevation_grants={
            elevation_id: frozenset(values)
            for elevation_id, values in elevation_grants.items()
        },
    )


def _authorization_snapshot_for_service_account(
    db: Session,
    account: ServiceAccount,
    *,
    credential_id: uuid.UUID,
    credential_grants: frozenset[str],
    policy_revision: int,
) -> AuthorizationContext:
    clock = database_clock(db)
    account_is_active = db.scalar(
        select(ServiceAccount.is_active)
        .where(ServiceAccount.id == account.id)
        .execution_options(populate_existing=True)
    )
    credential_is_active = db.scalar(
        select(ServiceAccountCredential.id).where(
            ServiceAccountCredential.id == credential_id,
            ServiceAccountCredential.service_account_id == account.id,
            ServiceAccountCredential.revoked_at.is_(None),
            ServiceAccountCredential.expires_at > clock,
        )
    )
    account_eligible = account_is_active is True and credential_is_active is not None
    rows = db.execute(
        select(
            IAMRole.id,
            IAMRole.key,
            IAMRole.name,
            IAMRolePermission.permission,
        )
        .join(
            ServiceAccountRoleAssignment,
            ServiceAccountRoleAssignment.role_id == IAMRole.id,
        )
        .outerjoin(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            ServiceAccountRoleAssignment.service_account_id == account.id,
            IAMRole.is_system.is_(False),
        )
    ).all()
    raw_grants: set[str] = set()
    grant_sources: dict[str, set[str]] = {}
    roles: list[EffectiveRole] = []
    seen_role_ids: set[uuid.UUID] = set()
    for row in rows:
        source_label = f"assigned service-account role: {row.name}"
        if row.permission is not None and row.permission not in WILDCARD_PERMISSION_IDS:
            raw_grants.add(row.permission)
            _record_grant_source(grant_sources, row.permission, source_label)
        if row.id not in seen_role_ids:
            seen_role_ids.add(row.id)
            roles.append(
                EffectiveRole(
                    id=row.id,
                    key=row.key,
                    name=row.name,
                    source="service-account",
                )
            )

    principal_permissions = (
        expand_permission_grants(raw_grants) & SERVICE_ACCOUNT_PERMISSION_IDS
    )
    provenance = {
        permission: tuple(
            sorted(_sources_for_permission(grant_sources, raw_grants, permission))
        )
        for permission in principal_permissions
    }
    permissions = frozenset(
        permission
        for permission in principal_permissions
        if account_eligible and has_required_scope(set(credential_grants), permission)
    )
    return AuthorizationContext(
        principal_type="service_account",
        principal_id=account.id,
        legacy_role=None,
        account_eligible=account_eligible,
        roles=tuple(roles),
        groups=(),
        grants=principal_permissions,
        credential_grants=credential_grants,
        permissions=permissions,
        provenance=provenance,
        policy_revision=policy_revision,
        elevation_ids=(),
        durable_grants=frozenset(raw_grants),
        elevation_grants={},
    )


def lock_iam_policy_for_mutation(db: Session) -> IAMPolicyState:
    state = db.scalar(
        select(IAMPolicyState).where(IAMPolicyState.id == 1).with_for_update()
    )
    if state is None:
        raise AuthorizationStateUnavailable(
            "Access policy state is missing. Restore the database or rerun migrations before changing access."
        )
    return state


def bump_iam_policy_revision(db: Session) -> int:
    state = lock_iam_policy_for_mutation(db)
    state.revision += 1
    db.add(state)
    db.flush()
    return state.revision


def fence_authorization_context(
    db: Session,
    context: AuthorizationContext,
) -> None:
    """Hold the IAM revision stable for the caller's transaction."""

    current_revision = db.scalar(
        select(IAMPolicyState.revision)
        .where(IAMPolicyState.id == 1)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if current_revision is None:
        raise AuthorizationStateUnavailable(
            "Access policy state is unavailable. Restore the database or rerun migrations."
        )
    if int(current_revision) != context.policy_revision:
        raise AuthorizationStateUnavailable(
            "Access policy changed while data access was being authorized. Retry the request."
        )


def role_permissions(db: Session, role_id: uuid.UUID) -> frozenset[str]:
    return frozenset(
        db.scalars(
            select(IAMRolePermission.permission).where(
                IAMRolePermission.role_id == role_id
            )
        ).all()
    )


def database_clock(db: Session):
    return (
        func.clock_timestamp()
        if db.get_bind().dialect.name == "postgresql"
        else func.now()
    )


def _policy_revision(db: Session) -> int:
    revision = db.scalar(select(IAMPolicyState.revision).where(IAMPolicyState.id == 1))
    if revision is None:
        raise AuthorizationStateUnavailable(
            "Access policy state is unavailable. Restore the database or rerun migrations."
        )
    return int(revision)


def _record_grant_source(
    grant_sources: dict[str, set[str]], permission: str, source: str
) -> None:
    grant_sources.setdefault(permission, set()).add(source)


def _sources_for_permission(
    grant_sources: dict[str, set[str]], grants: set[str], permission: str
) -> set[str]:
    sources = set(grant_sources.get(permission, set()))
    for grant, grant_labels in grant_sources.items():
        if grant != permission and has_required_scope({grant}, permission):
            sources.update(grant_labels)
    if not sources and has_required_scope(grants, permission):
        sources.add("inherited wildcard")
    return sources


def _legacy_role_name(role: str) -> str:
    return {
        "admin": "Administrator",
        "analyst": "Analyst",
        "viewer": "Viewer",
    }.get(role, role)


__all__ = [
    "AuthorizationContext",
    "AuthorizationStateUnavailable",
    "EffectiveRole",
    "authorization_context_for_user",
    "authorization_context_for_service_account",
    "bump_iam_policy_revision",
    "database_clock",
    "lock_iam_policy_for_mutation",
    "role_permissions",
]
