from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import SCOPE_WRITE_INVESTIGATIONS
from app.models.iam import (
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.investigation import (
    Investigation,
    InvestigationActivity,
    InvestigationMember,
)
from app.models.user import User
from app.services.investigation_owner_eligibility import (
    has_durable_investigation_write_access,
)

ELIGIBLE_INVESTIGATION_OWNER_ROLES = frozenset({ROLE_ADMIN, ROLE_ANALYST})


@dataclass(frozen=True)
class InvestigationOwnershipReference:
    investigation_id: uuid.UUID
    title: str


class InvestigationOwnerReassignmentRequired(ValueError):
    def __init__(self, investigations: list[InvestigationOwnershipReference]) -> None:
        self.investigations = tuple(investigations)
        super().__init__(_ownership_error_message(self.investigations))


@dataclass(frozen=True)
class InvestigationAccessChangeResult:
    cleared_assignment_ids: tuple[uuid.UUID, ...] = ()

    @property
    def cleared_assignment_count(self) -> int:
        return len(self.cleared_assignment_ids)


def is_eligible_investigation_owner(
    *,
    role: str,
    is_active: bool,
    is_approved: bool,
) -> bool:
    return role in ELIGIBLE_INVESTIGATION_OWNER_ROLES and is_active and is_approved


def reconcile_user_investigation_access_change(
    db: Session,
    *,
    user: User,
    next_role: str,
    next_is_active: bool,
    next_is_approved: bool,
    actor_user_id: uuid.UUID | None,
) -> InvestigationAccessChangeResult:
    """Protect ownership and assignment invariants before reducing account access."""
    if not has_durable_investigation_write_access(db, user):
        return InvestigationAccessChangeResult()
    if has_durable_investigation_write_access(
        db,
        user,
        role=next_role,
        is_active=next_is_active,
        is_approved=next_is_approved,
    ):
        return InvestigationAccessChangeResult()

    return _reconcile_lost_investigation_access(
        db,
        user=user,
        actor_user_id=actor_user_id,
        activity_details={
            "next_role": next_role,
            "next_is_active": next_is_active,
            "next_is_approved": next_is_approved,
        },
    )


def reconcile_user_investigation_permission_reduction(
    db: Session,
    *,
    user: User,
    actor_user_id: uuid.UUID | None,
) -> InvestigationAccessChangeResult:
    """Protect ownership when live IAM evaluation loses investigation write."""

    return _reconcile_lost_investigation_access(
        db,
        user=user,
        actor_user_id=actor_user_id,
        activity_details={"next_has_write_investigations": False},
    )


def _reconcile_lost_investigation_access(
    db: Session,
    *,
    user: User,
    actor_user_id: uuid.UUID | None,
    activity_details: dict[str, object],
) -> InvestigationAccessChangeResult:

    affected_ids = db.scalars(
        select(Investigation.id)
        .where(
            or_(
                Investigation.assignee_user_id == user.id,
                Investigation.id.in_(
                    select(InvestigationMember.investigation_id).where(
                        InvestigationMember.user_id == user.id,
                        InvestigationMember.role == "owner",
                    )
                ),
            )
        )
        .order_by(Investigation.id)
    ).all()
    if not affected_ids:
        return InvestigationAccessChangeResult()

    locked_investigations = db.scalars(
        select(Investigation)
        .where(Investigation.id.in_(affected_ids))
        .order_by(Investigation.id)
        .with_for_update()
    ).all()

    orphaned = _orphaned_investigations_after_user_change(db, user_id=user.id)
    if orphaned:
        raise InvestigationOwnerReassignmentRequired(orphaned)

    now = datetime.now(timezone.utc)
    cleared_assignment_ids: list[uuid.UUID] = []
    for investigation in locked_investigations:
        if investigation.assignee_user_id != user.id:
            continue
        investigation.assignee_user_id = None
        investigation.version += 1
        investigation.updated_at = now
        db.add(
            InvestigationActivity(
                investigation_id=investigation.id,
                actor_user_id=actor_user_id,
                action="investigation.assignee.cleared",
                entity_type="user",
                entity_id=user.id,
                details_json={
                    "reason": "account_access_reduced",
                    **activity_details,
                    "version": investigation.version,
                },
            )
        )
        cleared_assignment_ids.append(investigation.id)

    return InvestigationAccessChangeResult(
        cleared_assignment_ids=tuple(cleared_assignment_ids)
    )


def _orphaned_investigations_after_user_change(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> list[InvestigationOwnershipReference]:
    current_owner = aliased(InvestigationMember)
    other_owner = aliased(InvestigationMember)
    other_user = aliased(User)
    direct_write_grant = exists(
        select(1)
        .select_from(IAMUserRoleAssignment)
        .join(IAMRole, IAMRole.id == IAMUserRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMUserRoleAssignment.user_id == other_user.id,
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
            IAMGroupMembership.user_id == other_user.id,
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
            IAMGroupMembership.source == "local",
        )
    )
    eligible_other_owner = exists(
        select(1)
        .select_from(other_owner)
        .join(other_user, other_user.id == other_owner.user_id)
        .where(
            other_owner.investigation_id == Investigation.id,
            other_owner.user_id != user_id,
            other_owner.role == "owner",
            or_(
                other_user.role.in_(ELIGIBLE_INVESTIGATION_OWNER_ROLES),
                direct_write_grant,
                group_write_grant,
            ),
            other_user.is_active.is_(True),
            other_user.is_approved.is_(True),
        )
    )
    rows = db.execute(
        select(Investigation.id, Investigation.title)
        .join(
            current_owner,
            current_owner.investigation_id == Investigation.id,
        )
        .where(
            current_owner.user_id == user_id,
            current_owner.role == "owner",
            ~eligible_other_owner,
        )
        .order_by(Investigation.updated_at.desc(), Investigation.id)
    ).all()
    return [
        InvestigationOwnershipReference(investigation_id=row.id, title=row.title)
        for row in rows
    ]


def _ownership_error_message(
    investigations: tuple[InvestigationOwnershipReference, ...],
) -> str:
    count = len(investigations)
    visible = ", ".join(
        f'"{reference.title}" ({reference.investigation_id})'
        for reference in investigations[:3]
    )
    remainder = count - min(count, 3)
    suffix = f" and {remainder} more" if remainder else ""
    noun = "investigation" if count == 1 else "investigations"
    return (
        f"This account is the only eligible owner of {count} {noun}: {visible}{suffix}. "
        "Add another active, approved analyst or administrator as an owner before reducing this account's access."
    )


__all__ = [
    "InvestigationAccessChangeResult",
    "InvestigationOwnerReassignmentRequired",
    "is_eligible_investigation_owner",
    "reconcile_user_investigation_access_change",
    "reconcile_user_investigation_permission_reduction",
]
