"""Current group access predicates and canonical locks for team-owned resources.

Team membership is an object access boundary, never an IAM permission grant.
Callers must require their feature permission separately. Mutations fence IAM
and the actor before locking a team, then lock their resource after the team.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.api_errors import ApiHTTPException
from app.models.iam import IAMGroupMembership
from app.models.team import Team
from app.models.user import User
from app.services.auth_sessions import lock_user_auth_state
from app.services.authorization import AuthorizationContext, fence_authorization_context


def team_access_predicate(
    team_id_column: ColumnElement[uuid.UUID] | uuid.UUID,
    user_id: ColumnElement[uuid.UUID] | uuid.UUID,
    *,
    manage: bool = False,
) -> ColumnElement[bool]:
    """Evaluate current eligible membership before resource filtering/limits."""
    team = aliased(Team)
    membership = aliased(IAMGroupMembership)
    user = aliased(User)
    groups = (
        membership.group_id == team.manager_group_id
        if manage
        else or_(
            membership.group_id == team.membership_group_id,
            membership.group_id == team.manager_group_id,
        )
    )
    return (
        select(1)
        .select_from(team)
        .join(membership, groups)
        .join(user, user.id == membership.user_id)
        .where(
            team.id == team_id_column,
            team.active.is_(True),
            user.id == user_id,
            user.is_active.is_(True),
            user.is_approved.is_(True),
            or_(
                membership.source == "local",
                and_(
                    membership.source == "oidc",
                    membership.oidc_assertion_expires_at > func.clock_timestamp(),
                ),
            ),
        )
        .exists()
    )


def team_member_user_ids_query(
    team_id: uuid.UUID,
    *,
    manage: bool = False,
) -> Select[tuple[uuid.UUID]]:
    """Unique active group members for bounded candidate/assignment queries."""
    return select(User.id).where(team_access_predicate(team_id, User.id, manage=manage))


def require_team_access(
    db: Session,
    *,
    team_id: uuid.UUID,
    user: User,
    authorization: AuthorizationContext,
    manage: bool = False,
    for_update: bool = False,
) -> Team:
    """Keep IAM/actor/team stable until the caller's resource transaction ends."""
    if (
        authorization.principal_type != "user"
        or authorization.principal_id != user.id
        or not authorization.account_eligible
    ):
        raise ApiHTTPException(
            status_code=403,
            error_code="team_actor_unavailable",
            detail="Your account cannot access team workspaces. Sign in again and retry.",
        )
    fence_authorization_context(db, authorization)
    expected_security_version = user.auth_token_version
    actor = lock_user_auth_state(db, user.id)
    if (
        actor is None
        or not actor.is_active
        or not actor.is_approved
        or actor.auth_token_version != expected_security_version
    ):
        raise ApiHTTPException(
            status_code=403,
            error_code="team_actor_unavailable",
            detail="Your account access changed. Sign in again and retry.",
        )
    row = db.scalar(
        select(Team)
        .where(
            Team.id == team_id,
            team_access_predicate(Team.id, user.id, manage=manage),
        )
        .with_for_update(read=not for_update, of=Team)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="team_not_found",
            detail="Team not found or current group membership does not permit this action.",
        )
    return row
