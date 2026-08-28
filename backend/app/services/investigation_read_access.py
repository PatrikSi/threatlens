from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.investigation import Investigation, InvestigationMember
from app.models.user import User


@dataclass(frozen=True)
class InvestigationReadAccess:
    investigation: Investigation | None
    member_role: str | None = None
    authorization_changed: bool = False


def load_composed_investigation_read_access(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
) -> InvestigationReadAccess:
    """Load visible data, fencing private reads in canonical lock order.

    Private reads take shared user and investigation row locks, in that order.
    IAM and investigation writes take exclusive locks in the same order. Team
    reads remain lock-free.
    """

    authenticated_role = user.role
    authenticated_security_version = int(user.auth_token_version or 0)
    initial = _load_visible_investigation(
        db,
        investigation_id=investigation_id,
        user_id=user.id,
    )
    if initial.investigation is None or initial.investigation.visibility != "private":
        return initial

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
        or int(locked_user.auth_token_version or 0) != authenticated_security_version
    ):
        return InvestigationReadAccess(
            investigation=None,
            authorization_changed=True,
        )

    locked_investigation = db.scalar(
        select(Investigation)
        .where(Investigation.id == investigation_id)
        .with_for_update(read=True, of=Investigation)
        .execution_options(populate_existing=True)
    )
    if locked_investigation is None:
        return InvestigationReadAccess(investigation=None)

    # A separate statement is required so READ COMMITTED observes membership
    # changes that committed while the investigation lock was being acquired.
    return _load_visible_investigation(
        db,
        investigation_id=investigation_id,
        user_id=user.id,
        populate_existing=True,
    )


def _load_visible_investigation(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user_id: uuid.UUID,
    populate_existing: bool = False,
) -> InvestigationReadAccess:
    query = (
        select(Investigation, InvestigationMember.role.label("member_role"))
        .outerjoin(
            InvestigationMember,
            (InvestigationMember.investigation_id == Investigation.id)
            & (InvestigationMember.user_id == user_id),
        )
        .where(
            Investigation.id == investigation_id,
            or_(
                Investigation.visibility == "team",
                InvestigationMember.user_id.is_not(None),
            ),
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
