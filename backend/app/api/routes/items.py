import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_operator_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_ITEMS, SCOPE_WRITE_ITEMS
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.item_state import ItemState
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.item import (
    ItemDetailResponse,
    ItemGraphResponse,
    ItemClassificationResponse,
    ItemAIInsightResponse,
    ItemListEntry,
    ItemListResponse,
    ItemStateResponse,
    ItemTagSuggestionListResponse,
    ItemTagsUpdateRequest,
    NoteUpdateRequest,
    ReadUpdateRequest,
    StarUpdateRequest,
)
from app.services.audit import record_audit
from app.services.item_state import get_or_create_item_state
from app.services.item_views import build_item_graph, load_item_tag_suggestions, load_tags_for_items
from app.services.tag_feedback import record_feedback_events
from app.tasks.feed_tasks import fetch_article

router = APIRouter(prefix="/items", tags=["items"])


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


def _parse_tag_filters(tag: str | None, tags: str | None) -> list[str]:
    raw_values: list[str] = []
    if tag:
        raw_values.append(tag)
    if tags:
        raw_values.extend(tags.split(","))

    selected: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = raw.strip().lower()
        if not value or value == "content_fetched" or value in seen:
            continue
        selected.append(value)
        seen.add(value)
    return selected


def _escape_like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=ItemListResponse)
def list_items(
    q: str | None = None,
    feed_id: uuid.UUID | None = None,
    feed_ids: str | None = Query(default=None),
    tag: str | None = None,
    tags: str | None = Query(default=None),
    tags_mode: str = Query(default="any", pattern="^(any|all)$"),
    is_starred: bool | None = None,
    is_read: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    has_article: bool | None = None,
    date_basis: str = Query(default="first_seen_at", pattern="^(first_seen_at|published_at_or_first_seen_at)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    sort: str = Query(
        default="published_at_desc",
        pattern="^(published_at_desc|published_at_asc|first_seen_desc|first_seen_asc)$",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    selected_feed_ids = _parse_feed_ids(feed_ids)
    selected_tags = _parse_tag_filters(tag, tags)
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
            ItemAIEnrichment.relevance_score.label("ai_relevance_score"),
            ItemAIEnrichment.relevance_label.label("ai_relevance_label"),
            ItemAIEnrichment.status.label("ai_status"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(state_subq, state_subq.c.item_id == Item.id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
    )

    filters = []
    if q:
        pattern = f"%{_escape_like_value(q.lower())}%"
        filters.append(
            or_(
                func.lower(Item.title).like(pattern, escape="\\"),
                func.lower(func.coalesce(Item.summary, "")).like(pattern, escape="\\"),
                func.lower(cast(Item.url, String)).like(pattern, escape="\\"),
            )
        )
    if selected_feed_ids:
        filters.append(Item.feed_id.in_(selected_feed_ids))
    timeline_at = Item.first_seen_at
    if date_basis == "published_at_or_first_seen_at":
        timeline_at = func.coalesce(Item.published_at, Item.first_seen_at)
    if since:
        filters.append(timeline_at >= since)
    if until:
        filters.append(timeline_at <= until)
    if has_article is not None:
        article_exists = (
            select(Article.id)
            .where(and_(Article.item_id == Item.id, Article.text.is_not(None)))
            .exists()
        )
        filters.append(article_exists if has_article else ~article_exists)
    if is_read is not None:
        filters.append(func.coalesce(state_subq.c.is_read, False) == is_read)
    if is_starred is not None:
        filters.append(func.coalesce(state_subq.c.is_starred, False) == is_starred)
    if selected_tags:
        if tags_mode == "all":
            for selected_tag in selected_tags:
                filters.append(
                    select(ItemTag.item_id)
                    .join(Tag, Tag.id == ItemTag.tag_id)
                    .where(and_(ItemTag.item_id == Item.id, func.lower(Tag.name) == selected_tag))
                    .exists()
                )
        else:
            filters.append(
                select(ItemTag.item_id)
                .join(Tag, Tag.id == ItemTag.tag_id)
                .where(and_(ItemTag.item_id == Item.id, func.lower(Tag.name).in_(selected_tags)))
                .exists()
            )

    if filters:
        query = query.where(and_(*filters))

    count_stmt = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_stmt) or 0

    order_clauses = {
        "published_at_desc": [
            Item.published_at.desc().nullslast(),
            Item.first_seen_at.desc(),
            Item.id.desc(),
        ],
        "published_at_asc": [
            Item.published_at.asc().nullsfirst(),
            Item.first_seen_at.asc(),
            Item.id.asc(),
        ],
        "first_seen_desc": [
            Item.first_seen_at.desc(),
            Item.id.desc(),
        ],
        "first_seen_asc": [
            Item.first_seen_at.asc(),
            Item.id.asc(),
        ],
    }
    order_by = order_clauses[sort]

    rows = db.execute(query.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)).all()

    item_ids = [row.Item.id for row in rows]
    tags_by_item, tag_details_by_item = load_tags_for_items(db, item_ids=item_ids)

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
            tag_details=tag_details_by_item.get(row.Item.id, []),
            ai_relevance_score=float(row.ai_relevance_score) if row.ai_relevance_score is not None else None,
            ai_relevance_label=row.ai_relevance_label,
            ai_status=row.ai_status,
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
    enrichment = db.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item_id))
    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
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

    tags_by_item, tag_details_by_item = load_tags_for_items(db, item_ids=[item_id])
    existing_tag_names = tags_by_item.get(item_id, [])
    tag_suggestions = load_item_tag_suggestions(
        db,
        item=item,
        classification=classification,
        article=article,
        feed=feed,
        existing_tag_names=existing_tag_names,
    )

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
        tags=existing_tag_names,
        tag_details=tag_details_by_item.get(item_id, []),
        tag_suggestions=tag_suggestions,
        ai_insight=ItemAIInsightResponse(
            status=enrichment.status,
            summary_text=enrichment.summary_text,
            relevance_score=float(enrichment.relevance_score) if enrichment.relevance_score is not None else None,
            relevance_label=enrichment.relevance_label,
            relevance_reasons=list(enrichment.relevance_reasons_json or []),
            model=enrichment.model,
            generated_at=enrichment.generated_at,
            error=enrichment.error,
        )
        if enrichment
        else None,
        article=article,
        state=state_view,
    )


@router.get("/{item_id}/graph", response_model=ItemGraphResponse)
def get_item_graph(
    item_id: uuid.UUID,
    focus_node_id: str | None = Query(default=None),
    related_item_limit: int = Query(default=16, ge=1, le=60),
    ioc_limit: int = Query(default=18, ge=1, le=60),
    since_days: int = Query(default=30, ge=1, le=180),
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    return build_item_graph(
        db,
        item_id=item_id,
        focus_node_id=focus_node_id,
        related_item_limit=related_item_limit,
        ioc_limit=ioc_limit,
        since_days=since_days,
    )


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

    state = get_or_create_item_state(db, user_id=user.id, item_id=item_id)
    previous_is_read = state.is_read
    state.is_read = payload.is_read
    db.add(state)
    if previous_is_read != payload.is_read:
        existing_tag_names, _ = load_tags_for_items(db, item_ids=[item_id])
        record_feedback_events(
            db,
            user_id=user.id,
            item_id=item_id,
            signal_type="read" if payload.is_read else "unread",
            tag_names=existing_tag_names.get(item_id, []),
        )
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

    state = get_or_create_item_state(db, user_id=user.id, item_id=item_id)
    previous_is_starred = state.is_starred
    state.is_starred = payload.is_starred
    db.add(state)
    if previous_is_starred != payload.is_starred:
        existing_tag_names, _ = load_tags_for_items(db, item_ids=[item_id])
        record_feedback_events(
            db,
            user_id=user.id,
            item_id=item_id,
            signal_type="star" if payload.is_starred else "unstar",
            tag_names=existing_tag_names.get(item_id, []),
        )
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

    state = get_or_create_item_state(db, user_id=user.id, item_id=item_id)
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


@router.post("/{item_id}/retry-article-fetch", status_code=status.HTTP_202_ACCEPTED)
def retry_item_article_fetch(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_operator_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_ITEMS)),
):
    item = db.scalar(select(Item.id).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    try:
        task = fetch_article.delay(str(item_id), force=True)
    except Exception as exc:
        record_audit(
            db,
            actor_user_id=user.id,
            action="items.retry_article_fetch",
            resource_type="item",
            resource_id=str(item_id),
            success=False,
            metadata={"error": "task_queue_unavailable"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is temporarily unavailable. Try again later.",
        ) from exc

    record_audit(
        db,
        actor_user_id=user.id,
        action="items.retry_article_fetch",
        resource_type="item",
        resource_id=str(item_id),
        metadata={"task_id": getattr(task, "id", None)},
    )
    db.commit()
    return {"status": "queued"}


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

    existing_tag_rows = db.execute(
        select(Tag.name).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item_id)
    ).all()
    existing_tag_names = {tag_name for (tag_name,) in existing_tag_rows}

    requested_tag_ids = list(payload.tag_ids)
    applied: list[str] = []
    requested_tag_names: list[str] = []
    if requested_tag_ids:
        valid_tag_rows = db.scalars(select(Tag).where(Tag.id.in_(requested_tag_ids))).all()
        valid_tag_by_id = {tag.id: tag for tag in valid_tag_rows}
        valid_tag_set = set(valid_tag_by_id.keys())
        missing_tag_ids = [str(tag_id) for tag_id in requested_tag_ids if tag_id not in valid_tag_set]
        if missing_tag_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown tag IDs: {', '.join(missing_tag_ids)}",
            )
        requested_tag_names = [valid_tag_by_id[tag_id].name for tag_id in requested_tag_ids]

    db.query(ItemTag).filter(ItemTag.item_id == item_id).delete(synchronize_session=False)

    for tag_id in requested_tag_ids:
        db.add(
            ItemTag(
                item_id=item_id,
                tag_id=tag_id,
                source="manual",
                confidence=1.0,
                rules_version="manual:v1",
            )
        )
        applied.append(str(tag_id))

    requested_tag_name_set = set(requested_tag_names)
    added_tag_names = sorted(requested_tag_name_set - existing_tag_names)
    removed_tag_names = sorted(existing_tag_names - requested_tag_name_set)
    if added_tag_names:
        record_feedback_events(
            db,
            user_id=user.id,
            item_id=item_id,
            signal_type="manual_add",
            tag_names=added_tag_names,
        )
    if removed_tag_names:
        record_feedback_events(
            db,
            user_id=user.id,
            item_id=item_id,
            signal_type="manual_remove",
            tag_names=removed_tag_names,
        )

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


@router.get("/{item_id}/tag-suggestions", response_model=ItemTagSuggestionListResponse)
def get_item_tag_suggestions(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(require_token_scopes(SCOPE_READ_ITEMS)),
):
    item = db.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    article = db.scalar(select(Article).where(Article.item_id == item_id))
    classification = db.scalar(select(ItemClassification).where(ItemClassification.item_id == item_id))
    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
    tags_by_item, _ = load_tags_for_items(db, item_ids=[item_id])

    suggestions = load_item_tag_suggestions(
        db,
        item=item,
        classification=classification,
        article=article,
        feed=feed,
        existing_tag_names=tags_by_item.get(item_id, []),
    )
    return ItemTagSuggestionListResponse(item_id=item_id, suggestions=suggestions)
