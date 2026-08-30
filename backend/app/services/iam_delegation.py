from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.iam import (
    IAMGroupRoleAssignment,
    IAMRole,
    IAMRolePermission,
)
from app.services.authorization import AuthorizationContext


class IAMDelegationDenied(PermissionError):
    code = "iam_delegation_denied"

    def __init__(self, missing_permissions: Iterable[str]) -> None:
        self.missing_permissions = tuple(sorted(set(missing_permissions)))
        super().__init__(
            "You may delegate only permissions currently available to this session."
        )


def require_delegable_permissions(
    authorization: AuthorizationContext,
    permissions: Iterable[str],
) -> None:
    missing = [
        permission for permission in permissions if not authorization.has(permission)
    ]
    if missing:
        raise IAMDelegationDenied(missing)


def require_delegable_role(
    db: Session,
    authorization: AuthorizationContext,
    role_id: uuid.UUID,
) -> None:
    permissions = db.scalars(
        select(IAMRolePermission.permission)
        .join(IAMRole, IAMRole.id == IAMRolePermission.role_id)
        .where(IAMRole.id == role_id, IAMRole.is_system.is_(False))
    ).all()
    require_delegable_permissions(authorization, permissions)


def require_delegable_group(
    db: Session,
    authorization: AuthorizationContext,
    group_id: uuid.UUID,
) -> None:
    permissions = db.scalars(
        select(IAMRolePermission.permission)
        .join(
            IAMGroupRoleAssignment,
            IAMGroupRoleAssignment.role_id == IAMRolePermission.role_id,
        )
        .where(IAMGroupRoleAssignment.group_id == group_id)
    ).all()
    require_delegable_permissions(authorization, permissions)


__all__ = [
    "IAMDelegationDenied",
    "require_delegable_group",
    "require_delegable_permissions",
    "require_delegable_role",
]
