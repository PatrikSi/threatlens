"""Reviewed exports are deliberate team approvals, separate from raw research."""

import uuid
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.api.deps import get_authorization_context, get_data_access_context, require_permissions
from app.core.api_errors import ApiHTTPException
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from app.db.session import get_db
from app.models.user import User
from app.schemas.indicator_publications import PublicationCreate, PublicationPage, PublicationPreview, PublicationPreviewRequest, PublicationResponse, PublicationWithdraw
from app.services.data_access_policy import DataAccessContext
from app.services.export_transport import DeadlineResponse
from app.services.indicator_publication_formats import publication_bytes
from app.services.indicator_publications import create_publication, list_publications, load_publication, preview_publication, withdraw_publication
from app.services.team_assessment_access import AssessmentRequest, assessment_request

router = APIRouter(prefix="/teams/{team_id}/indicator-publications", tags=["reviewed intelligence"])
Result = TypeVar("Result")


def _actor(request: Request, user: User, access: DataAccessContext) -> AssessmentRequest:
    authorization = get_authorization_context(request)
    if authorization is None:
        raise ApiHTTPException(status_code=503, error_code="publication_access_unavailable", detail="Publication access could not be evaluated. Retry the request.")
    return assessment_request(request, user=user, authorization=authorization, access=access)


def _operation(db: Session, call: Callable[[], Result], *, commit: bool = True) -> Result:
    try:
        with database_operation(db, operation="interactive"):
            result = call()
            if commit:
                db.commit()
        return result
    except DatabaseDeadlineExceeded as exc:
        db.rollback()
        raise ApiHTTPException(status_code=503, error_code="publication_busy", detail="Publication is waiting for a concurrent update. Retry with the same request identity.") from exc
    except OperationalError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) in {"55P03", "57014", "40P01"}:
            raise ApiHTTPException(status_code=503, error_code="publication_busy", detail="Publication is waiting for a concurrent update. Retry with the same request identity.") from exc
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/preview", response_model=PublicationPreview)
def preview_indicator_publication(
    team_id: uuid.UUID, payload: PublicationPreviewRequest, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
):
    return _operation(db, lambda: preview_publication(db, actor=_actor(request, user, access), team_id=team_id, filters=payload.filters))


@router.post("", response_model=PublicationResponse, status_code=201)
def create_indicator_publication(
    team_id: uuid.UUID, payload: PublicationCreate, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_permissions("read:items", "read:teams", "write:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
):
    return _operation(db, lambda: PublicationResponse.model_validate(create_publication(
        db, actor=_actor(request, user, access), team_id=team_id, payload=payload,
    )))


@router.get("", response_model=PublicationPage)
def list_indicator_publications(
    team_id: uuid.UUID, request: Request, limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=512),
    db: Session = Depends(get_db), user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
):
    return _operation(db, lambda: list_publications(db, actor=_actor(request, user, access), team_id=team_id, cursor=cursor, limit=limit))


@router.get("/{publication_id}/download")
def download_indicator_publication(
    team_id: uuid.UUID, publication_id: uuid.UUID, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_permissions("read:items", "read:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
):
    actor = _actor(request, user, access)
    def load():
        return load_publication(db, actor=actor, team_id=team_id, publication_id=publication_id)
    # Persist newly discovered withdrawals, then reacquire all access/source
    # fences. The request-scoped session owns those locks until transfer ends.
    _operation(db, load)
    def build() -> tuple[bytes, str]:
        row = load()
        return publication_bytes(row), row.format
    content, format_name = _operation(db, build, commit=False)
    return DeadlineResponse(content=content, media_type="application/stix+json" if format_name == "stix" else "application/json", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="reviewed-{publication_id}.{format_name}.json"',
    })


@router.post("/{publication_id}/withdraw", response_model=PublicationResponse)
def withdraw_indicator_publication(
    team_id: uuid.UUID, publication_id: uuid.UUID, payload: PublicationWithdraw, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_permissions("read:items", "read:teams", "write:teams")),
    access: DataAccessContext = Depends(get_data_access_context),
):
    return _operation(db, lambda: PublicationResponse.model_validate(withdraw_publication(
        db, actor=_actor(request, user, access), team_id=team_id,
        publication_id=publication_id, expected_revision=payload.expected_revision,
    )))
