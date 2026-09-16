"""Explicit team assessment requests and analyst-reviewed investigation creation."""

from contextlib import contextmanager
import logging
import uuid

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.core.api_errors import ApiHTTPException
from app.db.session import get_db
from app.models.user import User
from app.schemas.team_assessments import (
    HuntReviewCommand,
    TeamAssessmentCommand,
    TeamAssessmentEnvelope,
)
from app.services.ai_workflow_publication import publish_ai_workflow
from app.services.data_access_policy import DataAccessContext
from app.services.investigation_contracts import (
    InvestigationConflictError,
    InvestigationNotFoundError,
    InvestigationPermissionError,
    InvestigationValidationError,
)
from app.services.team_assessments import (
    create_hunt_investigation,
    get_assessment,
    queue_assessment,
    review_hunt,
)
from app.services.team_assessment_access import AssessmentRequest, assessment_request

router = APIRouter(prefix="/items/{item_id}/team-assessment", tags=["team assessments"])
logger = logging.getLogger("threatlens.api")


def _actor(
    request: Request, user: User, access: DataAccessContext
) -> AssessmentRequest:
    authorization = get_authorization_context(request)
    if authorization is None:
        raise ApiHTTPException(
            status_code=503,
            error_code="team_assessment_access_unavailable",
            detail="Team assessment access could not be evaluated. Retry the request.",
        )
    return assessment_request(
        request, user=user, authorization=authorization, access=access
    )


def publish_accepted_assessment(db: Session, run_id: uuid.UUID) -> None:
    """Publication failure leaves the committed outbox entry eligible for recovery."""
    bind = db.get_bind()

    @contextmanager
    def publication_session():
        with Session(bind=bind) as publication_db:
            yield publication_db

    try:
        publish_ai_workflow(run_id, session_factory=publication_session)
    except Exception as exc:
        logger.warning(
            "team_assessment_publication_deferred run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )


@router.get("", response_model=TeamAssessmentEnvelope)
def get_team_assessment_route(
    item_id: uuid.UUID,
    request: Request,
    team_id: uuid.UUID = Query(),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> TeamAssessmentEnvelope:
    return get_assessment(
        db, actor=_actor(request, user, access), team_id=team_id, item_id=item_id
    )


@router.post(
    "", response_model=TeamAssessmentEnvelope, status_code=status.HTTP_202_ACCEPTED
)
def queue_team_assessment_route(
    item_id: uuid.UUID,
    payload: TeamAssessmentCommand,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> TeamAssessmentEnvelope:
    try:
        response, run_id = queue_assessment(
            db, actor=_actor(request, user, access), item_id=item_id, payload=payload
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    publish_accepted_assessment(db, run_id)
    return response


@router.patch("/hunts/{hunt_id}", response_model=TeamAssessmentEnvelope)
def review_team_hunt_route(
    item_id: uuid.UUID,
    payload: HuntReviewCommand,
    request: Request,
    hunt_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> TeamAssessmentEnvelope:
    try:
        response = review_hunt(
            db,
            actor=_actor(request, user, access),
            item_id=item_id,
            hunt_id=hunt_id,
            payload=payload,
        )
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise


@router.post("/hunts/{hunt_id}/investigation", response_model=TeamAssessmentEnvelope)
def create_team_hunt_investigation_route(
    item_id: uuid.UUID,
    payload: TeamAssessmentCommand,
    request: Request,
    hunt_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions(
            "read:items", "read:teams", "write:teams", "write:investigations"
        )
    ),
    access: DataAccessContext = Depends(get_data_access_context),
) -> TeamAssessmentEnvelope:
    try:
        response = create_hunt_investigation(
            db,
            actor=_actor(request, user, access),
            item_id=item_id,
            hunt_id=hunt_id,
            payload=payload,
        )
        db.commit()
        return response
    except (
        InvestigationNotFoundError,
        InvestigationPermissionError,
        InvestigationConflictError,
        InvestigationValidationError,
    ) as exc:
        db.rollback()
        status_code = (
            404
            if isinstance(exc, InvestigationNotFoundError)
            else 403
            if isinstance(exc, InvestigationPermissionError)
            else 409
            if isinstance(exc, InvestigationConflictError)
            else 422
        )
        raise ApiHTTPException(
            status_code=status_code,
            error_code=getattr(exc, "code", "hunt_investigation_failed"),
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise
