import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.token_scopes import SCOPE_READ_VIEWS, SCOPE_WRITE_VIEWS
from app.db.session import get_db
from app.models.saved_view import SavedView
from app.models.user import User
from app.schemas.view import SavedViewCreate, SavedViewResponse, SavedViewUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/views", tags=["views"])
MAX_SAVED_VIEW_WINDOWS = 12


def _serialize_saved_view(view: SavedView) -> SavedViewResponse:
    return SavedViewResponse.model_validate(view)


def _ensure_saved_view_window_limit(payload: SavedViewCreate | SavedViewUpdate) -> None:
    query = payload.query_json
    if query is not None and len(query.windows) > MAX_SAVED_VIEW_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Saved views can contain at most {MAX_SAVED_VIEW_WINDOWS} panels",
        )


@router.get("", response_model=list[SavedViewResponse])
def list_views(
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_VIEWS)),
):
    views = db.scalars(
        select(SavedView)
        .where(SavedView.user_id == user.id)
        .order_by(SavedView.created_at.desc())
    ).all()
    return [_serialize_saved_view(view) for view in views]


@router.post("", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
def create_view(
    payload: SavedViewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_VIEWS)),
):
    _ensure_saved_view_window_limit(payload)
    view = SavedView(
        user_id=user.id,
        name=payload.name,
        query_json=payload.query_json.model_dump(mode="python"),
    )
    db.add(view)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="views.create",
        resource_type="saved_view",
        resource_id=str(view.id),
        metadata={"name": view.name},
    )
    db.commit()
    db.refresh(view)
    return _serialize_saved_view(view)


@router.patch("/{view_id}", response_model=SavedViewResponse)
def update_view(
    view_id: uuid.UUID,
    payload: SavedViewUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_VIEWS)),
):
    _ensure_saved_view_window_limit(payload)
    view = db.scalar(select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user.id))
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")

    if payload.name is not None:
        view.name = payload.name
    if payload.query_json is not None:
        view.query_json = payload.query_json.model_dump(mode="python")

    record_audit(
        db,
        actor_user_id=user.id,
        action="views.update",
        resource_type="saved_view",
        resource_id=str(view.id),
        metadata={"name": view.name},
    )
    db.commit()
    db.refresh(view)
    return _serialize_saved_view(view)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(
    view_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_VIEWS)),
):
    view = db.scalar(select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user.id))
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")

    db.delete(view)
    record_audit(
        db,
        actor_user_id=user.id,
        action="views.delete",
        resource_type="saved_view",
        resource_id=str(view_id),
    )
    db.commit()
