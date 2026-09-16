"""Team-scoped AI preferences with fenced, optimistic updates.

The lock order is IAM, actor, team, then context. Taking the team update lock
also serializes the first write when no context row exists yet.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_TEAMS, SCOPE_WRITE_TEAMS
from app.models.team_ai_context import TeamAIContext
from app.models.user import User
from app.schemas.team_ai_context import TeamAIContextResponse, TeamAIContextUpdate
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.team_access import (
    assert_current_team_access,
    require_team_access,
    team_access_predicate,
)


def team_ai_context_snapshot(
    db: Session, *, team_id: uuid.UUID
) -> TeamAIContextResponse:
    """Load a bounded snapshot after the caller has fenced current team access.

    This helper does not grant access or commit. Version zero describes an
    unconfigured team without creating a database row as a side effect of reads.
    """
    context = db.scalar(
        select(TeamAIContext)
        .where(TeamAIContext.team_id == team_id)
        .execution_options(populate_existing=True)
    )
    if context is None:
        return TeamAIContextResponse(
            team_id=team_id,
            technology_stack=[],
            priorities=[],
            available_telemetry=[],
            relevance_criteria="",
            version=0,
        )
    return TeamAIContextResponse.model_validate(context)


def _require_permission(authorization: AuthorizationContext, permission: str) -> None:
    if not authorization.has(permission):
        raise ApiHTTPException(
            status_code=403,
            error_code="team_ai_context_permission_required",
            detail="Your credentials do not permit this team AI context action.",
        )


def get_team_ai_context(
    db: Session,
    *,
    team_id: uuid.UUID,
    user: User,
    authorization: AuthorizationContext,
) -> TeamAIContextResponse:
    _require_permission(authorization, SCOPE_READ_TEAMS)
    require_team_access(db, team_id=team_id, user=user, authorization=authorization)
    response = team_ai_context_snapshot(db, team_id=team_id)
    can_manage = authorization.has(SCOPE_WRITE_TEAMS) and bool(
        db.scalar(select(team_access_predicate(team_id, user.id, manage=True)))
    )
    assert_current_team_access(db, team_id=team_id, user_id=user.id)
    return response.model_copy(update={"can_manage": can_manage})


def update_team_ai_context(
    db: Session,
    *,
    team_id: uuid.UUID,
    user: User,
    authorization: AuthorizationContext,
    payload: TeamAIContextUpdate,
) -> TeamAIContextResponse:
    _require_permission(authorization, SCOPE_WRITE_TEAMS)
    require_team_access(
        db,
        team_id=team_id,
        user=user,
        authorization=authorization,
        manage=True,
        for_update=True,
    )
    context = db.scalar(
        select(TeamAIContext)
        .where(TeamAIContext.team_id == team_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert_current_team_access(db, team_id=team_id, user_id=user.id, manage=True)
    current_version = context.version if context is not None else 0
    if payload.expected_version != current_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_ai_context_version_conflict",
            detail="This team's AI context changed while you were editing. Reload it before saving.",
            error_context={"current_version": current_version},
        )
    values = payload.model_dump(exclude={"expected_version"})
    if context is None:
        context = TeamAIContext(team_id=team_id, **values)
        db.add(context)
    else:
        for field, value in values.items():
            setattr(context, field, value)
    context.version = current_version + 1
    context.updated_by_user_id = user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="teams.ai_context.update",
        resource_type="team",
        resource_id=str(team_id),
        metadata={"previous_version": current_version, "version": context.version},
    )
    return TeamAIContextResponse.model_validate(context).model_copy(
        update={"can_manage": True}
    )
