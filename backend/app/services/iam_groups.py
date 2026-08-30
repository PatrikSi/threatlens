from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMRole,
)
from app.models.user import User
from app.models.oidc_access import OIDCGroupClaimMapping
from app.schemas.iam import (
    GroupMemberResponse,
    GroupRoleAssignmentResponse,
    GroupResponse,
    GroupUpdateRequest,
    GroupWriteRequest,
)
from app.services.authorization import bump_iam_policy_revision


class IAMGroupError(RuntimeError):
    code = "iam_group_error"


class IAMGroupNotFound(IAMGroupError):
    code = "iam_group_not_found"


class IAMGroupUserNotFound(IAMGroupError):
    code = "iam_user_not_found"


class IAMGroupRoleNotFound(IAMGroupError):
    code = "iam_role_not_found"


class IAMGroupConflict(IAMGroupError):
    code = "iam_group_conflict"


class IAMGroupRevisionConflict(IAMGroupError):
    code = "iam_group_revision_conflict"

    def __init__(self, group: IAMGroup):
        super().__init__(
            "This group changed after it was loaded. Reload it and apply the intended changes again."
        )
        self.current_revision = group.revision


class IAMGroupRoleRevisionConflict(IAMGroupError):
    code = "iam_role_revision_conflict"

    def __init__(self, role: IAMRole):
        super().__init__(
            "This role changed after it was loaded. Reload access policy and retry the assignment."
        )
        self.current_revision = role.revision


class IAMSystemGroupImmutable(IAMGroupError):
    code = "iam_system_group_immutable"


@dataclass(frozen=True)
class MembershipResult:
    membership: IAMGroupMembership
    created: bool


@dataclass(frozen=True)
class GroupRoleResult:
    assignment: IAMGroupRoleAssignment
    created: bool


def list_groups(db: Session) -> list[GroupResponse]:
    groups = list(
        db.scalars(
            select(IAMGroup).order_by(
                IAMGroup.is_system.desc(), IAMGroup.name, IAMGroup.id
            )
        ).all()
    )
    return [_group_response(db, group) for group in groups]


def get_group_response(db: Session, group_id: uuid.UUID) -> GroupResponse:
    group = db.get(IAMGroup, group_id)
    if group is None:
        raise IAMGroupNotFound("Group not found.")
    return _group_response(db, group)


def list_group_members(db: Session, group_id: uuid.UUID) -> list[GroupMemberResponse]:
    group = db.get(IAMGroup, group_id)
    if group is None:
        raise IAMGroupNotFound("Group not found.")
    if group.is_system and group.key == "all-users":
        users = db.scalars(
            select(User)
            .where(User.is_active.is_(True), User.is_approved.is_(True))
            .order_by(User.email, User.id)
        ).all()
        return [
            GroupMemberResponse(
                id=user.id,
                user_id=user.id,
                email=user.email,
                source="local",
                source_key="__derived_all_users__",
                created_at=user.created_at,
            )
            for user in users
        ]
    rows = db.execute(
        select(IAMGroupMembership, User)
        .join(User, User.id == IAMGroupMembership.user_id)
        .where(IAMGroupMembership.group_id == group.id)
        .order_by(User.email, IAMGroupMembership.created_at)
    ).all()
    return [
        GroupMemberResponse(
            id=membership.id,
            user_id=user.id,
            email=user.email,
            source=membership.source,
            source_key=membership.source_key,
            created_at=membership.created_at,
        )
        for membership, user in rows
    ]


def list_group_role_assignments(
    db: Session, group_id: uuid.UUID
) -> list[GroupRoleAssignmentResponse]:
    if db.get(IAMGroup, group_id) is None:
        raise IAMGroupNotFound("Group not found.")
    rows = db.execute(
        select(IAMGroupRoleAssignment, IAMRole)
        .join(IAMRole, IAMRole.id == IAMGroupRoleAssignment.role_id)
        .where(IAMGroupRoleAssignment.group_id == group_id)
        .order_by(IAMRole.name, IAMGroupRoleAssignment.created_at)
    ).all()
    return [
        GroupRoleAssignmentResponse(
            id=assignment.id,
            group_id=assignment.group_id,
            role_id=role.id,
            role_key=role.key,
            role_name=role.name,
            role_revision=role.revision,
            created_at=assignment.created_at,
        )
        for assignment, role in rows
    ]


def create_group(
    db: Session, *, payload: GroupWriteRequest, actor_user_id: uuid.UUID
) -> IAMGroup:
    group = IAMGroup(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        source="local",
        external_key=None,
        is_system=False,
        revision=1,
        created_by_user_id=actor_user_id,
    )
    db.add(group)
    try:
        db.flush()
        bump_iam_policy_revision(db)
    except IntegrityError as exc:
        raise IAMGroupConflict("A group with this key already exists.") from exc
    return group


def update_group(
    db: Session, *, group_id: uuid.UUID, payload: GroupUpdateRequest
) -> IAMGroup:
    group = db.scalar(select(IAMGroup).where(IAMGroup.id == group_id).with_for_update())
    if group is None:
        raise IAMGroupNotFound("Group not found.")
    if group.is_system:
        raise IAMSystemGroupImmutable("System groups cannot be changed.")
    if group.source != "local":
        raise IAMGroupConflict(
            "Identity-provider groups must be changed through the provider mapping."
        )
    if group.revision != payload.expected_revision:
        raise IAMGroupRevisionConflict(group)
    if payload.name is not None:
        group.name = payload.name
    if payload.description is not None:
        group.description = payload.description
    _mark_group_changed(db, group)
    return group


def delete_group(db: Session, *, group_id: uuid.UUID) -> IAMGroup:
    group = db.scalar(select(IAMGroup).where(IAMGroup.id == group_id).with_for_update())
    if group is None:
        raise IAMGroupNotFound("Group not found.")
    if group.is_system:
        raise IAMSystemGroupImmutable("System groups cannot be deleted.")
    if group.source != "local":
        raise IAMGroupConflict(
            "Identity-provider groups must be removed through the provider mapping."
        )
    oidc_mapping_count = int(
        db.scalar(
            select(func.count(OIDCGroupClaimMapping.id)).where(
                OIDCGroupClaimMapping.group_id == group.id
            )
        )
        or 0
    )
    if oidc_mapping_count:
        raise IAMGroupConflict(
            "Group is referenced by an OIDC claim mapping. Remove that mapping before deleting the group."
        )
    db.delete(group)
    bump_iam_policy_revision(db)
    db.flush()
    return group


def add_group_member(
    db: Session,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> MembershipResult:
    group = _lock_mutable_local_group(db, group_id)
    if db.get(User, user_id) is None:
        raise IAMGroupUserNotFound("User not found.")
    existing = db.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == group.id,
            IAMGroupMembership.user_id == user_id,
            IAMGroupMembership.source == "local",
            IAMGroupMembership.source_key == "",
        )
    )
    if existing is not None:
        raise IAMGroupConflict("This group membership already exists.")
    membership = IAMGroupMembership(
        group_id=group.id,
        user_id=user_id,
        source="local",
        source_key="",
        assigned_by_user_id=actor_user_id,
    )
    db.add(membership)
    try:
        db.flush()
    except IntegrityError as exc:
        if _integrity_constraint_name(exc) == "uq_iam_group_memberships_origin":
            raise IAMGroupConflict("This group membership already exists.") from exc
        raise IAMGroupConflict(
            "The referenced group or user changed while the membership was created. Reload access policy and retry."
        ) from exc
    _mark_group_changed(db, group)
    return MembershipResult(membership, True)


def remove_group_member(
    db: Session, *, group_id: uuid.UUID, membership_id: uuid.UUID
) -> IAMGroupMembership:
    group = _lock_mutable_local_group(db, group_id)
    membership = db.scalar(
        select(IAMGroupMembership)
        .where(
            IAMGroupMembership.id == membership_id,
            IAMGroupMembership.group_id == group.id,
        )
        .with_for_update()
    )
    if membership is None:
        raise IAMGroupNotFound("Group membership not found.")
    if membership.source != "local":
        raise IAMGroupConflict(
            "Identity-provider memberships must be changed through the provider mapping."
        )
    db.delete(membership)
    _mark_group_changed(db, group)
    return membership


def add_group_role(
    db: Session,
    *,
    group_id: uuid.UUID,
    role_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    expected_role_revision: int | None = None,
) -> GroupRoleResult:
    group = _lock_mutable_local_group(db, group_id)
    role = db.scalar(
        select(IAMRole).where(IAMRole.id == role_id).with_for_update(read=True)
    )
    if role is None:
        raise IAMGroupRoleNotFound("Role not found.")
    if role.is_system:
        raise IAMGroupConflict(
            "Built-in roles cannot be granted through groups. Assign a custom role instead."
        )
    if expected_role_revision is not None and role.revision != expected_role_revision:
        raise IAMGroupRoleRevisionConflict(role)
    existing = db.scalar(
        select(IAMGroupRoleAssignment).where(
            IAMGroupRoleAssignment.group_id == group.id,
            IAMGroupRoleAssignment.role_id == role.id,
        )
    )
    if existing is not None:
        raise IAMGroupConflict("This group role assignment already exists.")
    assignment = IAMGroupRoleAssignment(
        group_id=group.id,
        role_id=role.id,
        assigned_by_user_id=actor_user_id,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        if _integrity_constraint_name(exc) == "uq_iam_group_role_assignments":
            raise IAMGroupConflict(
                "This group role assignment already exists."
            ) from exc
        raise IAMGroupConflict(
            "The referenced group or role changed while the assignment was created. Reload access policy and retry."
        ) from exc
    _mark_group_changed(db, group)
    return GroupRoleResult(assignment, True)


def remove_group_role(
    db: Session, *, group_id: uuid.UUID, assignment_id: uuid.UUID
) -> IAMGroupRoleAssignment:
    group = _lock_mutable_local_group(db, group_id)
    assignment = db.scalar(
        select(IAMGroupRoleAssignment)
        .where(
            IAMGroupRoleAssignment.id == assignment_id,
            IAMGroupRoleAssignment.group_id == group.id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise IAMGroupNotFound("Group role assignment not found.")
    db.delete(assignment)
    _mark_group_changed(db, group)
    return assignment


def _lock_mutable_local_group(db: Session, group_id: uuid.UUID) -> IAMGroup:
    group = db.scalar(select(IAMGroup).where(IAMGroup.id == group_id).with_for_update())
    if group is None:
        raise IAMGroupNotFound("Group not found.")
    if group.is_system:
        raise IAMSystemGroupImmutable(
            "System group membership is derived automatically."
        )
    if group.source != "local":
        raise IAMGroupConflict(
            "Identity-provider groups must be changed through the provider mapping."
        )
    return group


def _mark_group_changed(db: Session, group: IAMGroup) -> None:
    group.revision += 1
    db.add(group)
    bump_iam_policy_revision(db)
    db.flush()


def _group_response(db: Session, group: IAMGroup) -> GroupResponse:
    member_count = int(
        db.scalar(
            select(func.count(IAMGroupMembership.id)).where(
                IAMGroupMembership.group_id == group.id
            )
        )
        or 0
    )
    if group.is_system and group.key == "all-users":
        member_count = int(
            db.scalar(
                select(func.count(User.id)).where(
                    User.is_active.is_(True), User.is_approved.is_(True)
                )
            )
            or 0
        )
    role_ids = list(
        db.scalars(
            select(IAMGroupRoleAssignment.role_id).where(
                IAMGroupRoleAssignment.group_id == group.id
            )
        ).all()
    )
    return GroupResponse(
        id=group.id,
        key=group.key,
        name=group.name,
        description=group.description,
        source=group.source,
        external_key=group.external_key,
        is_system=group.is_system,
        revision=group.revision,
        member_count=member_count,
        role_ids=role_ids,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diagnostic, "constraint_name", None)
