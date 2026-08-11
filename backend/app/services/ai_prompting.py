from __future__ import annotations

import json
from datetime import datetime

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.services.ai_config import (
    ActiveAISettings,
    build_daily_brief_system_prompt,
    build_item_enrichment_system_prompt,
)
from app.services.ai_normalization import truncate_text
from app.services.url_utils import normalize_url


MAX_ITEM_ARTICLE_PROMPT_CHARS = 8_000
MAX_ITEM_SUMMARY_CHARS = 2_000
MAX_BRIEF_ITEM_SUMMARY_CHARS = 900


def build_item_enrichment_messages(
    active: ActiveAISettings,
    *,
    item: Item,
    article: Article,
    classification: ItemClassification | None,
    feed: Feed | None,
    tag_names: list[str],
) -> list[dict[str, str]]:
    feature_instructions: dict[str, str] = {}
    if active.summary_enabled:
        feature_instructions["summary_text"] = (
            "2-4 concise sentences explaining the article and its practical defensive significance. Use only the provided input."
        )
    if active.relevance_enabled:
        feature_instructions["relevance_score"] = (
            "A number between 0 and 1 describing how relevant the item is to the company profile. "
            "Score conservatively when the match is generic, indirect, speculative, or contradicted by exclusions."
        )
        feature_instructions["relevance_reasons"] = (
            "1-4 short plain strings citing concrete matches, mismatches, exclusions, or evidence gaps from company_context and item content."
        )

    return [
        {"role": "system", "content": build_item_enrichment_system_prompt(active)},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "item_enrichment",
                    "audience": "security lead or analyst triaging content for one defended organization",
                    "requested_output": feature_instructions,
                    "company_context": build_company_context(active),
                    "item": {
                        "title": item.title,
                        "summary": truncate_text(item.summary, MAX_ITEM_SUMMARY_CHARS),
                        "article_text": truncate_text(article.text, MAX_ITEM_ARTICLE_PROMPT_CHARS),
                        "feed_name": feed.name if feed is not None else None,
                        "url": normalize_url(item.canonical_url or item.url) or None,
                        "published_at": item.published_at.isoformat() if item.published_at else None,
                        "classification": {
                            "primary_category": classification.primary_category if classification is not None else None,
                            "secondary_categories": classification.secondary_categories if classification is not None else [],
                            "confidence": float(classification.confidence) if classification is not None else None,
                        },
                        "tags": tag_names[:10],
                    },
                }
            ),
        },
    ]


def build_daily_brief_messages(
    active: ActiveAISettings,
    *,
    item_rows: list[object],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, str]]:
    compact_items = [
        {
            "id": str(row.id),
            "title": row.title,
            "feed_name": row.feed_name,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "classification": row.primary_category,
            "summary": truncate_text(row.ai_summary or row.summary, MAX_BRIEF_ITEM_SUMMARY_CHARS),
            "relevance_score": float(row.relevance_score) if row.relevance_score is not None else None,
            "relevance_label": row.relevance_label,
            "url": normalize_url(getattr(row, "url", None)) or None,
        }
        for row in item_rows
    ]

    return [
        {"role": "system", "content": build_daily_brief_system_prompt(active)},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "daily_brief",
                    "audience": "security leads and analysts preparing a daily triage and prioritization handoff",
                    "requested_output": {
                        "title": "A short title capturing the most important theme from the selected items.",
                        "brief_text": "A concise overview explaining what matters most to the organization and why.",
                        "key_points": "4-6 short factual strings prioritizing the most relevant developments without duplicating the same story.",
                        "recommended_actions": "3-5 short, practical, evidence-based strings for validation, monitoring, or response.",
                    },
                    "briefing_priorities": [
                        "Direct relevance to the company profile, stack, vendors, regions, keywords, and priority topics",
                        "Clear defensive significance, active risk, or near-term actionability",
                        "Synthesis of overlapping stories so repeated coverage does not dominate the brief",
                    ],
                    "company_context": build_company_context(active),
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "items": compact_items,
                }
            ),
        },
    ]


def build_company_context(active: ActiveAISettings) -> dict[str, object]:
    return {
        "company_name": active.company_name,
        "industry": active.company_industry,
        "regions": active.company_regions,
        "technology_stack": active.company_stack,
        "priority_topics": active.company_priority_topics,
        "keywords": active.company_keywords,
        "exclusions": active.company_exclusions,
        "profile_text": active.company_profile_text,
    }
