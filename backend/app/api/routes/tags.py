from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_operator_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_TAGS, SCOPE_WRITE_TAGS
from app.db.session import get_db
from app.models.tag import Tag
from app.models.user import User
from app.schemas.tag import TagCreate, TagResponse
from app.services.audit import record_audit

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagResponse])
def list_tags(
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_TAGS)),
):
    tags = db.scalars(select(Tag).order_by(Tag.name.asc())).all()
    return list(tags)


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_TAGS)),
):
    existing = db.scalar(select(Tag).where(Tag.name == payload.name.lower()))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag already exists")

    tag = Tag(name=payload.name.lower())
    db.add(tag)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="tags.create",
        resource_type="tag",
        resource_id=str(tag.id),
        metadata={"name": tag.name},
    )
    db.commit()
    db.refresh(tag)
    return tag
