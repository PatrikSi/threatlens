import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.schemas.item import (
    ItemGraphEdgeResponse,
    ItemGraphNodeResponse,
    ItemGraphResponse,
    ItemTagDetailResponse,
    ItemTagSuggestionResponse,
)
from app.services.algorithm_tags import build_tag_candidates
from app.services.tag_feedback import load_feedback_adjustments

SUGGESTION_CONFIDENCE_MIN = 0.25
SUGGESTION_LIMIT = 12


@dataclass(frozen=True)
class ItemGraphRow:
    item: Item
    feed_name: str
    primary_category: str | None


def load_tags_for_items(
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


def load_item_tag_suggestions(
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


def build_item_graph(
    db: Session,
    *,
    item_id: uuid.UUID,
    focus_node_id: str | None,
    related_item_limit: int,
    ioc_limit: int,
    since_days: int,
) -> ItemGraphResponse:
    base_row = _load_item_graph_row(db, item_id=item_id)
    if base_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    focus_kind = "item"
    focus_uuid = item_id
    if focus_node_id:
        focus_kind, focus_uuid = _parse_graph_node_id(focus_node_id)

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    root_item_node_id = f"item:{item_id}"
    current_focus_node_id: str
    nodes: dict[str, ItemGraphNodeResponse] = {}
    edges: list[ItemGraphEdgeResponse] = []
    seen_edges: set[tuple[str, str, str]] = set()

    if focus_kind == "item":
        focus_row = base_row if focus_uuid == item_id else _load_item_graph_row(db, item_id=focus_uuid)
        if focus_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Focus item not found")

        focus_item = focus_row.item
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

        item_rows = _load_item_graph_rows(db, item_ids=ranked_related_items)
        for related_item_id in ranked_related_items:
            row = item_rows.get(related_item_id)
            if row is None:
                continue

            node_id = f"item:{related_item_id}"
            nodes[node_id] = _build_item_graph_node(
                item=row.item,
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
        item_rows = _load_item_graph_rows(db, item_ids=primary_item_ids)
        for link in item_link_rows:
            row = item_rows.get(link.item_id)
            if row is None:
                continue
            node_id = f"item:{row.item.id}"
            nodes[node_id] = _build_item_graph_node(
                item=row.item,
                feed_name=row.feed_name,
                classification=row.primary_category,
                is_root=row.item.id == item_id,
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

    if root_item_node_id not in nodes and item_id == base_row.item.id and not focus_node_id:
        nodes[root_item_node_id] = _build_item_graph_node(
            item=base_row.item,
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


def _load_item_graph_row(db: Session, *, item_id: uuid.UUID) -> ItemGraphRow | None:
    row = db.execute(
        select(
            Item,
            Feed.name.label("feed_name"),
            ItemClassification.primary_category.label("primary_category"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == item_id)
    ).first()
    if row is None:
        return None
    return ItemGraphRow(item=row.Item, feed_name=row.feed_name, primary_category=row.primary_category)


def _load_item_graph_rows(db: Session, *, item_ids: list[uuid.UUID]) -> dict[uuid.UUID, ItemGraphRow]:
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
    return {
        row.Item.id: ItemGraphRow(item=row.Item, feed_name=row.feed_name, primary_category=row.primary_category)
        for row in rows
    }


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
) -> None:
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
