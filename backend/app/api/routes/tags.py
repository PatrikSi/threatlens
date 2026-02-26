from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tag import Tag
from app.schemas.tag import TagCreate, TagResponse

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagResponse])
def list_tags(db: Session = Depends(get_db), _=Depends(get_current_user)):
    tags = db.scalars(select(Tag).order_by(Tag.name.asc())).all()
    return list(tags)


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag(payload: TagCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    existing = db.scalar(select(Tag).where(Tag.name == payload.name.lower()))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag already exists")

    tag = Tag(name=payload.name.lower())
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag
