import uuid
from datetime import datetime, timedelta, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_operator_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_ITEMS, SCOPE_WRITE_ITEMS
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.item_state import ItemState
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.item import (
    ItemDetailResponse,
    ItemGraphEdgeResponse,
    ItemGraphNodeResponse,
    ItemGraphResponse,
    ItemClassificationResponse,
    ItemListEntry,
    ItemListResponse,
    ItemStateResponse,
    ItemTagsUpdateRequest,
    NoteUpdateRequest,
    ReadUpdateRequest,
    StarUpdateRequest,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/items", tags=["items"])
TITLE_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")


def _parse_feed_ids(feed_ids: str | None) -> list[uuid.UUID]:
    if not feed_ids:
        return []

    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in feed_ids.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            feed_uuid = uuid.UUID(candidate)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid feed id: {candidate}",
            ) from exc

        if feed_uuid not in seen:
            parsed.append(feed_uuid)
            seen.add(feed_uuid)
    return parsed



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
    feed_ids: str | None = Query(default=None),
    tag: str | None = None,
    is_starred: bool | None = None,
    is_read: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    sort: str = Query(default="published_at_desc"),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    selected_feed_ids = _parse_feed_ids(feed_ids)
    if feed_id and feed_id not in selected_feed_ids:
        selected_feed_ids.append(feed_id)

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
            ItemClassification.primary_category.label("primary_category"),
            func.coalesce(state_subq.c.is_read, False).label("is_read"),
            func.coalesce(state_subq.c.is_starred, False).label("is_starred"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
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
    if selected_feed_ids:
        filters.append(Item.feed_id.in_(selected_feed_ids))
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
            classification=row.primary_category,
            is_read=row.is_read,
            is_starred=row.is_starred,
            tags=tags_by_item.get(row.Item.id, []),
        )
        for row in rows
    ]

    return ItemListResponse(items=entries, total=total, page=page, page_size=page_size)


@router.get("/{item_id}", response_model=ItemDetailResponse)
def get_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    row = db.execute(
        select(Item, Feed.name.label("feed_name"))
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id == item_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    item = row.Item

    article = db.scalar(select(Article).where(Article.item_id == item_id))
    classification = db.scalar(select(ItemClassification).where(ItemClassification.item_id == item_id))
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
        classification=ItemClassificationResponse(
            primary_category=classification.primary_category,
            secondary_categories=classification.secondary_categories or [],
            confidence=classification.confidence,
            scores=classification.scores_json or {},
            rules_version=classification.rules_version,
            classified_at=classification.classified_at,
        )
        if classification
        else None,
        last_error=item.last_error,
        tags=[tag_name for (tag_name,) in tag_rows],
        article=article,
        state=state_view,
    )


@router.get("/{item_id}/graph", response_model=ItemGraphResponse)
def get_item_graph(
    item_id: uuid.UUID,
    limit: int = Query(default=12, ge=1, le=30),
    since_days: int = Query(default=30, ge=1, le=180),
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    base_row = db.execute(
        select(Item, Feed.name.label("feed_name"), ItemClassification)
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == item_id)
    ).first()
    if base_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    base_item = base_row.Item
    base_feed_name = base_row.feed_name
    base_classification = base_row.ItemClassification

    if base_classification is None:
        center_node = ItemGraphNodeResponse(
            id=f"item:{base_item.id}",
            type="item",
            label=base_item.title,
            metadata={
                "item_id": str(base_item.id),
                "feed_name": base_feed_name,
                "classification": None,
            },
        )
        return ItemGraphResponse(nodes=[center_node], edges=[])

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    candidate_rows = db.execute(
        select(Item, Feed.name.label("feed_name"), ItemClassification)
        .join(Feed, Feed.id == Item.feed_id)
        .join(ItemClassification, ItemClassification.item_id == Item.id)
        .where(
            and_(
                Item.id != item_id,
                Item.first_seen_at >= cutoff,
            )
        )
        .order_by(Item.first_seen_at.desc())
        .limit(500)
    ).all()

    scored: list[tuple[float, object]] = []
    base_secondary = set(base_classification.secondary_categories or [])
    base_primary = base_classification.primary_category
    base_tokens = _tokenize_title(base_item.title)

    for row in candidate_rows:
        classification = row.ItemClassification
        score = 0.0

        if classification.primary_category == base_primary:
            score += 3.0

        candidate_secondary = set(classification.secondary_categories or [])
        overlap = len(base_secondary.intersection(candidate_secondary))
        if overlap:
            score += overlap * 1.5

        if row.Item.feed_id == base_item.feed_id:
            score += 0.7

        token_overlap = len(base_tokens.intersection(_tokenize_title(row.Item.title)))
        if token_overlap:
            score += min(2.0, token_overlap * 0.35)

        if score >= 1.5:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_rows = scored[:limit]

    center_id = f"item:{base_item.id}"
    nodes: dict[str, ItemGraphNodeResponse] = {
        center_id: ItemGraphNodeResponse(
            id=center_id,
            type="item",
            label=base_item.title,
            metadata={
                "item_id": str(base_item.id),
                "feed_name": base_feed_name,
                "classification": base_primary,
                "confidence": base_classification.confidence,
                "published_at": base_item.published_at.isoformat() if base_item.published_at else None,
            },
        )
    }
    edges: list[ItemGraphEdgeResponse] = []

    base_category_id = f"category:{base_primary}"
    nodes[base_category_id] = ItemGraphNodeResponse(
        id=base_category_id,
        type="category",
        label=base_primary,
        metadata={},
    )
    edges.append(
        ItemGraphEdgeResponse(
            source=center_id,
            target=base_category_id,
            relation="classified_as",
            weight=1.0,
        )
    )

    for score, row in top_rows:
        item = row.Item
        feed_name = row.feed_name
        classification = row.ItemClassification
        node_id = f"item:{item.id}"
        nodes[node_id] = ItemGraphNodeResponse(
            id=node_id,
            type="item",
            label=item.title,
            metadata={
                "item_id": str(item.id),
                "feed_name": feed_name,
                "classification": classification.primary_category,
                "confidence": classification.confidence,
                "published_at": item.published_at.isoformat() if item.published_at else None,
            },
        )
        edges.append(
            ItemGraphEdgeResponse(
                source=center_id,
                target=node_id,
                relation="related",
                weight=round(float(score), 3),
            )
        )

        category_id = f"category:{classification.primary_category}"
        if category_id not in nodes:
            nodes[category_id] = ItemGraphNodeResponse(
                id=category_id,
                type="category",
                label=classification.primary_category,
                metadata={},
            )
        edges.append(
            ItemGraphEdgeResponse(
                source=node_id,
                target=category_id,
                relation="classified_as",
                weight=1.0,
            )
        )

    return ItemGraphResponse(nodes=list(nodes.values()), edges=edges)


@router.post("/{item_id}/read", status_code=status.HTTP_200_OK)
def set_item_read(
    item_id: uuid.UUID,
    payload: ReadUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_ITEMS)),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    state = _get_or_create_state(db, user.id, item_id)
    state.is_read = payload.is_read
    db.add(state)
    record_audit(
        db,
        actor_user_id=user.id,
        action="items.set_read",
        resource_type="item",
        resource_id=str(item_id),
        metadata={"is_read": payload.is_read},
    )
    db.commit()
    return {"status": "ok"}


def _tokenize_title(value: str) -> set[str]:
    return set(TITLE_TOKEN_RE.findall((value or "").lower()))


@router.post("/{item_id}/star", status_code=status.HTTP_200_OK)
def set_item_star(
    item_id: uuid.UUID,
    payload: StarUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_ITEMS)),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    state = _get_or_create_state(db, user.id, item_id)
    state.is_starred = payload.is_starred
    db.add(state)
    record_audit(
        db,
        actor_user_id=user.id,
        action="items.set_star",
        resource_type="item",
        resource_id=str(item_id),
        metadata={"is_starred": payload.is_starred},
    )
    db.commit()
    return {"status": "ok"}


@router.post("/{item_id}/note", status_code=status.HTTP_200_OK)
def set_item_note(
    item_id: uuid.UUID,
    payload: NoteUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_ITEMS)),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    state = _get_or_create_state(db, user.id, item_id)
    state.note = payload.note
    db.add(state)
    record_audit(
        db,
        actor_user_id=user.id,
        action="items.set_note",
        resource_type="item",
        resource_id=str(item_id),
    )
    db.commit()
    return {"status": "ok"}


@router.post("/{item_id}/tags", status_code=status.HTTP_200_OK)
def set_item_tags(
    item_id: uuid.UUID,
    payload: ItemTagsUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_ITEMS)),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    db.query(ItemTag).filter(ItemTag.item_id == item_id).delete(synchronize_session=False)

    applied: list[str] = []
    if payload.tag_ids:
        valid_tags = db.scalars(select(Tag.id).where(Tag.id.in_(payload.tag_ids))).all()
        valid_tag_set = set(valid_tags)
        for tag_id in payload.tag_ids:
            if tag_id in valid_tag_set:
                db.add(ItemTag(item_id=item_id, tag_id=tag_id))
                applied.append(str(tag_id))

    record_audit(
        db,
        actor_user_id=user.id,
        action="items.set_tags",
        resource_type="item",
        resource_id=str(item_id),
        metadata={"tag_ids": applied},
    )
    db.commit()
    return {"status": "ok"}
