"""Current evidence access governs deadlines; shared views contain filters only."""

import uuid
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.orm import Session
from app.api.deps import get_data_access_context, require_permissions
from app.api.routes.team_assessments import _actor
from app.api.routes.teams import _authorization
from app.db.budgets import database_operation
from app.db.session import get_db
from app.models.user import User
from app.schemas.team_hunt_worklist import (
    HuntReminderAcknowledgement,
    HuntReviewSchedule,
    HuntReviewScheduleCommand,
    HuntViewPage,
    HuntViewResponse,
    HuntViewWrite,
)
from app.services.data_access_policy import DataAccessContext
from app.services.team_hunt_review_workflow import save_review_schedule
from app.services.team_hunt_views import list_hunt_views, write_hunt_view

router = APIRouter(prefix="/teams/{team_id}/hunts", tags=["team hunts"])


@router.get("/views", response_model=HuntViewPage)
def get_team_hunt_views(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:teams")),
) -> HuntViewPage:
    with database_operation(db, operation="interactive"):
        return list_hunt_views(
            db, team_id=team_id, user=user, authorization=_authorization(request)
        )


@router.put("/views/{view_id}", response_model=HuntViewResponse)
def save_team_hunt_view(
    team_id: uuid.UUID,
    view_id: uuid.UUID,
    payload: HuntViewWrite,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("write:teams")),
) -> HuntViewResponse:
    with database_operation(db, operation="interactive"):
        result = write_hunt_view(
            db,
            team_id=team_id,
            view_id=view_id,
            user=user,
            authorization=_authorization(request),
            payload=payload,
            expected_version=payload.expected_version,
        )
        db.commit()
        assert result is not None
        return result


@router.delete("/views/{view_id}", status_code=204)
def delete_team_hunt_view(
    team_id: uuid.UUID,
    view_id: uuid.UUID,
    request: Request,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("write:teams")),
) -> Response:
    with database_operation(db, operation="interactive"):
        write_hunt_view(
            db,
            team_id=team_id,
            view_id=view_id,
            user=user,
            authorization=_authorization(request),
            payload=None,
            expected_version=expected_version,
        )
        db.commit()
        return Response(status_code=204)


@router.patch("/{assessment_id}/{hunt_id}/schedule", response_model=HuntReviewSchedule)
def update_hunt_review_schedule(
    team_id: uuid.UUID,
    assessment_id: uuid.UUID,
    payload: HuntReviewScheduleCommand,
    request: Request,
    hunt_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> HuntReviewSchedule:
    with database_operation(db, operation="interactive"):
        result = save_review_schedule(
            db,
            actor=_actor(request, user, access),
            team_id=team_id,
            assessment_id=assessment_id,
            hunt_id=hunt_id,
            payload=payload,
        )
        db.commit()
        return result


@router.post(
    "/{assessment_id}/{hunt_id}/reminder-acknowledgement",
    response_model=HuntReviewSchedule,
)
def acknowledge_hunt_review_reminder(
    team_id: uuid.UUID,
    assessment_id: uuid.UUID,
    payload: HuntReminderAcknowledgement,
    request: Request,
    hunt_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> HuntReviewSchedule:
    with database_operation(db, operation="interactive"):
        result = save_review_schedule(
            db,
            actor=_actor(request, user, access),
            team_id=team_id,
            assessment_id=assessment_id,
            hunt_id=hunt_id,
            payload=payload,
        )
        db.commit()
        return result
