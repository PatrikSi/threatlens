from __future__ import annotations

import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import SCOPE_WRITE_INVESTIGATIONS
from app.models.iam import (
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.investigation import InvestigationMember
from app.models.user import User

OWNER_MEMBER_ROLE = "owner"


def eligible_investigation_owner_ids_query(
    investigation_id: uuid.UUID,
    *,
    excluding_user_id: uuid.UUID | None = None,
):
    """Return eligible owner IDs for mutation guards and IAM reconciliation."""

    direct_write_grant = exists(
        select(1)
        .select_from(IAMUserRoleAssignment)
        .join(IAMRole, IAMRole.id == IAMUserRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMUserRoleAssignment.user_id == User.id,
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
            IAMUserRoleAssignment.source == "local",
        )
    )
    group_write_grant = exists(
        select(1)
        .select_from(IAMGroupMembership)
        .join(
            IAMGroupRoleAssignment,
            IAMGroupRoleAssignment.group_id == IAMGroupMembership.group_id,
        )
        .join(IAMRole, IAMRole.id == IAMGroupRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMGroupMembership.user_id == User.id,
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
            IAMGroupMembership.source == "local",
        )
    )
    query = (
        select(InvestigationMember.user_id)
        .join(User, User.id == InvestigationMember.user_id)
        .where(
            InvestigationMember.investigation_id == investigation_id,
            InvestigationMember.role == OWNER_MEMBER_ROLE,
            User.is_active.is_(True),
            User.is_approved.is_(True),
            or_(
                User.role.in_((ROLE_ADMIN, ROLE_ANALYST)),
                direct_write_grant,
                group_write_grant,
            ),
        )
    )
    if excluding_user_id is not None:
        query = query.where(InvestigationMember.user_id != excluding_user_id)
    return query


def has_durable_investigation_write_access(
    db: Session,
    user: User,
    *,
    role: str | None = None,
    is_active: bool | None = None,
    is_approved: bool | None = None,
) -> bool:
    """Return whether a user can safely hold persistent investigation ownership."""

    effective_role = role if role is not None else user.role
    effective_active = is_active if is_active is not None else user.is_active
    effective_approved = is_approved if is_approved is not None else user.is_approved
    if not effective_active or not effective_approved:
        return False
    if effective_role in (ROLE_ADMIN, ROLE_ANALYST):
        return True
    direct_write_grant = exists(
        select(1)
        .select_from(IAMUserRoleAssignment)
        .join(IAMRole, IAMRole.id == IAMUserRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMUserRoleAssignment.user_id == user.id,
            IAMUserRoleAssignment.source == "local",
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
        )
    )
    group_write_grant = exists(
        select(1)
        .select_from(IAMGroupMembership)
        .join(
            IAMGroupRoleAssignment,
            IAMGroupRoleAssignment.group_id == IAMGroupMembership.group_id,
        )
        .join(IAMRole, IAMRole.id == IAMGroupRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMGroupMembership.user_id == user.id,
            IAMGroupMembership.source == "local",
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
        )
    )
    return bool(db.scalar(select(or_(direct_write_grant, group_write_grant))))


__all__ = [
    "eligible_investigation_owner_ids_query",
    "has_durable_investigation_write_access",
]
