"""Current account eligibility and membership invariants for investigations.

This module changes neither IAM grants nor group bindings. Ordinary writers
share the IAM policy fence, then lock account state before resource rows.
"""

from __future__ import annotations

import uuid
from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_WRITE_INVESTIGATIONS
from app.models.investigation import Investigation, InvestigationMember
from app.models.user import User
from app.services.auth_sessions import lock_user_auth_state
from app.services.authorization import (
    authorization_context_for_user,
    fence_authorization_context,
)
from app.services.investigation_contracts import (
    OWNER_MEMBER_ROLE,
    WRITE_MEMBER_ROLES,
    InvestigationActorNotEligibleError,
    InvestigationConflictError,
    InvestigationPermissionError,
    InvestigationValidationError,
)
from app.services.investigation_owner_eligibility import (
    eligible_investigation_owner_ids_query,
    has_durable_investigation_write_access,
)


def require_owner(member: InvestigationMember) -> None:
    if member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError(
            "Only an investigation owner can manage members."
        )


def validate_member_role_for_account(db: Session, user: User, member_role: str) -> None:
    if member_role == OWNER_MEMBER_ROLE and not has_durable_investigation_write_access(
        db, user
    ):
        raise InvestigationValidationError(
            "Investigation ownership requires an analyst or administrator account with "
            "durable built-in access, or a locally managed investigation-write role. "
            "Expiring identity-provider access can be used for editor membership but "
            "cannot be the basis for ownership."
        )
    if member_role in WRITE_MEMBER_ROLES and (
        not user.is_active
        or not user.is_approved
        or not authorization_context_for_user(db, user).has(SCOPE_WRITE_INVESTIGATIONS)
    ):
        raise InvestigationValidationError(
            "Owner and editor membership requires an analyst or administrator account, "
            "or an active, approved account with an explicit investigation-write role."
        )


def lock_membership_account(db: Session, user_id: uuid.UUID) -> User | None:
    """Serialize membership eligibility with IAM access reductions."""
    return lock_user_auth_state(db, user_id)


def lock_eligible_actor(db: Session, user_id: uuid.UUID) -> User:
    # These operations change investigation content/membership, not IAM grants.
    # A shared policy fence blocks access reductions without read-to-write upgrades.
    actor_snapshot = db.get(User, user_id)
    if actor_snapshot is None:
        raise InvestigationActorNotEligibleError("Your account no longer exists.")
    fence_authorization_context(db, authorization_context_for_user(db, actor_snapshot))
    actor = lock_membership_account(db, user_id)
    if (
        actor is None
        or not actor.is_active
        or not actor.is_approved
        or not authorization_context_for_user(db, actor).has(SCOPE_WRITE_INVESTIGATIONS)
    ):
        raise InvestigationActorNotEligibleError(
            "Your account is no longer active, approved, authorized as an analyst or "
            "administrator, or granted explicit investigation write access. Sign in "
            "again before retrying."
        )
    return actor


def require_individual_membership_management(investigation: Investigation) -> None:
    if investigation.team_id is not None:
        raise InvestigationValidationError(
            "Named-team membership follows its IAM groups. Manage group membership through Identity settings."
        )


def require_another_owner(
    db: Session, investigation_id: uuid.UUID, *, excluding_user_id: uuid.UUID
) -> None:
    other_owner = db.scalar(
        eligible_investigation_owner_ids_query(
            investigation_id,
            excluding_user_id=excluding_user_id,
        ).limit(1)
    )
    if other_owner is None:
        raise InvestigationConflictError(
            "An investigation must retain at least one owner who is active, approved, "
            "and has investigation write access. "
            "Promote an eligible member before changing this owner.",
            code="investigation_owner_required",
        )
