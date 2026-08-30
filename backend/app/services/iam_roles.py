from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.iam import (
    IAMGroupRoleAssignment,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.user import User
from app.schemas.iam import (
    RoleResponse,
    RoleUpdateRequest,
    RoleWriteRequest,
    UserRoleAssignmentResponse,
)
from app.services.authorization import bump_iam_policy_revision, role_permissions


class IAMRoleError(RuntimeError):
    code = "iam_role_error"


class IAMRoleNotFound(IAMRoleError):
    code = "iam_role_not_found"


class IAMUserNotFound(IAMRoleError):
    code = "iam_user_not_found"


class IAMRoleConflict(IAMRoleError):
    code = "iam_role_conflict"


class IAMRoleRevisionConflict(IAMRoleError):
    code = "iam_role_revision_conflict"

    def __init__(self, role: IAMRole):
        super().__init__(
            "This role changed after it was loaded. Reload it and apply the intended changes again."
        )
        self.current_revision = role.revision


class IAMSystemRoleImmutable(IAMRoleError):
    code = "iam_system_role_immutable"


@dataclass(frozen=True)
class AssignmentResult:
    assignment: IAMUserRoleAssignment
    role: IAMRole
    created: bool


def list_roles(db: Session) -> list[RoleResponse]:
    roles = list(
        db.scalars(
            select(IAMRole).order_by(IAMRole.is_system.desc(), IAMRole.name, IAMRole.id)
        ).all()
    )
    return [_role_response(db, role) for role in roles]


def get_role_response(db: Session, role_id: uuid.UUID) -> RoleResponse:
    role = db.get(IAMRole, role_id)
    if role is None:
        raise IAMRoleNotFound("Role not found.")
    return _role_response(db, role)


def list_user_role_assignments(
    db: Session, user_id: uuid.UUID
) -> list[UserRoleAssignmentResponse]:
    rows = db.execute(
        select(IAMUserRoleAssignment, IAMRole)
        .join(IAMRole, IAMRole.id == IAMUserRoleAssignment.role_id)
        .where(IAMUserRoleAssignment.user_id == user_id)
        .order_by(IAMRole.name, IAMUserRoleAssignment.created_at)
    ).all()
    return [
        UserRoleAssignmentResponse(
            id=assignment.id,
            user_id=assignment.user_id,
            role_id=role.id,
            role_key=role.key,
            role_name=role.name,
            role_revision=role.revision,
            source=assignment.source,
            source_key=assignment.source_key,
            created_at=assignment.created_at,
        )
        for assignment, role in rows
    ]


def create_role(
    db: Session, *, payload: RoleWriteRequest, actor_user_id: uuid.UUID
) -> IAMRole:
    role = IAMRole(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        is_system=False,
        revision=1,
        created_by_user_id=actor_user_id,
    )
    db.add(role)
    try:
        db.flush()
        _replace_role_permissions(db, role.id, payload.permissions)
        bump_iam_policy_revision(db)
    except IntegrityError as exc:
        raise IAMRoleConflict("A role with this key already exists.") from exc
    return role


def update_role(
    db: Session, *, role_id: uuid.UUID, payload: RoleUpdateRequest
) -> IAMRole:
    role = db.scalar(select(IAMRole).where(IAMRole.id == role_id).with_for_update())
    if role is None:
        raise IAMRoleNotFound("Role not found.")
    if role.is_system:
        raise IAMSystemRoleImmutable(
            "Built-in roles are sealed to preserve upgrade and break-glass behavior. Clone the role instead."
        )
    if role.revision != payload.expected_revision:
        raise IAMRoleRevisionConflict(role)
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    if payload.permissions is not None:
        _replace_role_permissions(db, role.id, payload.permissions)
    role.revision += 1
    db.add(role)
    bump_iam_policy_revision(db)
    db.flush()
    return role


def delete_role(db: Session, *, role_id: uuid.UUID) -> IAMRole:
    role = db.scalar(select(IAMRole).where(IAMRole.id == role_id).with_for_update())
    if role is None:
        raise IAMRoleNotFound("Role not found.")
    if role.is_system:
        raise IAMSystemRoleImmutable("Built-in roles cannot be deleted.")
    assignment_count = int(
        db.scalar(
            select(func.count(IAMUserRoleAssignment.id)).where(
                IAMUserRoleAssignment.role_id == role.id
            )
        )
        or 0
    )
    group_count = int(
        db.scalar(
            select(func.count(IAMGroupRoleAssignment.id)).where(
                IAMGroupRoleAssignment.role_id == role.id
            )
        )
        or 0
    )
    if assignment_count or group_count:
        raise IAMRoleConflict(
            "Role is still assigned. Remove its user and group assignments before deleting it."
        )
    db.delete(role)
    bump_iam_policy_revision(db)
    db.flush()
    return role


def assign_role_to_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    expected_role_revision: int | None = None,
) -> AssignmentResult:
    user = db.get(User, user_id)
    if user is None:
        raise IAMUserNotFound("User not found.")
    role = db.scalar(
        select(IAMRole).where(IAMRole.id == role_id).with_for_update(read=True)
    )
    if role is None:
        raise IAMRoleNotFound("Role not found.")
    if role.is_system:
        raise IAMSystemRoleImmutable(
            "Built-in roles remain managed through the compatibility role field. Assign a custom role instead."
        )
    if expected_role_revision is not None and role.revision != expected_role_revision:
        raise IAMRoleRevisionConflict(role)
    existing = db.scalar(
        select(IAMUserRoleAssignment).where(
            IAMUserRoleAssignment.user_id == user_id,
            IAMUserRoleAssignment.role_id == role_id,
            IAMUserRoleAssignment.source == "local",
            IAMUserRoleAssignment.source_key == "",
        )
    )
    if existing is not None:
        raise IAMRoleConflict("This role assignment already exists.")
    assignment = IAMUserRoleAssignment(
        user_id=user_id,
        role_id=role_id,
        source="local",
        source_key="",
        assigned_by_user_id=actor_user_id,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        if _integrity_constraint_name(exc) == "uq_iam_user_role_assignments_origin":
            raise IAMRoleConflict("This role assignment already exists.") from exc
        raise IAMRoleConflict(
            "The referenced user or role changed while the assignment was created. Reload access policy and retry."
        ) from exc
    bump_iam_policy_revision(db)
    return AssignmentResult(assignment=assignment, role=role, created=True)


def remove_role_from_user(
    db: Session, *, user_id: uuid.UUID, assignment_id: uuid.UUID
) -> IAMUserRoleAssignment:
    assignment = db.scalar(
        select(IAMUserRoleAssignment)
        .where(
            IAMUserRoleAssignment.id == assignment_id,
            IAMUserRoleAssignment.user_id == user_id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise IAMRoleNotFound("Role assignment not found.")
    if assignment.source != "local":
        raise IAMRoleConflict(
            "Identity-provider assignments must be changed through the provider mapping."
        )
    db.delete(assignment)
    bump_iam_policy_revision(db)
    db.flush()
    return assignment


def _replace_role_permissions(
    db: Session, role_id: uuid.UUID, permissions: list[str]
) -> None:
    db.execute(delete(IAMRolePermission).where(IAMRolePermission.role_id == role_id))
    db.add_all(
        IAMRolePermission(role_id=role_id, permission=permission)
        for permission in permissions
    )
    db.flush()


def _role_response(db: Session, role: IAMRole) -> RoleResponse:
    if role.is_system:
        assignment_count = int(
            db.scalar(select(func.count(User.id)).where(User.role == role.key)) or 0
        )
    else:
        assignment_count = int(
            db.scalar(
                select(func.count(IAMUserRoleAssignment.id)).where(
                    IAMUserRoleAssignment.role_id == role.id
                )
            )
            or 0
        )
    group_count = int(
        db.scalar(
            select(func.count(IAMGroupRoleAssignment.id)).where(
                IAMGroupRoleAssignment.role_id == role.id
            )
        )
        or 0
    )
    return RoleResponse(
        id=role.id,
        key=role.key,
        name=role.name,
        description=role.description,
        permissions=sorted(role_permissions(db, role.id)),
        is_system=role.is_system,
        revision=role.revision,
        assignment_count=assignment_count,
        group_count=group_count,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diagnostic, "constraint_name", None)
