"""Source-bound indicator observations and team review endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.core.api_errors import ApiHTTPException
from app.db.session import get_db
from app.models.user import User
from app.schemas.intel_assessments import (
    AssessmentCommand,
    AssessmentResponse,
    IndicatorHistoryPage,
    IndicatorPage,
    SuppressionCreate,
    SuppressionPage,
    SuppressionResponse,
    SuppressionUpdate,
)
from app.services.data_access_policy import DataAccessContext
from app.services.indicator_assessments import list_indicators, update_assessment
from app.services.indicator_suppressions import (
    history_page,
    list_suppressions,
    save_suppression,
)
from app.services.team_assessment_access import assessment_request

router = APIRouter(tags=["indicator intelligence"])


def _actor(request: Request, user: User, access: DataAccessContext):
    authorization = get_authorization_context(request)
    if authorization is None:
        raise ApiHTTPException(
            status_code=503,
            error_code="indicator_access_unavailable",
            detail="Indicator access could not be evaluated. Retry the request.",
        )
    return assessment_request(
        request, user=user, authorization=authorization, access=access
    )


@router.get("/items/{item_id}/indicators", response_model=IndicatorPage)
def get_item_indicators(
    item_id: uuid.UUID,
    request: Request,
    team_id: uuid.UUID | None = None,
    ioc_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:items")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> IndicatorPage:
    return list_indicators(
        db,
        actor=_actor(request, user, access),
        item_id=item_id,
        team_id=team_id,
        ioc_id=ioc_id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/items/{item_id}/indicators/{ioc_id}/assessment", response_model=AssessmentResponse
)
def patch_indicator_assessment(
    item_id: uuid.UUID,
    ioc_id: uuid.UUID,
    payload: AssessmentCommand,
    request: Request,
    team_id: uuid.UUID = Query(),
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> AssessmentResponse:
    try:
        result = update_assessment(
            db,
            actor=_actor(request, user, access),
            item_id=item_id,
            ioc_id=ioc_id,
            team_id=team_id,
            payload=payload,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.get(
    "/items/{item_id}/indicators/{ioc_id}/assessment/history",
    response_model=IndicatorHistoryPage,
)
def get_indicator_history(
    item_id: uuid.UUID,
    ioc_id: uuid.UUID,
    request: Request,
    team_id: uuid.UUID = Query(),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> IndicatorHistoryPage:
    return history_page(
        db,
        actor=_actor(request, user, access),
        item_id=item_id,
        ioc_id=ioc_id,
        team_id=team_id,
        page=page,
        page_size=page_size,
    )


@router.get("/teams/{team_id}/indicator-suppressions", response_model=SuppressionPage)
def get_indicator_suppressions(
    team_id: uuid.UUID,
    request: Request,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> SuppressionPage:
    return list_suppressions(
        db,
        actor=_actor(request, user, access),
        team_id=team_id,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/teams/{team_id}/indicator-suppressions",
    response_model=SuppressionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_indicator_suppression(
    team_id: uuid.UUID,
    payload: SuppressionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> SuppressionResponse:
    try:
        result = save_suppression(
            db, actor=_actor(request, user, access), team_id=team_id, payload=payload
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.patch(
    "/teams/{team_id}/indicator-suppressions/{suppression_id}",
    response_model=SuppressionResponse,
)
def patch_indicator_suppression(
    team_id: uuid.UUID,
    suppression_id: uuid.UUID,
    payload: SuppressionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> SuppressionResponse:
    try:
        result = save_suppression(
            db,
            actor=_actor(request, user, access),
            team_id=team_id,
            suppression_id=suppression_id,
            payload=payload,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.get(
    "/teams/{team_id}/indicator-suppressions/{suppression_id}/history",
    response_model=IndicatorHistoryPage,
)
def get_suppression_history(
    team_id: uuid.UUID,
    suppression_id: uuid.UUID,
    request: Request,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> IndicatorHistoryPage:
    return history_page(
        db,
        actor=_actor(request, user, access),
        team_id=team_id,
        suppression_id=suppression_id,
        page=page,
        page_size=page_size,
    )
