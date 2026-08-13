from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.services.ai_config import ActiveAISettings


def record_usage_event(
    db: Session,
    *,
    feature_type: str,
    success: bool,
    provider: str | None,
    model: str | None,
    item_id: uuid.UUID | None = None,
    daily_brief_id: uuid.UUID | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    db.add(
        AIUsageEvent(
            feature_type=feature_type,
            success=success,
            provider=provider,
            model=model,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            error=error,
        )
    )


def replace_daily_brief_source_items(
    db: Session,
    *,
    brief: AIDailyBrief,
    item_rows_all: list[object],
    selected_item_ids: set[str],
) -> None:
    db.execute(delete(AIDailyBriefSourceItem).where(AIDailyBriefSourceItem.daily_brief_id == brief.id))
    for index, row in enumerate(item_rows_all, start=1):
        included = str(row.id) in selected_item_ids
        db.add(
            AIDailyBriefSourceItem(
                daily_brief_id=brief.id,
                item_id=row.id,
                included=included,
                rank=index,
                exclusion_reason=None if included else "brief_item_cap",
                title_snapshot=row.title,
                feed_name_snapshot=row.feed_name,
                url_snapshot=row.url,
                classification_snapshot=row.primary_category,
                relevance_score_snapshot=float(row.relevance_score) if row.relevance_score is not None else None,
                relevance_label_snapshot=row.relevance_label,
                published_at_snapshot=row.published_at,
                first_seen_at_snapshot=row.first_seen_at,
            )
        )


def load_item_tag_names(db: Session, *, item_id: uuid.UUID) -> list[str]:
    rows = db.execute(
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == item_id)
        .order_by(Tag.name.asc())
    ).all()
    return [name for (name,) in rows]


def compute_item_source_hash(
    active: ActiveAISettings,
    *,
    item: Item,
    article: Article,
    classification: ItemClassification | None,
    tag_names: list[str],
    feed_name: str,
) -> str:
    payload = json.dumps(
        {
            "settings": {
                "model": active.model,
                "summary_enabled": active.summary_enabled,
                "relevance_enabled": active.relevance_enabled,
                "company_name": active.company_name,
                "company_industry": active.company_industry,
                "company_regions": active.company_regions,
                "company_stack": active.company_stack,
                "company_priority_topics": active.company_priority_topics,
                "company_keywords": active.company_keywords,
                "company_exclusions": active.company_exclusions,
                "company_profile_text": active.company_profile_text,
                "global_instructions": active.global_instructions,
                "item_summary_instructions": active.item_summary_instructions,
                "relevance_instructions": active.relevance_instructions,
            },
            "item": {
                "title": item.title,
                "summary": item.summary,
                "article_text": article.text,
                "feed_name": feed_name,
                "classification": {
                    "primary_category": classification.primary_category if classification is not None else None,
                    "secondary_categories": classification.secondary_categories if classification is not None else [],
                    "confidence": float(classification.confidence) if classification is not None else None,
                },
                "tags": tag_names,
            },
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()
