import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, and_, cast, false, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.item_state import ItemState
from app.models.tag import ItemTag
from app.schemas.exports import ArticleExportFilters, ArticleExportPreviewItem
from app.services.export_models import (
    ExportAIInsight,
    ExportArticleContent,
    ExportClassification,
    ExportIOC,
    ExportRecord,
    ExportTag,
    ExportUserState,
)
from app.services.item_views import load_tags_for_items
from app.services.url_utils import normalize_url

EXPORT_RECORD_BATCH_SIZE = 200


class ExportSnapshotChangedError(RuntimeError):
    """Raised when an item disappears after the export snapshot is selected."""


@dataclass(frozen=True)
class ExportCounts:
    total: int
    with_article_text: int
    with_iocs: int


@dataclass(frozen=True)
class ExportQueryContext:
    state_subquery: Any
    clauses: tuple[ColumnElement[bool], ...]
    order_by: tuple[Any, ...]


def build_export_query_context(
    *,
    user_id: uuid.UUID | None,
    filters: ArticleExportFilters,
) -> ExportQueryContext:
    state_subquery = (
        select(
            ItemState.item_id.label("item_id"),
            ItemState.is_read.label("is_read"),
            ItemState.is_starred.label("is_starred"),
            ItemState.note.label("note"),
            ItemState.updated_at.label("state_updated_at"),
        )
        .where(ItemState.user_id == user_id if user_id is not None else false())
        .subquery()
    )
    clauses: list[ColumnElement[bool]] = []

    if filters.q:
        pattern = f"%{_escape_like_value(filters.q.lower())}%"
        clauses.append(
            or_(
                func.lower(Item.title).like(pattern, escape="\\"),
                func.lower(func.coalesce(Item.summary, "")).like(pattern, escape="\\"),
                func.lower(cast(Item.url, String)).like(pattern, escape="\\"),
            )
        )
    if filters.feed_ids:
        clauses.append(Item.feed_id.in_(filters.feed_ids))
    if filters.tag_ids:
        if filters.tags_mode == "all":
            for tag_id in filters.tag_ids:
                clauses.append(
                    select(ItemTag.item_id)
                    .where(and_(ItemTag.item_id == Item.id, ItemTag.tag_id == tag_id))
                    .exists()
                )
        else:
            clauses.append(
                select(ItemTag.item_id)
                .where(
                    and_(
                        ItemTag.item_id == Item.id, ItemTag.tag_id.in_(filters.tag_ids)
                    )
                )
                .exists()
            )
    if filters.classifications:
        clauses.append(
            func.lower(ItemClassification.primary_category).in_(filters.classifications)
        )
    if filters.ai_relevance_labels:
        clauses.append(
            ItemAIEnrichment.relevance_label.in_(filters.ai_relevance_labels)
        )
    if filters.ai_score_min is not None:
        clauses.append(ItemAIEnrichment.relevance_score >= filters.ai_score_min)
    if filters.ai_score_max is not None:
        clauses.append(ItemAIEnrichment.relevance_score <= filters.ai_score_max)
    if filters.is_read is not None:
        clauses.append(
            func.coalesce(state_subquery.c.is_read, False) == filters.is_read
        )
    if filters.is_starred is not None:
        clauses.append(
            func.coalesce(state_subquery.c.is_starred, False) == filters.is_starred
        )

    article_has_text = _article_has_text_clause()
    if filters.has_article_text is not None:
        clauses.append(
            article_has_text if filters.has_article_text else ~article_has_text
        )

    timeline_at = Item.first_seen_at
    if filters.date_basis == "published_at_or_first_seen_at":
        timeline_at = func.coalesce(Item.published_at, Item.first_seen_at)
    if filters.since:
        clauses.append(timeline_at >= filters.since)
    if filters.until:
        clauses.append(timeline_at <= filters.until)

    order_clauses = {
        "published_at_desc": (
            Item.published_at.desc().nullslast(),
            Item.first_seen_at.desc(),
            Item.id.desc(),
        ),
        "published_at_asc": (
            Item.published_at.asc().nullsfirst(),
            Item.first_seen_at.asc(),
            Item.id.asc(),
        ),
        "first_seen_desc": (Item.first_seen_at.desc(), Item.id.desc()),
        "first_seen_asc": (Item.first_seen_at.asc(), Item.id.asc()),
    }
    return ExportQueryContext(
        state_subquery=state_subquery,
        clauses=tuple(clauses),
        order_by=order_clauses[filters.sort],
    )


def load_export_counts(db: Session, *, context: ExportQueryContext) -> ExportCounts:
    return ExportCounts(
        total=_count_filtered_items(db, context=context),
        with_article_text=_count_filtered_items(
            db, context=context, additional_clause=_article_has_text_clause()
        ),
        with_iocs=_count_filtered_items(
            db,
            context=context,
            additional_clause=select(ItemIOC.item_id)
            .where(ItemIOC.item_id == Item.id)
            .exists(),
        ),
    )


def load_export_item_ids(
    db: Session,
    *,
    context: ExportQueryContext,
    limit: int,
) -> list[uuid.UUID]:
    statement = (
        _base_item_query(context, Item.id).order_by(*context.order_by).limit(limit)
    )
    return list(db.scalars(statement).all())


def iter_export_records(
    db: Session,
    *,
    item_ids: Sequence[uuid.UUID],
    context: ExportQueryContext,
    include_iocs: bool,
    batch_size: int = EXPORT_RECORD_BATCH_SIZE,
) -> Iterator[ExportRecord]:
    for start in range(0, len(item_ids), batch_size):
        chunk = list(item_ids[start : start + batch_size])
        records_by_id = _load_export_record_batch(
            db,
            item_ids=chunk,
            context=context,
            include_iocs=include_iocs,
        )
        for item_id in chunk:
            record = records_by_id.get(item_id)
            if record is None:
                raise ExportSnapshotChangedError(
                    f"Export item {item_id} changed while the export was generated"
                )
            yield record


def build_preview_items(
    records: Sequence[ExportRecord],
    *,
    personal_state_available: bool = True,
) -> list[ArticleExportPreviewItem]:
    return [
        ArticleExportPreviewItem(
            id=record.id,
            title=record.title,
            url=record.url,
            feed_name=record.feed_name,
            published_at=record.published_at,
            first_seen_at=record.first_seen_at,
            classification=record.classification.primary_category
            if record.classification
            else None,
            ai_relevance_score=record.ai.relevance_score if record.ai else None,
            ai_relevance_label=record.ai.relevance_label if record.ai else None,
            tags=[tag.name for tag in record.tags],
            is_read=record.state.is_read,
            is_starred=record.state.is_starred,
            personal_state_available=personal_state_available,
            has_article_text=bool(
                record.article and record.article.text and record.article.text.strip()
            ),
            ioc_count=len(record.iocs),
        )
        for record in records
    ]


def _base_item_query(context: ExportQueryContext, *columns: Any):
    statement = (
        select(*columns)
        .select_from(Item)
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .outerjoin(context.state_subquery, context.state_subquery.c.item_id == Item.id)
    )
    if context.clauses:
        statement = statement.where(and_(*context.clauses))
    return statement


def _count_filtered_items(
    db: Session,
    *,
    context: ExportQueryContext,
    additional_clause: ColumnElement[bool] | None = None,
) -> int:
    statement = _base_item_query(context, Item.id)
    if additional_clause is not None:
        statement = statement.where(additional_clause)
    return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _load_export_record_batch(
    db: Session,
    *,
    item_ids: list[uuid.UUID],
    context: ExportQueryContext,
    include_iocs: bool,
) -> dict[uuid.UUID, ExportRecord]:
    if not item_ids:
        return {}

    rows = db.execute(
        select(
            Item,
            Feed.name.label("feed_name"),
            Article,
            ItemClassification,
            ItemAIEnrichment,
            func.coalesce(context.state_subquery.c.is_read, False).label("is_read"),
            func.coalesce(context.state_subquery.c.is_starred, False).label(
                "is_starred"
            ),
            context.state_subquery.c.note,
            context.state_subquery.c.state_updated_at,
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .outerjoin(context.state_subquery, context.state_subquery.c.item_id == Item.id)
        .where(Item.id.in_(item_ids))
    ).all()

    _, tag_details_by_item = load_tags_for_items(db, item_ids=item_ids)
    iocs_by_item = _load_iocs_for_items(db, item_ids=item_ids) if include_iocs else {}
    records: dict[uuid.UUID, ExportRecord] = {}
    for row in rows:
        item = row.Item
        classification = row.ItemClassification
        enrichment = row.ItemAIEnrichment
        article = row.Article
        records[item.id] = ExportRecord(
            id=item.id,
            feed_id=item.feed_id,
            feed_name=row.feed_name,
            source_guid=item.source_guid,
            url=normalize_url(item.url),
            canonical_url=normalize_url(item.canonical_url) or None,
            title=item.title,
            summary=item.summary,
            published_at=item.published_at,
            first_seen_at=item.first_seen_at,
            status=item.status,
            classification=_serialize_classification(classification),
            ai=_serialize_ai(enrichment),
            article=_serialize_article(article),
            state=ExportUserState(
                is_read=bool(row.is_read),
                is_starred=bool(row.is_starred),
                note=row.note,
                updated_at=row.state_updated_at,
            ),
            tags=[
                ExportTag(
                    id=detail.id,
                    name=detail.name,
                    source=detail.source,
                    confidence=detail.confidence,
                    rules_version=detail.rules_version,
                )
                for detail in tag_details_by_item.get(item.id, [])
            ],
            iocs=iocs_by_item.get(item.id, []),
        )
    return records


def _load_iocs_for_items(
    db: Session,
    *,
    item_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[ExportIOC]]:
    by_item: dict[uuid.UUID, list[ExportIOC]] = {item_id: [] for item_id in item_ids}
    rows = db.execute(
        select(ItemIOC, IOC)
        .join(IOC, IOC.id == ItemIOC.ioc_id)
        .where(ItemIOC.item_id.in_(item_ids))
        .order_by(ItemIOC.item_id.asc(), IOC.type.asc(), IOC.value_norm.asc())
    ).all()
    for link, ioc in rows:
        by_item[link.item_id].append(
            ExportIOC(
                id=ioc.id,
                type=ioc.type,
                value=ioc.value_norm,
                source_section=link.source_section,
                occurrences=link.occurrences,
                confidence=float(link.confidence),
                first_seen_at=ioc.first_seen_at,
                last_seen_at=ioc.last_seen_at,
            )
        )
    return by_item


def _serialize_classification(
    value: ItemClassification | None,
) -> ExportClassification | None:
    if value is None:
        return None
    return ExportClassification(
        primary_category=value.primary_category,
        secondary_categories=list(value.secondary_categories or []),
        confidence=float(value.confidence),
        scores=dict(value.scores_json or {}),
        matched_terms=dict(value.matched_terms_json or {}),
        rules_version=value.rules_version,
        classified_at=value.classified_at,
    )


def _serialize_ai(value: ItemAIEnrichment | None) -> ExportAIInsight | None:
    if value is None:
        return None
    return ExportAIInsight(
        status=value.status,
        summary=value.summary_text,
        relevance_score=float(value.relevance_score)
        if value.relevance_score is not None
        else None,
        relevance_label=value.relevance_label,
        relevance_reasons=list(value.relevance_reasons_json or []),
        provider=value.provider,
        model=value.model,
        generated_at=value.generated_at,
        error=value.error,
    )


def _serialize_article(value: Article | None) -> ExportArticleContent | None:
    if value is None:
        return None
    return ExportArticleContent(
        final_url=normalize_url(value.final_url),
        retrieved_at=value.retrieved_at,
        http_status=value.http_status,
        content_type=value.content_type,
        title=value.title_extracted,
        text=value.text,
        extraction_method=value.extraction_method,
        language=value.language,
        word_count=value.word_count,
        error=value.error,
    )


def _article_has_text_clause() -> ColumnElement[bool]:
    return func.nullif(func.btrim(Article.text), "").is_not(None)


def _escape_like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
