"""Small shared queue views with optimistic writes and explicit team ownership."""

import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.api_errors import ApiHTTPException
from app.models.team_hunt_view import TeamHuntView
from app.models.user import User
from app.schemas.team_hunt_worklist import HuntViewPage, HuntViewResponse, HuntViewWrite
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.team_access import (
    assert_current_team_access,
    require_team_access,
    team_access_predicate,
)


def response(row: TeamHuntView) -> HuntViewResponse:
    return HuntViewResponse(
        id=row.id, name=row.name, version=row.version, filters=row.filters_json
    )


def list_hunt_views(
    db: Session, *, team_id: uuid.UUID, user: User, authorization: AuthorizationContext
) -> HuntViewPage:
    require_team_access(db, team_id=team_id, user=user, authorization=authorization)
    rows = db.scalars(
        select(TeamHuntView)
        .where(TeamHuntView.team_id == team_id)
        .order_by(TeamHuntView.name, TeamHuntView.id)
        .limit(30)
    ).all()
    manager = authorization.has("write:teams") and bool(
        db.scalar(select(team_access_predicate(team_id, user.id, manage=True)))
    )
    assert_current_team_access(db, team_id=team_id, user_id=user.id)
    return HuntViewPage(items=[response(row) for row in rows], can_manage=manager)


def write_hunt_view(
    db: Session,
    *,
    team_id: uuid.UUID,
    view_id: uuid.UUID,
    user: User,
    authorization: AuthorizationContext,
    payload: HuntViewWrite | None,
    expected_version: int,
) -> HuntViewResponse | None:
    require_team_access(
        db,
        team_id=team_id,
        user=user,
        authorization=authorization,
        manage=True,
        for_update=True,
    )
    row = db.get(TeamHuntView, view_id, with_for_update=True, populate_existing=True)
    if row is not None and row.team_id != team_id:
        raise ApiHTTPException(
            status_code=404,
            error_code="hunt_view_not_found",
            detail="Saved hunt view not found.",
        )
    if (
        payload
        and expected_version == 0
        and row is not None
        and row.version == 1
        and row.name == payload.name
        and row.filters_json == payload.filters.model_dump()
    ):
        return response(row)
    if (row.version if row else 0) != expected_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_view_conflict",
            detail="This saved hunt view changed. Reload it before saving.",
        )
    if payload is None:
        if row is None:
            raise ApiHTTPException(
                status_code=404,
                error_code="hunt_view_not_found",
                detail="Saved hunt view not found.",
            )
        db.delete(row)
        result = None
    else:
        if db.scalar(
            select(TeamHuntView.id).where(
                TeamHuntView.team_id == team_id,
                TeamHuntView.name == payload.name,
                TeamHuntView.id != view_id,
            )
        ):
            raise ApiHTTPException(
                status_code=409,
                error_code="hunt_view_name_conflict",
                detail="A team hunt view already uses this name.",
            )
        if row is None:
            if (
                db.scalar(
                    select(func.count())
                    .select_from(TeamHuntView)
                    .where(TeamHuntView.team_id == team_id)
                )
                or 0
            ) >= 30:
                raise ApiHTTPException(
                    status_code=429,
                    error_code="hunt_view_capacity",
                    detail="This team has 30 saved hunt views. Update or remove one before adding another.",
                )
            row = TeamHuntView(id=view_id, team_id=team_id, version=0)
            db.add(row)
        row.name, row.filters_json, row.version = (
            payload.name,
            payload.filters.model_dump(),
            row.version + 1,
        )
        db.flush()
        result = response(row)
    assert_current_team_access(db, team_id=team_id, user_id=user.id, manage=True)
    record_audit(
        db,
        actor_user_id=user.id,
        action="teams.hunt_view.save" if payload else "teams.hunt_view.delete",
        resource_type="team",
        resource_id=str(team_id),
        metadata={"view_id": str(view_id), "version": row.version},
    )
    return result
