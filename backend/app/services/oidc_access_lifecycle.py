from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_WRITE_INVESTIGATIONS
from app.models.iam import IAMGroupMembership, IAMUserRoleAssignment
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.services.auth_sessions import lock_user_auth_states
from app.services.authorization import (
    AuthorizationContext,
    authorization_context_for_user,
    bump_iam_policy_revision,
)
from app.services.investigation_ownership import (
    InvestigationOwnerReassignmentRequired,
    reconcile_user_investigation_permission_reduction,
)
from app.services.user_access import revoke_user_credentials_with_counts


@dataclass(frozen=True)
class OIDCAccessPurgeResult:
    affected_user_ids: tuple[uuid.UUID, ...] = ()
    access_reduced_user_ids: tuple[uuid.UUID, ...] = ()
    removed_role_assignments: int = 0
    removed_group_memberships: int = 0
    revoked_api_tokens: int = 0
    revoked_auth_sessions: int = 0
    cancelled_pending_mfa_enrollments: int = 0
    cleared_investigation_assignments: int = 0
    iam_policy_revision: int | None = None

    @property
    def affected_user_count(self) -> int:
        return len(self.affected_user_ids)

    @property
    def access_reduced_user_count(self) -> int:
        return len(self.access_reduced_user_ids)


class OIDCAccessPurgeBlocked(RuntimeError):
    def __init__(
        self,
        *,
        user_id: uuid.UUID,
        cause: InvestigationOwnerReassignmentRequired,
    ) -> None:
        self.user_id = user_id
        self.investigations = cause.investigations
        super().__init__(str(cause))


def provider_oidc_source_keys(
    db: Session, provider_id: uuid.UUID
) -> tuple[set[str], set[str]]:
    policy_id = select(OIDCAccessPolicy.id).where(
        OIDCAccessPolicy.provider_id == provider_id
    )
    mapping_set_ids = select(OIDCClaimMappingSet.id).where(
        OIDCClaimMappingSet.access_policy_id.in_(policy_id)
    )
    role_keys = set(
        db.scalars(
            select(OIDCRoleClaimMapping.source_key).where(
                OIDCRoleClaimMapping.mapping_set_id.in_(mapping_set_ids)
            )
        ).all()
    )
    group_keys = set(
        db.scalars(
            select(OIDCGroupClaimMapping.source_key).where(
                OIDCGroupClaimMapping.mapping_set_id.in_(mapping_set_ids)
            )
        ).all()
    )
    return role_keys, group_keys


def purge_oidc_access(
    db: Session,
    *,
    role_source_keys: Iterable[str] = (),
    group_source_keys: Iterable[str] = (),
    user_ids: Iterable[uuid.UUID] | None = None,
    actor_user_id: uuid.UUID | None,
    revoke_credentials: bool = True,
    revocation_reason: str = "oidc_access_reduced",
) -> OIDCAccessPurgeResult:
    """Remove owned OIDC grants and reconcile every resulting access reduction.

    Callers must hold the IAM policy mutation lock before entering this function.
    This keeps grant discovery, deletion, policy revision, ownership checks, and
    credential revocation in one transaction.
    """

    role_keys = frozenset(role_source_keys)
    group_keys = frozenset(group_source_keys)
    limited_user_ids = frozenset(user_ids) if user_ids is not None else None
    role_rows = _role_assignments(db, source_keys=role_keys, user_ids=limited_user_ids)
    group_rows = _group_memberships(
        db, source_keys=group_keys, user_ids=limited_user_ids
    )
    affected_user_ids = tuple(
        sorted(
            {row.user_id for row in (*role_rows, *group_rows)},
            key=lambda value: value.hex,
        )
    )
    if not affected_user_ids:
        return OIDCAccessPurgeResult()

    locked_users = lock_user_auth_states(db, affected_user_ids)
    before_contexts = {
        user_id: authorization_context_for_user(db, user)
        for user_id, user in locked_users.items()
    }

    role_ids = [row.id for row in role_rows]
    group_ids = [row.id for row in group_rows]
    if role_ids:
        db.execute(
            delete(IAMUserRoleAssignment).where(IAMUserRoleAssignment.id.in_(role_ids))
        )
    if group_ids:
        db.execute(
            delete(IAMGroupMembership).where(IAMGroupMembership.id.in_(group_ids))
        )
    db.flush()
    iam_policy_revision = bump_iam_policy_revision(db)

    access_reduced_user_ids: list[uuid.UUID] = []
    revoked_api_tokens = 0
    revoked_auth_sessions = 0
    cancelled_pending_mfa = 0
    cleared_investigation_assignments = 0
    for user_id in affected_user_ids:
        user = locked_users.get(user_id)
        if user is None:
            continue
        before_context = before_contexts[user_id]
        after_context = authorization_context_for_user(db, user)
        if not _access_was_reduced(before_context, after_context):
            continue
        access_reduced_user_ids.append(user_id)
        if (
            SCOPE_WRITE_INVESTIGATIONS in before_context.permissions
            and SCOPE_WRITE_INVESTIGATIONS not in after_context.permissions
        ):
            try:
                investigation_result = (
                    reconcile_user_investigation_permission_reduction(
                        db,
                        user=user,
                        actor_user_id=actor_user_id,
                    )
                )
            except InvestigationOwnerReassignmentRequired as exc:
                raise OIDCAccessPurgeBlocked(user_id=user_id, cause=exc) from exc
            cleared_investigation_assignments += (
                investigation_result.cleared_assignment_count
            )
        if revoke_credentials:
            revoked = revoke_user_credentials_with_counts(
                db,
                user,
                reason=revocation_reason,
            )
            revoked_api_tokens += revoked.api_tokens
            revoked_auth_sessions += revoked.auth_sessions
            cancelled_pending_mfa += revoked.pending_mfa_enrollments

    return OIDCAccessPurgeResult(
        affected_user_ids=affected_user_ids,
        access_reduced_user_ids=tuple(access_reduced_user_ids),
        removed_role_assignments=len(role_ids),
        removed_group_memberships=len(group_ids),
        revoked_api_tokens=revoked_api_tokens,
        revoked_auth_sessions=revoked_auth_sessions,
        cancelled_pending_mfa_enrollments=cancelled_pending_mfa,
        cleared_investigation_assignments=cleared_investigation_assignments,
        iam_policy_revision=iam_policy_revision,
    )


def oidc_access_affected_user_ids(
    db: Session,
    *,
    role_source_keys: Iterable[str] = (),
    group_source_keys: Iterable[str] = (),
) -> tuple[uuid.UUID, ...]:
    role_keys = frozenset(role_source_keys)
    group_keys = frozenset(group_source_keys)
    user_ids: set[uuid.UUID] = set()
    if role_keys:
        user_ids.update(
            db.scalars(
                select(IAMUserRoleAssignment.user_id).where(
                    IAMUserRoleAssignment.source == "oidc",
                    IAMUserRoleAssignment.source_key.in_(role_keys),
                )
            ).all()
        )
    if group_keys:
        user_ids.update(
            db.scalars(
                select(IAMGroupMembership.user_id).where(
                    IAMGroupMembership.source == "oidc",
                    IAMGroupMembership.source_key.in_(group_keys),
                )
            ).all()
        )
    return tuple(sorted(user_ids, key=lambda value: value.hex))


def _access_was_reduced(
    before: AuthorizationContext, after: AuthorizationContext
) -> bool:
    before_roles = {(role.id, role.key) for role in before.roles}
    after_roles = {(role.id, role.key) for role in after.roles}
    return bool(
        before.permissions - after.permissions
        or set(before.groups) - set(after.groups)
        or before_roles - after_roles
    )


def _role_assignments(
    db: Session,
    *,
    source_keys: frozenset[str],
    user_ids: frozenset[uuid.UUID] | None,
) -> list[IAMUserRoleAssignment]:
    if not source_keys:
        return []
    statement = select(IAMUserRoleAssignment).where(
        IAMUserRoleAssignment.source == "oidc",
        IAMUserRoleAssignment.source_key.in_(source_keys),
    )
    if user_ids is not None:
        if not user_ids:
            return []
        statement = statement.where(IAMUserRoleAssignment.user_id.in_(user_ids))
    return list(
        db.scalars(statement.order_by(IAMUserRoleAssignment.id).with_for_update()).all()
    )


def _group_memberships(
    db: Session,
    *,
    source_keys: frozenset[str],
    user_ids: frozenset[uuid.UUID] | None,
) -> list[IAMGroupMembership]:
    if not source_keys:
        return []
    statement = select(IAMGroupMembership).where(
        IAMGroupMembership.source == "oidc",
        IAMGroupMembership.source_key.in_(source_keys),
    )
    if user_ids is not None:
        if not user_ids:
            return []
        statement = statement.where(IAMGroupMembership.user_id.in_(user_ids))
    return list(
        db.scalars(statement.order_by(IAMGroupMembership.id).with_for_update()).all()
    )


__all__ = [
    "OIDCAccessPurgeBlocked",
    "OIDCAccessPurgeResult",
    "oidc_access_affected_user_ids",
    "provider_oidc_source_keys",
    "purge_oidc_access",
]
