"""Team managers choose among destinations approved by AI administrators."""

import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.api.deps import get_admin_user, require_permissions, require_token_scopes
from app.api.routes.teams import _authorization
from app.api.routes.ai_providers import provider_operation
from app.db.session import get_db
from app.models.user import User
from app.schemas.team_ai_governance import (
    TeamAIGovernancePolicy,
    TeamAIGovernanceResponse,
    TeamAISelection,
)
from app.services.authorization import fence_authorization_context
from app.services.team_ai_governance import (
    governance_snapshot,
    read_governance,
    save_governance,
)

router = APIRouter(tags=["team AI governance"])


@router.get("/teams/{team_id}/ai-governance", response_model=TeamAIGovernanceResponse)
def get_team_ai_governance(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:teams")),
) -> TeamAIGovernanceResponse:
    with provider_operation(db, request):
        return read_governance(
            db, team_id=team_id, user=user, authorization=_authorization(request)
        )


@router.patch("/teams/{team_id}/ai-governance", response_model=TeamAIGovernanceResponse)
def select_team_ai_destination(
    team_id: uuid.UUID,
    payload: TeamAISelection,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("write:teams")),
) -> TeamAIGovernanceResponse:
    with provider_operation(db, request):
        result = save_governance(
            db,
            team_id=team_id,
            user=user,
            authorization=_authorization(request),
            payload=payload,
            approve=False,
        )
        db.commit()
        return result


@router.get("/ai/team-governance/{team_id}", response_model=TeamAIGovernanceResponse)
def get_admin_team_ai_governance(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes("read:ai")),
) -> TeamAIGovernanceResponse:
    with provider_operation(db, request):
        fence_authorization_context(db, _authorization(request))
        return governance_snapshot(db, team_id).model_copy(
            update={"can_approve": _authorization(request).has("write:ai")}
        )


@router.put("/ai/team-governance/{team_id}", response_model=TeamAIGovernanceResponse)
def approve_team_ai_governance(
    team_id: uuid.UUID,
    payload: TeamAIGovernancePolicy,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes("write:ai")),
) -> TeamAIGovernanceResponse:
    with provider_operation(db, request):
        result = save_governance(
            db,
            team_id=team_id,
            user=user,
            authorization=_authorization(request),
            payload=payload,
            approve=True,
        )
        db.commit()
        return result
