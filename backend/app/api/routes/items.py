import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_state import ItemState
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.item import (
    ItemDetailResponse,
    ItemListEntry,
    ItemListResponse,
    ItemStateResponse,
    ItemTagsUpdateRequest,
    NoteUpdateRequest,
    ReadUpdateRequest,
    StarUpdateRequest,
)

router = APIRouter(prefix="/items", tags=["items"])


def _get_or_create_state(db: Session, user_id: uuid.UUID, item_id: uuid.UUID) -> ItemState:
    state = db.scalar(
        select(ItemState).where(
            and_(
                ItemState.user_id == user_id,
                ItemState.item_id == item_id,
            )
        )
    )
    if state is None:
        state = ItemState(user_id=user_id, item_id=item_id)
        db.add(state)
        db.flush()
    return state


@router.get("", response_model=ItemListResponse)
def list_items(
    q: str | None = None,
    feed_id: uuid.UUID | None = None,
    tag: str | None = None,
    is_starred: bool | None = None,
    is_read: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    sort: str = Query(default="published_at_desc"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    state_subq = (
        select(
            ItemState.item_id.label("item_id"),
            ItemState.is_read.label("is_read"),
            ItemState.is_starred.label("is_starred"),
        )
        .where(ItemState.user_id == user.id)
        .subquery()
    )

    query = (
        select(
            Item,
            Feed.name.label("feed_name"),
            func.coalesce(state_subq.c.is_read, False).label("is_read"),
            func.coalesce(state_subq.c.is_starred, False).label("is_starred"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(state_subq, state_subq.c.item_id == Item.id)
    )

    filters = []
    if q:
        pattern = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(Item.title).like(pattern),
                func.lower(func.coalesce(Item.summary, "")).like(pattern),
                func.lower(cast(Item.url, String)).like(pattern),
            )
        )
    if feed_id:
        filters.append(Item.feed_id == feed_id)
    if since:
        filters.append(Item.first_seen_at >= since)
    if until:
        filters.append(Item.first_seen_at <= until)
    if is_read is not None:
        filters.append(func.coalesce(state_subq.c.is_read, False) == is_read)
    if is_starred is not None:
        filters.append(func.coalesce(state_subq.c.is_starred, False) == is_starred)
    if tag:
        query = query.join(ItemTag, ItemTag.item_id == Item.id).join(Tag, Tag.id == ItemTag.tag_id)
        filters.append(Tag.name == tag)

    if filters:
        query = query.where(and_(*filters))

    count_stmt = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_stmt) or 0

    order_clauses = {
        "published_at_desc": Item.published_at.desc().nullslast(),
        "published_at_asc": Item.published_at.asc().nullsfirst(),
        "first_seen_desc": Item.first_seen_at.desc(),
        "first_seen_asc": Item.first_seen_at.asc(),
    }
    order_by = order_clauses.get(sort, order_clauses["published_at_desc"])

    rows = db.execute(query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)).all()

    item_ids = [row.Item.id for row in rows]
    tags_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
    if item_ids:
        tag_rows = db.execute(
            select(ItemTag.item_id, Tag.name)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id.in_(item_ids))
            .order_by(Tag.name.asc())
        ).all()
        for item_id_value, tag_name in tag_rows:
            tags_by_item[item_id_value].append(tag_name)

    entries = [
        ItemListEntry(
            id=row.Item.id,
            feed_id=row.Item.feed_id,
            feed_name=row.feed_name,
            url=row.Item.url,
            canonical_url=row.Item.canonical_url,
            title=row.Item.title,
            summary=row.Item.summary,
            published_at=row.Item.published_at,
            first_seen_at=row.Item.first_seen_at,
            status=row.Item.status,
            is_read=row.is_read,
            is_starred=row.is_starred,
            tags=tags_by_item.get(row.Item.id, []),
        )
        for row in rows
    ]

    return ItemListResponse(items=entries, total=total, page=page, page_size=page_size)


@router.get("/{item_id}", response_model=ItemDetailResponse)
def get_item(item_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.execute(
        select(Item, Feed.name.label("feed_name"))
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id == item_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    item = row.Item

    article = db.scalar(select(Article).where(Article.item_id == item_id))
    state = db.scalar(
        select(ItemState).where(and_(ItemState.user_id == user.id, ItemState.item_id == item_id))
    )
    if state is None:
        state_view = ItemStateResponse(is_read=False, is_starred=False, note=None, updated_at=None)
    else:
        state_view = ItemStateResponse(
            is_read=state.is_read,
            is_starred=state.is_starred,
            note=state.note,
            updated_at=state.updated_at,
        )

    tag_rows = db.execute(
        select(Tag.name).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item_id).order_by(Tag.name.asc())
    ).all()

    return ItemDetailResponse(
        id=item.id,
        feed_id=item.feed_id,
        feed_name=row.feed_name,
        source_guid=item.source_guid,
        url=item.url,
        canonical_url=item.canonical_url,
        title=item.title,
        summary=item.summary,
        published_at=item.published_at,
        first_seen_at=item.first_seen_at,
        status=item.status,
        last_error=item.last_error,
        tags=[tag_name for (tag_name,) in tag_rows],
        article=article,
        state=state_view,
    )


@router.post("/{item_id}/read", status_code=status.HTTP_200_OK)
def set_item_read(
    item_id: uuid.UUID,
    payload: ReadUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    state = _get_or_create_state(db, user.id, item_id)
    state.is_read = payload.is_read
    db.add(state)
    db.commit()
    return {"status": "ok"}


@router.post("/{item_id}/star", status_code=status.HTTP_200_OK)
def set_item_star(
    item_id: uuid.UUID,
    payload: StarUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    state = _get_or_create_state(db, user.id, item_id)
    state.is_starred = payload.is_starred
    db.add(state)
    db.commit()
    return {"status": "ok"}


@router.post("/{item_id}/note", status_code=status.HTTP_200_OK)
def set_item_note(
    item_id: uuid.UUID,
    payload: NoteUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    state = _get_or_create_state(db, user.id, item_id)
    state.note = payload.note
    db.add(state)
    db.commit()
    return {"status": "ok"}


@router.post("/{item_id}/tags", status_code=status.HTTP_200_OK)
def set_item_tags(
    item_id: uuid.UUID,
    payload: ItemTagsUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    _ = user
    db.query(ItemTag).filter(ItemTag.item_id == item_id).delete(synchronize_session=False)

    if payload.tag_ids:
        valid_tags = db.scalars(select(Tag.id).where(Tag.id.in_(payload.tag_ids))).all()
        valid_tag_set = set(valid_tags)
        for tag_id in payload.tag_ids:
            if tag_id in valid_tag_set:
                db.add(ItemTag(item_id=item_id, tag_id=tag_id))

    db.commit()
    return {"status": "ok"}
