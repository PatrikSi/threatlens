import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_operator_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_ITEMS, SCOPE_WRITE_ITEMS
from app.db.session import get_db
from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
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
    ItemAIInsightResponse,
    ItemListEntry,
    ItemListResponse,
    ItemStateResponse,
    ItemTagDetailResponse,
    ItemTagSuggestionListResponse,
    ItemTagSuggestionResponse,
    ItemTagsUpdateRequest,
    NoteUpdateRequest,
    ReadUpdateRequest,
    StarUpdateRequest,
)
from app.services.algorithm_tags import build_tag_candidates
from app.services.audit import record_audit
from app.services.tag_feedback import load_feedback_adjustments, record_feedback_events

router = APIRouter(prefix="/items", tags=["items"])
SUGGESTION_CONFIDENCE_MIN = 0.25
SUGGESTION_LIMIT = 12


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


def _parse_graph_node_id(node_id: str) -> tuple[str, uuid.UUID]:
    if ":" not in node_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid focus_node_id")

    kind, value = node_id.split(":", 1)
    if kind not in {"item", "ioc"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported focus node type")

    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid focus node id") from exc

    return kind, parsed


def _escape_like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_item_graph_node(
    *,
    item: Item,
    feed_name: str,
    classification: str | None,
    is_root: bool = False,
) -> ItemGraphNodeResponse:
    return ItemGraphNodeResponse(
        id=f"item:{item.id}",
        type="item",
        label=item.title,
        metadata={
            "item_id": str(item.id),
            "feed_name": feed_name,
            "classification": classification,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "is_root": is_root,
        },
    )


def _build_ioc_graph_node(ioc: IOC) -> ItemGraphNodeResponse:
    return ItemGraphNodeResponse(
        id=f"ioc:{ioc.id}",
        type=ioc.type,
        label=ioc.value_raw,
        metadata={
            "ioc_id": str(ioc.id),
            "ioc_type": ioc.type,
            "value_norm": ioc.value_norm,
            "last_seen_at": ioc.last_seen_at.isoformat() if ioc.last_seen_at else None,
        },
    )


def _upsert_graph_edge(
    *,
    edges: list[ItemGraphEdgeResponse],
    seen: set[tuple[str, str, str]],
    source: str,
    target: str,
    relation: str,
    weight: float,
):
    key = (source, target, relation)
    if key in seen:
        return
    seen.add(key)
    edges.append(
        ItemGraphEdgeResponse(
            source=source,
            target=target,
            relation=relation,
            weight=weight,
        )
    )



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
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            state = db.scalar(
                select(ItemState).where(
                    and_(
                        ItemState.user_id == user_id,
                        ItemState.item_id == item_id,
                    )
                )
            )
            if state is None:
                raise
    return state


def _load_tags_for_items(
    db: Session,
    *,
    item_ids: list[uuid.UUID],
) -> tuple[dict[uuid.UUID, list[str]], dict[uuid.UUID, list[ItemTagDetailResponse]]]:
    names_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
    details_by_item: dict[uuid.UUID, list[ItemTagDetailResponse]] = {item_id: [] for item_id in item_ids}
    if not item_ids:
        return names_by_item, details_by_item

    tag_rows = db.execute(
        select(
            ItemTag.item_id,
            Tag.id,
            Tag.name,
            ItemTag.source,
            ItemTag.confidence,
            ItemTag.rules_version,
        )
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id.in_(item_ids))
        .order_by(Tag.name.asc())
    ).all()
    for item_id_value, tag_id, tag_name, source, confidence, rules_version in tag_rows:
        names_by_item[item_id_value].append(tag_name)
        details_by_item[item_id_value].append(
            ItemTagDetailResponse(
                id=tag_id,
                name=tag_name,
                source=source,
                confidence=round(float(confidence), 3),
                rules_version=rules_version,
            )
        )
    return names_by_item, details_by_item


def _load_item_ioc_values_by_type(db: Session, *, item_id: uuid.UUID) -> dict[str, list[str]]:
    rows = db.execute(
        select(IOC.type, IOC.value_norm)
        .join(ItemIOC, ItemIOC.ioc_id == IOC.id)
        .where(ItemIOC.item_id == item_id)
    ).all()
    by_type: dict[str, list[str]] = {}
    for ioc_type, value_norm in rows:
        by_type.setdefault(ioc_type, []).append(value_norm)
    return by_type


def _load_item_tag_suggestions(
    db: Session,
    *,
    item: Item,
    classification: ItemClassification | None,
    article: Article | None,
    feed: Feed | None,
    existing_tag_names: list[str],
) -> list[ItemTagSuggestionResponse]:
    ioc_values_by_type = _load_item_ioc_values_by_type(db, item_id=item.id)

    base_candidates = build_tag_candidates(
        primary_category=classification.primary_category if classification else "threat_intelligence_research",
        secondary_categories=classification.secondary_categories if classification else [],
        classification_confidence=classification.confidence if classification else 0.35,
        ioc_values_by_type=ioc_values_by_type,
        title=item.title,
        summary=item.summary,
        article_text=article.text if article else None,
        feed_name=feed.name if feed else "",
        feed_url=feed.url if feed else "",
        feedback_adjustments={},
    )
    adjustments = load_feedback_adjustments(db, tag_names=[candidate.name for candidate in base_candidates])
    candidates = build_tag_candidates(
        primary_category=classification.primary_category if classification else "threat_intelligence_research",
        secondary_categories=classification.secondary_categories if classification else [],
        classification_confidence=classification.confidence if classification else 0.35,
        ioc_values_by_type=ioc_values_by_type,
        title=item.title,
        summary=item.summary,
        article_text=article.text if article else None,
        feed_name=feed.name if feed else "",
        feed_url=feed.url if feed else "",
        feedback_adjustments=adjustments,
    )

    existing = set(existing_tag_names)
    suggestions: list[ItemTagSuggestionResponse] = []
    for candidate in candidates:
        if candidate.name in existing:
            continue
        if candidate.confidence < SUGGESTION_CONFIDENCE_MIN:
            continue
        suggestions.append(
            ItemTagSuggestionResponse(
                name=candidate.name,
                source=candidate.source,
                confidence=round(candidate.confidence, 3),
                rules_version=candidate.rules_version,
            )
        )
        if len(suggestions) >= SUGGESTION_LIMIT:
            break
    return suggestions


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
    if since:
        filters.append(Item.first_seen_at >= since)
    if until:
        filters.append(Item.first_seen_at <= until)
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
        "published_at_desc": Item.published_at.desc().nullslast(),
        "published_at_asc": Item.published_at.asc().nullsfirst(),
        "first_seen_desc": Item.first_seen_at.desc(),
        "first_seen_asc": Item.first_seen_at.asc(),
    }
    order_by = order_clauses[sort]

    rows = db.execute(query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)).all()

    item_ids = [row.Item.id for row in rows]
    tags_by_item, tag_details_by_item = _load_tags_for_items(db, item_ids=item_ids)

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

    tags_by_item, tag_details_by_item = _load_tags_for_items(db, item_ids=[item_id])
    existing_tag_names = tags_by_item.get(item_id, [])
    tag_suggestions = _load_item_tag_suggestions(
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
    base_row = db.execute(
        select(
            Item,
            Feed.name.label("feed_name"),
            ItemClassification.primary_category.label("primary_category"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == item_id)
    ).first()
    if base_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    focus_kind: str = "item"
    focus_uuid: uuid.UUID = item_id
    if focus_node_id:
        focus_kind, focus_uuid = _parse_graph_node_id(focus_node_id)

    def load_item_rows(item_ids: list[uuid.UUID]) -> dict[uuid.UUID, object]:
        if not item_ids:
            return {}
        rows = db.execute(
            select(
                Item,
                Feed.name.label("feed_name"),
                ItemClassification.primary_category.label("primary_category"),
            )
            .join(Feed, Feed.id == Item.feed_id)
            .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
            .where(Item.id.in_(item_ids))
        ).all()
        return {row.Item.id: row for row in rows}

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    root_item_node_id = f"item:{item_id}"
    current_focus_node_id: str

    nodes: dict[str, ItemGraphNodeResponse] = {}
    edges: list[ItemGraphEdgeResponse] = []
    seen_edges: set[tuple[str, str, str]] = set()

    if focus_kind == "item":
        focus_row = base_row
        if focus_uuid != item_id:
            focus_row = db.execute(
                select(
                    Item,
                    Feed.name.label("feed_name"),
                    ItemClassification.primary_category.label("primary_category"),
                )
                .join(Feed, Feed.id == Item.feed_id)
                .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
                .where(Item.id == focus_uuid)
            ).first()
            if focus_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Focus item not found")

        focus_item = focus_row.Item
        current_focus_node_id = f"item:{focus_item.id}"
        nodes[current_focus_node_id] = _build_item_graph_node(
            item=focus_item,
            feed_name=focus_row.feed_name,
            classification=focus_row.primary_category,
            is_root=focus_item.id == item_id,
        )

        ioc_rows = db.execute(
            select(ItemIOC, IOC)
            .join(IOC, IOC.id == ItemIOC.ioc_id)
            .where(ItemIOC.item_id == focus_item.id)
            .order_by(ItemIOC.occurrences.desc(), IOC.last_seen_at.desc())
            .limit(ioc_limit)
        ).all()

        selected_ioc_ids: list[uuid.UUID] = []
        for link, ioc in ioc_rows:
            ioc_node_id = f"ioc:{ioc.id}"
            nodes[ioc_node_id] = _build_ioc_graph_node(ioc)
            selected_ioc_ids.append(ioc.id)
            _upsert_graph_edge(
                edges=edges,
                seen=seen_edges,
                source=current_focus_node_id,
                target=ioc_node_id,
                relation="mentions",
                weight=max(1.0, float(link.occurrences)),
            )

        related_item_scores: dict[uuid.UUID, float] = {}
        related_item_latest: dict[uuid.UUID, float] = {}
        related_item_iocs: dict[uuid.UUID, set[uuid.UUID]] = {}
        edge_weights: dict[tuple[uuid.UUID, uuid.UUID], float] = {}

        if selected_ioc_ids:
            related_rows = db.execute(
                select(ItemIOC.item_id, ItemIOC.ioc_id, ItemIOC.occurrences, Item.first_seen_at)
                .join(Item, Item.id == ItemIOC.item_id)
                .where(
                    and_(
                        ItemIOC.ioc_id.in_(selected_ioc_ids),
                        ItemIOC.item_id != focus_item.id,
                        Item.first_seen_at >= cutoff,
                    )
                )
            ).all()

            for related_item_id, ioc_id, occurrences, first_seen_at in related_rows:
                related_item_scores[related_item_id] = related_item_scores.get(related_item_id, 0.0) + float(occurrences) + 1.0
                related_item_iocs.setdefault(related_item_id, set()).add(ioc_id)
                related_item_latest[related_item_id] = max(
                    related_item_latest.get(related_item_id, 0.0),
                    first_seen_at.timestamp() if first_seen_at else 0.0,
                )
                edge_weights[(related_item_id, ioc_id)] = max(
                    edge_weights.get((related_item_id, ioc_id), 0.0),
                    float(occurrences),
                )

        ranked_related_items = sorted(
            related_item_scores.keys(),
            key=lambda candidate_id: (
                related_item_scores.get(candidate_id, 0.0),
                related_item_latest.get(candidate_id, 0.0),
            ),
            reverse=True,
        )[:related_item_limit]

        item_rows = load_item_rows(ranked_related_items)
        for related_item_id in ranked_related_items:
            row = item_rows.get(related_item_id)
            if row is None:
                continue

            node_id = f"item:{related_item_id}"
            nodes[node_id] = _build_item_graph_node(
                item=row.Item,
                feed_name=row.feed_name,
                classification=row.primary_category,
                is_root=related_item_id == item_id,
            )

            for shared_ioc_id in related_item_iocs.get(related_item_id, set()):
                source_node = f"ioc:{shared_ioc_id}"
                if source_node not in nodes:
                    continue
                _upsert_graph_edge(
                    edges=edges,
                    seen=seen_edges,
                    source=source_node,
                    target=node_id,
                    relation="observed_in",
                    weight=max(1.0, edge_weights.get((related_item_id, shared_ioc_id), 1.0)),
                )
    else:
        focus_ioc = db.scalar(select(IOC).where(IOC.id == focus_uuid))
        if focus_ioc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Focus IOC not found")

        current_focus_node_id = f"ioc:{focus_ioc.id}"
        nodes[current_focus_node_id] = _build_ioc_graph_node(focus_ioc)

        item_link_rows = db.execute(
            select(ItemIOC.item_id, ItemIOC.occurrences, Item.first_seen_at)
            .join(Item, Item.id == ItemIOC.item_id)
            .where(and_(ItemIOC.ioc_id == focus_ioc.id, Item.first_seen_at >= cutoff))
            .order_by(Item.first_seen_at.desc())
            .limit(related_item_limit)
        ).all()

        primary_item_ids = [row.item_id for row in item_link_rows]
        item_rows = load_item_rows(primary_item_ids)
        for link in item_link_rows:
            row = item_rows.get(link.item_id)
            if row is None:
                continue
            node_id = f"item:{row.Item.id}"
            nodes[node_id] = _build_item_graph_node(
                item=row.Item,
                feed_name=row.feed_name,
                classification=row.primary_category,
                is_root=row.Item.id == item_id,
            )
            _upsert_graph_edge(
                edges=edges,
                seen=seen_edges,
                source=current_focus_node_id,
                target=node_id,
                relation="observed_in",
                weight=max(1.0, float(link.occurrences)),
            )

        secondary_ioc_scores: dict[uuid.UUID, float] = {}
        secondary_ioc_rows: dict[uuid.UUID, IOC] = {}
        secondary_links: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        if primary_item_ids:
            supporting_rows = db.execute(
                select(ItemIOC.item_id, ItemIOC.occurrences, IOC)
                .join(IOC, IOC.id == ItemIOC.ioc_id)
                .where(and_(ItemIOC.item_id.in_(primary_item_ids), ItemIOC.ioc_id != focus_ioc.id))
                .order_by(ItemIOC.occurrences.desc(), IOC.last_seen_at.desc())
                .limit(ioc_limit * 6)
            ).all()

            for related_item_id, occurrences, related_ioc in supporting_rows:
                secondary_ioc_scores[related_ioc.id] = secondary_ioc_scores.get(related_ioc.id, 0.0) + float(occurrences)
                secondary_ioc_rows[related_ioc.id] = related_ioc
                secondary_links[(related_item_id, related_ioc.id)] = max(
                    secondary_links.get((related_item_id, related_ioc.id), 0.0),
                    float(occurrences),
                )

        selected_secondary_ioc_ids = sorted(
            secondary_ioc_scores.keys(),
            key=lambda candidate_id: secondary_ioc_scores.get(candidate_id, 0.0),
            reverse=True,
        )[:ioc_limit]
        selected_secondary_ioc_set = set(selected_secondary_ioc_ids)

        for related_ioc_id in selected_secondary_ioc_ids:
            related_ioc = secondary_ioc_rows[related_ioc_id]
            node_id = f"ioc:{related_ioc.id}"
            nodes[node_id] = _build_ioc_graph_node(related_ioc)

        for related_item_id in primary_item_ids:
            item_node_id = f"item:{related_item_id}"
            if item_node_id not in nodes:
                continue
            for related_ioc_id in selected_secondary_ioc_set:
                weight = secondary_links.get((related_item_id, related_ioc_id))
                if weight is None:
                    continue
                _upsert_graph_edge(
                    edges=edges,
                    seen=seen_edges,
                    source=item_node_id,
                    target=f"ioc:{related_ioc_id}",
                    relation="mentions",
                    weight=max(1.0, weight),
                )

    if root_item_node_id not in nodes and item_id == base_row.Item.id and not focus_node_id:
        nodes[root_item_node_id] = _build_item_graph_node(
            item=base_row.Item,
            feed_name=base_row.feed_name,
            classification=base_row.primary_category,
            is_root=True,
        )

    return ItemGraphResponse(
        nodes=list(nodes.values()),
        edges=edges,
        focus_node_id=current_focus_node_id,
        root_item_id=str(item_id),
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

    state = _get_or_create_state(db, user.id, item_id)
    previous_is_read = state.is_read
    state.is_read = payload.is_read
    db.add(state)
    if previous_is_read != payload.is_read:
        existing_tag_names, _ = _load_tags_for_items(db, item_ids=[item_id])
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

    state = _get_or_create_state(db, user.id, item_id)
    previous_is_starred = state.is_starred
    state.is_starred = payload.is_starred
    db.add(state)
    if previous_is_starred != payload.is_starred:
        existing_tag_names, _ = _load_tags_for_items(db, item_ids=[item_id])
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
    tags_by_item, _ = _load_tags_for_items(db, item_ids=[item_id])

    suggestions = _load_item_tag_suggestions(
        db,
        item=item,
        classification=classification,
        article=article,
        feed=feed,
        existing_tag_names=tags_by_item.get(item_id, []),
    )
    return ItemTagSuggestionListResponse(item_id=item_id, suggestions=suggestions)
