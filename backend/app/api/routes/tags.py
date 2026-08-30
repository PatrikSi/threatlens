from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthenticatedPrincipal,
    get_data_access_context,
    require_permissions,
)
from app.core.token_scopes import SCOPE_READ_TAGS, SCOPE_WRITE_TAGS
from app.db.session import get_db
from app.models.feed import Feed
from app.models.item import Item
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.tag import TagCreate, TagResponse
from app.services.audit import record_audit
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagResponse])
def list_tags(
    db: Session = Depends(get_db),
    _principal: AuthenticatedPrincipal = Depends(require_permissions(SCOPE_READ_TAGS)),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    feed_access_filter = handling_label_access_predicate(
        Feed.handling_label_id, data_access
    )
    tags = db.scalars(
        select(Tag)
        .where(
            exists(
                select(1)
                .select_from(ItemTag)
                .join(Item, Item.id == ItemTag.item_id)
                .join(Feed, Feed.id == Item.feed_id)
                .where(ItemTag.tag_id == Tag.id, feed_access_filter)
            )
        )
        .order_by(Tag.name.asc())
    ).all()
    return list(tags)


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_WRITE_TAGS)),
):
    existing = db.scalar(select(Tag).where(Tag.name == payload.name.lower()))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Tag already exists"
        )

    tag = Tag(name=payload.name.lower())
    try:
        with db.begin_nested():
            db.add(tag)
            db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Tag already exists"
        ) from exc

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
