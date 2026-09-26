"""Team hunt review queue; mutations retain existing source and credential fences."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context, require_permissions
from app.api.routes.team_assessments import _actor
from app.db.budgets import database_operation
from app.db.session import get_db
from app.models.user import User
from app.schemas.team_hunt_worklist import (
    HuntClaimCommand,
    HuntClaimResponse,
    HuntWorklistPage,
    HuntWorklistStatus,
)
from app.services.data_access_policy import DataAccessContext
from app.services.team_hunt_claims import change_hunt_claim
from app.services.team_hunt_worklist import list_team_hunts

router = APIRouter(prefix="/teams/{team_id}/hunts", tags=["team hunts"])


@router.get("", response_model=HuntWorklistPage)
def list_team_hunt_worklist(
    team_id: uuid.UUID,
    request: Request,
    status: HuntWorklistStatus | None = None,
    ownership: Literal["all", "mine", "unclaimed"] = "all",
    cursor: str | None = Query(default=None, max_length=4000),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> HuntWorklistPage:
    with database_operation(db, operation="interactive"):
        return list_team_hunts(
            db,
            actor=_actor(request, user, access),
            team_id=team_id,
            status=status,
            ownership=ownership,
            cursor=cursor,
            limit=limit,
        )


@router.post("/{assessment_id}/{hunt_id}/claim", response_model=HuntClaimResponse)
def update_team_hunt_claim(
    team_id: uuid.UUID,
    assessment_id: uuid.UUID,
    payload: HuntClaimCommand,
    request: Request,
    hunt_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> HuntClaimResponse:
    with database_operation(db, operation="interactive"):
        result = change_hunt_claim(
            db,
            actor=_actor(request, user, access),
            team_id=team_id,
            assessment_id=assessment_id,
            hunt_id=hunt_id,
            payload=payload,
        )
        db.commit()
        return result
