import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.saved_view import SavedView
from app.models.user import User
from app.schemas.view import SavedViewCreate, SavedViewResponse

router = APIRouter(prefix="/views", tags=["views"])


@router.get("", response_model=list[SavedViewResponse])
def list_views(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    views = db.scalars(
        select(SavedView)
        .where(SavedView.user_id == user.id)
        .order_by(SavedView.created_at.desc())
    ).all()
    return list(views)


@router.post("", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
def create_view(payload: SavedViewCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    view = SavedView(user_id=user.id, name=payload.name, query_json=payload.query_json)
    db.add(view)
    db.commit()
    db.refresh(view)
    return view


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(view_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    view = db.scalar(select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user.id))
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")

    db.delete(view)
    db.commit()
