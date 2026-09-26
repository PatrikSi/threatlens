"""Administrator-controlled shared upstream account budgets."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.api.routes.ai_providers import provider_operation
from app.api.routes.ai_route_helpers import require_ai_enabled
from app.core.token_scopes import SCOPE_READ_AI, SCOPE_WRITE_AI
from app.db.session import get_db
from app.models.ai_quota_group import AIQuotaGroup
from app.models.user import User
from app.schemas.ai_quota_groups import (
    AIQuotaGroupCreate,
    AIQuotaGroupPage,
    AIQuotaGroupResponse,
    AIQuotaGroupUpdate,
)
from app.services.ai_quota_groups import (
    quota_response,
    quota_responses,
    save_quota_group,
)
from app.services.audit import record_audit

router = APIRouter(
    prefix="/ai/quota-groups", tags=["ai"], dependencies=[Depends(require_ai_enabled)]
)


@router.get("", response_model=AIQuotaGroupPage)
def list_ai_quota_groups(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_READ_AI)),
) -> AIQuotaGroupPage:
    with provider_operation(db):
        rows = db.scalars(
            select(AIQuotaGroup)
            .order_by(AIQuotaGroup.normalized_name, AIQuotaGroup.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return AIQuotaGroupPage(
            items=quota_responses(db, rows),
            total=db.scalar(select(func.count()).select_from(AIQuotaGroup)) or 0,
            limit=limit,
            offset=offset,
        )


def _save(
    db: Session,
    request: Request,
    actor: User,
    payload: AIQuotaGroupCreate | AIQuotaGroupUpdate,
    group_id: uuid.UUID,
) -> AIQuotaGroupResponse:
    with provider_operation(db, request):
        group = save_quota_group(db, payload, group_id=group_id)
        result = quota_response(db, group)
        record_audit(
            db,
            actor_user_id=actor.id,
            action="ai.quota_group.save",
            resource_type="ai_quota_group",
            resource_id=str(group.id),
            metadata=result.model_dump(mode="json"),
        )
        db.commit()
        return result


@router.post("", response_model=AIQuotaGroupResponse, status_code=201)
def create_ai_quota_group(
    payload: AIQuotaGroupCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
) -> AIQuotaGroupResponse:
    return _save(db, request, actor, payload, payload.id)


@router.put("/{group_id}", response_model=AIQuotaGroupResponse)
def update_ai_quota_group(
    group_id: uuid.UUID,
    payload: AIQuotaGroupUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
) -> AIQuotaGroupResponse:
    return _save(db, request, actor, payload, group_id)
