"""Bounded briefing source projection; audit-only rows never load summary bodies."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.db.text_projection import stripped_text
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.services.ai_enrichment_provenance import (
    STALE_ENRICHMENT_WARNING,
    current_enrichment_predicate,
)
from app.services.ai_prompting import MAX_BRIEF_ITEM_SUMMARY_CHARS
from app.services.ai_config import ActiveAISettings


MAX_AUDIT_ROWS = 2000
MAX_SOURCE_BYTES = 40 * 1024 * 1024
TITLE_CHARS, FEED_CHARS, URL_CHARS = 512, 255, 4096
# PostgreSQL UTF-8 is at most four bytes per character. Bound every projected
# text field and derive the row allowance before executing/materializing SQL.
METADATA_ROW_BYTES = 4 * (TITLE_CHARS + FEED_CHARS + URL_CHARS + 64 + 16) + 256


@dataclass(frozen=True)
class BriefSourceSelection:
    audit_rows: list[Row[Any]]
    selected_rows: list[Row[Any]]
    warnings: list[str]


def load_brief_sources(
    db: Session,
    *,
    active: ActiveAISettings,
    window_start: datetime,
    window_end: datetime,
    total_items: int,
    audit_limit: int,
) -> BriefSourceSelection:
    selected_limit = max(1, min(100, active.daily_brief_max_items))
    text_reserve = selected_limit * 2 * MAX_BRIEF_ITEM_SUMMARY_CHARS * 4
    budget_rows = (MAX_SOURCE_BYTES - text_reserve) // METADATA_ROW_BYTES
    row_limit = min(
        MAX_AUDIT_ROWS, budget_rows, total_items, max(selected_limit, audit_limit)
    )
    current = current_enrichment_predicate()
    score = case((current, ItemAIEnrichment.relevance_score))
    label = case((current, ItemAIEnrichment.relevance_label))
    timeline = func.coalesce(Item.published_at, Item.first_seen_at)
    ordering = (score.desc().nullslast(), timeline.desc(), Item.id)
    candidates = (
        select(
            Item.id.label("item_id"),
            func.row_number().over(order_by=ordering).label("position"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .where(timeline >= window_start, timeline <= window_end)
        .order_by(*ordering)
        .limit(row_limit)
        .cte("brief_candidates")
    )
    selected = candidates.c.position <= selected_limit
    primary = func.coalesce(func.nullif(stripped_text(Item.summary), ""), Article.text)
    rows = db.execute(
        select(
            Item.id,
            func.substr(Item.title, 1, TITLE_CHARS).label("title"),
            case(
                (selected, func.substr(primary, 1, MAX_BRIEF_ITEM_SUMMARY_CHARS))
            ).label("summary"),
            case((func.char_length(Item.url) <= URL_CHARS, Item.url)).label("url"),
            Item.published_at,
            Item.first_seen_at,
            func.substr(Feed.name, 1, FEED_CHARS).label("feed_name"),
            ItemClassification.primary_category.label("primary_category"),
            case(
                (
                    selected & current,
                    func.substr(
                        ItemAIEnrichment.summary_text, 1, MAX_BRIEF_ITEM_SUMMARY_CHARS
                    ),
                )
            ).label("ai_summary"),
            score.label("relevance_score"),
            label.label("relevance_label"),
            case(
                (ItemAIEnrichment.item_id.is_(None), False),
                (current, False),
                else_=True,
            ).label("enrichment_fallback"),
        )
        .join(candidates, candidates.c.item_id == Item.id)
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .order_by(candidates.c.position)
        .with_for_update(read=True, of=(Item, Feed))
    ).all()
    selected_rows = rows[:selected_limit]
    warnings = (
        [STALE_ENRICHMENT_WARNING]
        if any(row.enrichment_fallback for row in selected_rows)
        else []
    )
    if row_limit < min(total_items, max(selected_limit, audit_limit)):
        warnings.append(
            "Source audit history was limited by the briefing source byte budget."
        )
    return BriefSourceSelection(list(rows), list(selected_rows), warnings)
