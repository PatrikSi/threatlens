from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.models.investigation import Investigation, InvestigationMember
from app.models.user import User
from app.models.team import Team
from app.services.authorization import (
    authorization_context_for_user,
    fence_authorization_context,
)
from app.services.team_access import team_access_predicate
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import DataAccessContext, fence_data_access_context


@dataclass(frozen=True)
class InvestigationReadAccess:
    investigation: Investigation | None
    member_role: str | None = None
    authorization_changed: bool = False


@dataclass(frozen=True)
class InvestigationWriteAccess:
    investigation: Investigation | None
    member: InvestigationMember | None = None


def load_composed_investigation_read_access(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    data_access: DataAccessContext,
) -> InvestigationReadAccess:
    """Load visible data, fencing private reads in canonical lock order.

    Private reads take shared user and investigation row locks, in that order.
    Team reads take a shared investigation lock so evidence and its monotonic
    policy envelope cannot change between the authorization and child queries.
    """

    authenticated_role = user.role
    fence_authorization_context(db, authorization_context_for_user(db, user))
    fence_data_access_context(db, data_access)
    authenticated_security_version = int(user.auth_token_version or 0)
    initial = _load_visible_investigation(
        db,
        investigation_id=investigation_id,
        user_id=user.id,
        data_access=data_access,
    )
    if initial.investigation is None:
        return initial

    if initial.investigation.visibility == "private":
        locked_user = db.scalar(
            select(User)
            .where(User.id == user.id)
            .with_for_update(read=True, of=User)
            .execution_options(populate_existing=True)
        )
        if (
            locked_user is None
            or not locked_user.is_active
            or not locked_user.is_approved
            or locked_user.role != authenticated_role
            or int(locked_user.auth_token_version or 0)
            != authenticated_security_version
        ):
            return InvestigationReadAccess(
                investigation=None,
                authorization_changed=True,
            )

    if initial.investigation.team_id is not None and not lock_investigation_team(
        db, initial.investigation.team_id, user.id
    ):
        return InvestigationReadAccess(investigation=None)
    locked_investigation = db.scalar(
        select(Investigation)
        .where(
            Investigation.id == investigation_id,
            _investigation_visibility_predicate(user.id),
            _investigation_data_access_predicate(data_access),
        )
        .with_for_update(read=True, of=Investigation)
        .execution_options(populate_existing=True)
    )
    if locked_investigation is None:
        return InvestigationReadAccess(investigation=None)

    # The shared investigation lock keeps visibility, membership-backed access,
    # and the evidence-derived envelope stable while child collections load.
    return _load_visible_investigation(
        db,
        investigation_id=investigation_id,
        user_id=user.id,
        data_access=data_access,
        populate_existing=True,
    )


def lock_composed_investigation_write_access(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user_id: uuid.UUID,
    data_access: DataAccessContext,
) -> InvestigationWriteAccess:
    fence_data_access_context(db, data_access)
    team_id = db.scalar(
        select(Investigation.team_id).where(Investigation.id == investigation_id)
    )
    if team_id is not None and not lock_investigation_team(db, team_id, user_id):
        return InvestigationWriteAccess(investigation=None)
    investigation = db.scalar(
        select(Investigation)
        .where(
            Investigation.id == investigation_id,
            _investigation_visibility_predicate(user_id),
            _investigation_data_access_predicate(data_access),
        )
        .with_for_update(of=Investigation)
        .execution_options(populate_existing=True)
    )
    if investigation is None:
        return InvestigationWriteAccess(investigation=None)
    if investigation.team_id is not None:
        # OIDC membership can expire while the investigation row lock waits.
        # A fresh statement must still permit membership before deriving a role.
        if not db.scalar(select(team_access_predicate(investigation.team_id, user_id))):
            return InvestigationWriteAccess(investigation=None)
        manager = bool(
            db.scalar(
                select(
                    team_access_predicate(investigation.team_id, user_id, manage=True)
                )
            )
        )
        return InvestigationWriteAccess(
            investigation=investigation,
            member=InvestigationMember(
                investigation_id=investigation.id,
                user_id=user_id,
                role="owner" if manager else "editor",
            ),
        )
    member = db.scalar(
        select(InvestigationMember)
        .where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    return InvestigationWriteAccess(investigation=investigation, member=member)


def _load_visible_investigation(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user_id: uuid.UUID,
    data_access: DataAccessContext,
    populate_existing: bool = False,
) -> InvestigationReadAccess:
    query = (
        select(
            Investigation,
            investigation_member_role(InvestigationMember.role, user_id).label(
                "member_role"
            ),
        )
        .outerjoin(
            InvestigationMember,
            (InvestigationMember.investigation_id == Investigation.id)
            & (InvestigationMember.user_id == user_id),
        )
        .where(
            Investigation.id == investigation_id,
            _investigation_visibility_predicate(user_id),
            _investigation_data_access_predicate(data_access),
        )
    )
    if populate_existing:
        query = query.execution_options(populate_existing=True)
    row = db.execute(query).first()
    if row is None:
        return InvestigationReadAccess(investigation=None)
    return InvestigationReadAccess(
        investigation=row.Investigation,
        member_role=row.member_role,
    )


def _investigation_visibility_predicate(user_id: uuid.UUID):
    return or_(
        team_access_predicate(Investigation.team_id, user_id),
        and_(
            Investigation.team_id.is_(None),
            or_(
                Investigation.visibility == "team",
                select(InvestigationMember.user_id)
                .where(
                    InvestigationMember.investigation_id == Investigation.id,
                    InvestigationMember.user_id == user_id,
                )
                .correlate(Investigation)
                .exists(),
            ),
        ),
    )


def investigation_member_role(legacy_role, user_id: uuid.UUID):
    return case(
        (
            Investigation.team_id.is_not(None),
            case(
                (
                    team_access_predicate(Investigation.team_id, user_id, manage=True),
                    "owner",
                ),
                else_="editor",
            ),
        ),
        else_=legacy_role,
    )


def lock_investigation_team(
    db: Session, team_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Caller holds IAM/actor fences; team precedes the investigation row."""
    return (
        db.scalar(
            select(Team.id)
            .where(Team.id == team_id, team_access_predicate(Team.id, user_id))
            .with_for_update(read=True, of=Team)
        )
        is not None
    )


def _investigation_data_access_predicate(data_access: DataAccessContext):
    return data_access_envelope_predicate(
        DATA_ACCESS_RESOURCE_INVESTIGATION,
        Investigation.id,
        data_access,
    )
