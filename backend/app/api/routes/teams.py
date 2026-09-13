"""Named team metadata; content access remains feature- and label-scoped."""

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_context, require_permissions
from app.api.routes.iam import require_iam_mutation_actor
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_IAM, SCOPE_READ_TEAMS, SCOPE_WRITE_TEAMS
from app.db.session import get_db
from app.models.iam import IAMGroup
from app.models.team import Team
from app.models.user import User
from app.schemas.team import (
    TeamBindingsUpdate,
    TeamCreate,
    TeamListResponse,
    TeamMemberListResponse,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdate,
)
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext, fence_authorization_context
from app.services.team_access import (
    require_team_access,
    team_access_predicate,
    team_member_user_ids_query,
)

router = APIRouter(prefix="/teams", tags=["teams"])


def _authorization(request: Request) -> AuthorizationContext:
    context = get_authorization_context(request)
    if context is None:
        raise ApiHTTPException(
            status_code=503,
            error_code="team_access_unavailable",
            detail="Team access could not be evaluated. Retry the request.",
        )
    return context


def _list(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext,
    page: int,
    page_size: int,
    admin: bool,
) -> TeamListResponse:
    fence_authorization_context(db, authorization)
    filters = [] if admin else [team_access_predicate(Team.id, user.id)]
    total = int(db.scalar(select(func.count()).select_from(Team).where(*filters)) or 0)
    rows = db.execute(
        select(
            Team, team_access_predicate(Team.id, user.id, manage=True).label("manager")
        )
        .where(*filters)
        .order_by(Team.name, Team.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return TeamListResponse(
        items=[
            TeamResponse.model_validate(team).model_copy(
                update={
                    "can_manage": bool(manager and authorization.has(SCOPE_WRITE_TEAMS))
                }
            )
            for team, manager in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("", response_model=TeamListResponse)
def list_teams(
    request: Request,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_TEAMS)),
):
    return _list(
        db,
        user=user,
        authorization=_authorization(request),
        page=page,
        page_size=page_size,
        admin=False,
    )


@router.get("/admin", response_model=TeamListResponse)
def list_admin_teams(
    request: Request,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    return _list(
        db,
        user=user,
        authorization=_authorization(request),
        page=page,
        page_size=page_size,
        admin=True,
    )


@router.get("/admin/{team_id}", response_model=TeamResponse)
def get_admin_team(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_IAM)),
):
    authorization = _authorization(request)
    fence_authorization_context(db, authorization)
    team = db.get(Team, team_id)
    if team is None:
        raise ApiHTTPException(
            status_code=404, error_code="team_not_found", detail="Team not found."
        )
    manager = bool(
        db.scalar(select(team_access_predicate(team.id, user.id, manage=True)))
    )
    return TeamResponse.model_validate(team).model_copy(
        update={"can_manage": manager and authorization.has(SCOPE_WRITE_TEAMS)}
    )


def _validate_groups(
    db: Session, membership_group_id: uuid.UUID, manager_group_id: uuid.UUID | None
) -> None:
    ids = {membership_group_id, *([manager_group_id] if manager_group_id else [])}
    found = set(
        db.scalars(
            select(IAMGroup.id).where(
                IAMGroup.id.in_(ids), IAMGroup.is_system.is_(False)
            )
        )
    )
    if found != ids:
        raise ApiHTTPException(
            status_code=422,
            error_code="team_group_not_found",
            detail="Choose existing non-system IAM groups for team access. Refresh the group list.",
        )


def _require_revision(team: Team, expected: int) -> None:
    if team.revision != expected:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_revision_conflict",
            detail="This team changed while you were editing. Reload it before saving.",
            error_context={"current_revision": team.revision},
        )


def _commit(db: Session, team: Team, *, user: User, action: str) -> TeamResponse:
    record_audit(
        db,
        actor_user_id=user.id,
        action=action,
        resource_type="team",
        resource_id=str(team.id),
        metadata={"revision": team.revision},
    )
    db.flush()
    response = TeamResponse.model_validate(team)
    db.commit()
    return response


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_iam_mutation_actor),
):
    _validate_groups(db, payload.membership_group_id, payload.manager_group_id)
    team = Team(**payload.model_dump(), created_by_user_id=user.id)
    db.add(team)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiHTTPException(
            status_code=409,
            error_code="team_key_conflict",
            detail="A team with this key already exists. Choose another key.",
        ) from exc
    return _commit(db, team, user=user, action="teams.create")


@router.get("/{team_id}", response_model=TeamResponse)
def get_team(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_TEAMS)),
):
    authorization = _authorization(request)
    team = require_team_access(
        db, team_id=team_id, user=user, authorization=authorization
    )
    manager = bool(
        db.scalar(select(team_access_predicate(team.id, user.id, manage=True)))
    )
    return TeamResponse.model_validate(team).model_copy(
        update={"can_manage": manager and authorization.has(SCOPE_WRITE_TEAMS)}
    )


@router.patch("/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: uuid.UUID,
    payload: TeamUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_WRITE_TEAMS)),
):
    team = require_team_access(
        db,
        team_id=team_id,
        user=user,
        authorization=_authorization(request),
        manage=True,
        for_update=True,
    )
    _require_revision(team, payload.expected_revision)
    team.name, team.description = payload.name, payload.description
    team.revision += 1
    return _commit(db, team, user=user, action="teams.update").model_copy(
        update={"can_manage": True}
    )


@router.put("/{team_id}/bindings", response_model=TeamResponse)
def update_team_bindings(
    team_id: uuid.UUID,
    payload: TeamBindingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_iam_mutation_actor),
):
    team = db.scalar(
        select(Team)
        .where(Team.id == team_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if team is None:
        raise ApiHTTPException(
            status_code=404, error_code="team_not_found", detail="Team not found."
        )
    _require_revision(team, payload.expected_revision)
    _validate_groups(db, payload.membership_group_id, payload.manager_group_id)
    team.membership_group_id, team.manager_group_id, team.active = (
        payload.membership_group_id,
        payload.manager_group_id,
        payload.active,
    )
    team.revision += 1
    return _commit(db, team, user=user, action="teams.bindings.update")


@router.get("/{team_id}/members", response_model=TeamMemberListResponse)
def list_team_members(
    team_id: uuid.UUID,
    request: Request,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
    q: str | None = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_TEAMS)),
):
    require_team_access(
        db, team_id=team_id, user=user, authorization=_authorization(request)
    )
    filter_ = User.id.in_(team_member_user_ids_query(team_id))
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filter_ = filter_ & User.email.ilike(f"%{term}%", escape="\\")
    total = int(db.scalar(select(func.count(User.id)).where(filter_)) or 0)
    rows = db.execute(
        select(
            User.id,
            User.email,
            User.role,
            team_access_predicate(team_id, User.id, manage=True),
        )
        .where(filter_)
        .order_by(User.email, User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return TeamMemberListResponse(
        items=[
            TeamMemberResponse(
                id=id_, email=email, account_role=role, is_manager=manager
            )
            for id_, email, role, manager in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
