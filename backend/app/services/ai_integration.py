from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.schemas.ai import (
    AIDailyBriefItemResponse,
    AIDailyBriefResponse,
    AITestConnectionResponse,
    AIUsageFeatureSummary,
    AIUsageSummaryResponse,
)
from app.services.ai_config import (
    ActiveAISettings,
    build_daily_brief_system_prompt,
    build_item_enrichment_system_prompt,
    load_active_ai_settings,
)

FEATURE_ITEM_ENRICHMENT = "item_enrichment"
FEATURE_DAILY_BRIEF = "daily_brief"
FEATURE_CONNECTION_TEST = "connection_test"

MAX_ITEM_ARTICLE_PROMPT_CHARS = 8_000
MAX_ITEM_SUMMARY_CHARS = 2_000
MAX_BRIEF_ITEM_SUMMARY_CHARS = 900


class AIIntegrationError(ValueError):
    pass


@dataclass(frozen=True)
class AICompletionResult:
    payload: dict[str, object]
    provider: str
    model: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    prompt_char_count: int = 0
    response_char_count: int = 0


@dataclass(frozen=True)
class AIItemEnrichmentResult:
    enrichment: ItemAIEnrichment | None
    status: str
    reason: str | None
    input_text_chars: int
    prompt_char_count: int | None = None
    response_char_count: int | None = None


@dataclass(frozen=True)
class AIDailyBriefGenerationResult:
    brief: AIDailyBrief | None
    status: str
    reason: str | None
    items_considered: int
    items_selected: int
    prompt_char_count: int | None = None
    response_char_count: int | None = None


def test_ai_connection(db: Session) -> AITestConnectionResponse:
    active = load_active_ai_settings(db)
    if not active.ai_enabled:
        raise AIIntegrationError("AI features are disabled")
    if not active.ai_configured:
        raise AIIntegrationError("Configure the AI base URL and model before testing the connection")

    try:
        completion = _request_json_with_usage(
            db,
            active,
            feature_type=FEATURE_CONNECTION_TEST,
            messages=[
                {
                    "role": "system",
                    "content": "Return only JSON. Do not include markdown code fences.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "connection_test",
                            "instructions": "Return {\"ok\": true, \"message\": \"ready\"}.",
                        }
                    ),
                },
            ],
        )
    except AIIntegrationError as exc:
        return AITestConnectionResponse(
            success=False,
            latency_ms=None,
            provider="openai_compatible",
            model=active.model,
            error=str(exc),
        )

    return AITestConnectionResponse(
        success=bool(completion.payload.get("ok") is True),
        latency_ms=completion.latency_ms,
        provider="openai_compatible",
        model=completion.model,
        error=None if completion.payload.get("ok") is True else "Unexpected response from AI endpoint",
    )


def generate_item_ai_enrichment(db: Session, *, item_id: uuid.UUID, force: bool = False) -> ItemAIEnrichment | None:
    return run_item_ai_enrichment(db, item_id=item_id, force=force).enrichment


def run_item_ai_enrichment(
    db: Session,
    *,
    item_id: uuid.UUID,
    force: bool = False,
) -> AIItemEnrichmentResult:
    active = load_active_ai_settings(db)
    if not active.ai_enabled or not active.ai_configured:
        return AIItemEnrichmentResult(enrichment=None, status="skipped", reason="ai_not_configured" if active.ai_enabled else "ai_disabled", input_text_chars=0)
    if not active.summary_enabled and not active.relevance_enabled:
        return AIItemEnrichmentResult(enrichment=None, status="skipped", reason="feature_disabled", input_text_chars=0)

    item = db.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        return AIItemEnrichmentResult(enrichment=None, status="skipped", reason="item_not_found", input_text_chars=0)

    article = db.scalar(select(Article).where(Article.item_id == item_id))
    if article is None or not (article.text or "").strip():
        return AIItemEnrichmentResult(
            enrichment=None,
            status="skipped",
            reason="no_article" if article is None else "no_article_text",
            input_text_chars=len((article.text or "")) if article is not None else 0,
        )

    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
    classification = db.scalar(select(ItemClassification).where(ItemClassification.item_id == item_id))
    enrichment = _ensure_item_ai_enrichment_row(db, item_id=item_id)
    tag_names = _load_item_tag_names(db, item_id=item_id)
    source_hash = _compute_item_source_hash(
        active,
        item=item,
        article=article,
        classification=classification,
        tag_names=tag_names,
        feed_name=feed.name if feed is not None else "",
    )

    if enrichment is not None and enrichment.source_hash == source_hash:
        if enrichment.status == "ready" and not force:
            return AIItemEnrichmentResult(
                enrichment=enrichment,
                status="skipped",
                reason="source_hash_unchanged",
                input_text_chars=len(article.text or ""),
            )
        if enrichment.status == "pending" and not force:
            return AIItemEnrichmentResult(
                enrichment=enrichment,
                status="skipped",
                reason="already_pending",
                input_text_chars=len(article.text or ""),
            )

    now = datetime.now(timezone.utc)
    db.execute(
        update(ItemAIEnrichment)
        .where(ItemAIEnrichment.item_id == item_id)
        .values(
            status="pending",
            source_hash=source_hash,
            error=None,
            provider=active.provider_type,
            model=active.model,
        )
    )
    db.flush()
    db.refresh(enrichment)

    try:
        completion = _request_json_with_usage(
            db,
            active,
            feature_type=FEATURE_ITEM_ENRICHMENT,
            item_id=item_id,
            messages=_build_item_enrichment_messages(
                active,
                item=item,
                article=article,
                classification=classification,
                feed=feed,
                tag_names=tag_names,
            ),
        )
    except AIIntegrationError as exc:
        enrichment.status = "error"
        enrichment.error = str(exc)
        enrichment.generated_at = now
        return AIItemEnrichmentResult(
            enrichment=enrichment,
            status="error",
            reason="request_failed",
            input_text_chars=len(article.text or ""),
        )

    summary_text = _normalize_optional_text(completion.payload.get("summary_text")) if active.summary_enabled else None
    relevance_score = _coerce_score(completion.payload.get("relevance_score")) if active.relevance_enabled else None
    relevance_label = _score_to_label(relevance_score, active) if relevance_score is not None else None
    relevance_reasons = _normalize_string_list(completion.payload.get("relevance_reasons")) if active.relevance_enabled else []

    enrichment.status = "ready"
    enrichment.summary_text = summary_text
    enrichment.relevance_score = relevance_score
    enrichment.relevance_label = relevance_label
    enrichment.relevance_reasons_json = relevance_reasons[:4]
    enrichment.provider = completion.provider
    enrichment.model = completion.model
    enrichment.prompt_tokens = completion.prompt_tokens
    enrichment.completion_tokens = completion.completion_tokens
    enrichment.total_tokens = completion.total_tokens
    enrichment.latency_ms = completion.latency_ms
    enrichment.error = None
    enrichment.generated_at = now
    return AIItemEnrichmentResult(
        enrichment=enrichment,
        status="ready",
        reason=None,
        input_text_chars=len(article.text or ""),
        prompt_char_count=completion.prompt_char_count,
        response_char_count=completion.response_char_count,
    )


def _ensure_item_ai_enrichment_row(db: Session, *, item_id: uuid.UUID) -> ItemAIEnrichment:
    db.execute(
        pg_insert(ItemAIEnrichment)
        .values(
            item_id=item_id,
            status="pending",
            source_hash="",
            relevance_reasons_json=[],
        )
        .on_conflict_do_nothing(index_elements=[ItemAIEnrichment.item_id])
    )
    enrichment = db.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item_id))
    if enrichment is None:
        raise AIIntegrationError("Failed to initialize AI enrichment state")
    return enrichment


def generate_daily_brief(
    db: Session,
    *,
    force: bool = False,
    reference_time: datetime | None = None,
) -> AIDailyBrief | None:
    return run_daily_brief_generation(db, force=force, reference_time=reference_time).brief


def run_daily_brief_generation(
    db: Session,
    *,
    force: bool = False,
    reference_time: datetime | None = None,
) -> AIDailyBriefGenerationResult:
    active = load_active_ai_settings(db)
    if not active.ai_enabled or not active.ai_configured or not active.daily_brief_enabled:
        if not active.ai_enabled:
            return AIDailyBriefGenerationResult(brief=None, status="skipped", reason="ai_disabled", items_considered=0, items_selected=0)
        if not active.ai_configured:
            return AIDailyBriefGenerationResult(brief=None, status="skipped", reason="ai_not_configured", items_considered=0, items_selected=0)
        return AIDailyBriefGenerationResult(brief=None, status="skipped", reason="feature_disabled", items_considered=0, items_selected=0)

    now = reference_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    brief_date = now.date()

    existing = db.scalar(select(AIDailyBrief).where(AIDailyBrief.brief_date == brief_date))
    if existing is not None and existing.status == "ready" and not force:
        prune_daily_brief_history(db, keep_limit=active.daily_brief_history_limit)
        return AIDailyBriefGenerationResult(
            brief=existing,
            status="skipped",
            reason="already_generated",
            items_considered=int(existing.item_count or 0),
            items_selected=len(existing.top_item_ids_json or []),
        )

    window_end = now
    window_start = now - timedelta(hours=active.daily_brief_window_hours)
    total_items = db.scalar(
        select(func.count(Item.id)).where(Item.first_seen_at >= window_start, Item.first_seen_at <= window_end)
    ) or 0
    if total_items <= 0:
        return existing if existing is not None and existing.status == "ready" else None

    item_rows_all = db.execute(
        select(
            Item.id,
            Item.title,
            Item.summary,
            Item.url,
            Item.published_at,
            Item.first_seen_at,
            Feed.name.label("feed_name"),
            ItemClassification.primary_category.label("primary_category"),
            ItemAIEnrichment.summary_text.label("ai_summary"),
            ItemAIEnrichment.relevance_score.label("relevance_score"),
            ItemAIEnrichment.relevance_label.label("relevance_label"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .where(Item.first_seen_at >= window_start, Item.first_seen_at <= window_end)
        .order_by(ItemAIEnrichment.relevance_score.desc().nullslast(), Item.first_seen_at.desc())
    ).all()
    item_rows = item_rows_all[: active.daily_brief_max_items]
    if not item_rows:
        return AIDailyBriefGenerationResult(
            brief=existing if existing is not None and existing.status == "ready" else None,
            status="skipped",
            reason="no_items",
            items_considered=int(total_items),
            items_selected=0,
        )

    brief = existing or AIDailyBrief(brief_date=brief_date, window_start=window_start, window_end=window_end)
    brief.status = "pending"
    brief.window_start = window_start
    brief.window_end = window_end
    brief.item_count = int(total_items)
    brief.error = None
    brief.provider = active.provider_type
    brief.model = active.model
    db.add(brief)
    db.flush()

    try:
        completion = _request_json_with_usage(
            db,
            active,
            feature_type=FEATURE_DAILY_BRIEF,
            daily_brief_id=brief.id,
            messages=_build_daily_brief_messages(active, item_rows=item_rows, window_start=window_start, window_end=window_end),
        )
    except AIIntegrationError as exc:
        brief.status = "error"
        brief.error = str(exc)
        brief.generated_at = now
        db.add(brief)
        _replace_daily_brief_source_items(db, brief=brief, item_rows_all=item_rows_all, selected_item_ids={str(row.id) for row in item_rows})
        prune_daily_brief_history(db, keep_limit=active.daily_brief_history_limit)
        return AIDailyBriefGenerationResult(
            brief=brief,
            status="error",
            reason="request_failed",
            items_considered=len(item_rows_all),
            items_selected=len(item_rows),
        )

    brief.status = "ready"
    brief.title = _normalize_optional_text(completion.payload.get("title")) or "Daily Brief"
    brief.brief_text = _normalize_optional_text(completion.payload.get("brief_text"))
    brief.key_points_json = _normalize_string_list(completion.payload.get("key_points"))[:6]
    brief.recommended_actions_json = _normalize_string_list(completion.payload.get("recommended_actions"))[:6]
    brief.top_item_ids_json = [str(row.id) for row in item_rows]
    brief.provider = completion.provider
    brief.model = completion.model
    brief.prompt_tokens = completion.prompt_tokens
    brief.completion_tokens = completion.completion_tokens
    brief.total_tokens = completion.total_tokens
    brief.latency_ms = completion.latency_ms
    brief.error = None
    brief.generated_at = now
    db.add(brief)
    _replace_daily_brief_source_items(db, brief=brief, item_rows_all=item_rows_all, selected_item_ids={str(row.id) for row in item_rows})
    prune_daily_brief_history(db, keep_limit=active.daily_brief_history_limit)
    return AIDailyBriefGenerationResult(
        brief=brief,
        status="ready",
        reason=None,
        items_considered=len(item_rows_all),
        items_selected=len(item_rows),
        prompt_char_count=completion.prompt_char_count,
        response_char_count=completion.response_char_count,
    )


def get_latest_daily_brief(db: Session) -> AIDailyBrief | None:
    return db.scalar(
        select(AIDailyBrief)
        .where(AIDailyBrief.status == "ready")
        .order_by(AIDailyBrief.brief_date.desc(), AIDailyBrief.generated_at.desc().nullslast())
    )


def get_recent_daily_briefs(db: Session, *, limit: int) -> list[AIDailyBrief]:
    return list(
        db.scalars(
            select(AIDailyBrief)
            .where(AIDailyBrief.status == "ready")
            .order_by(AIDailyBrief.brief_date.desc(), AIDailyBrief.generated_at.desc().nullslast())
            .limit(limit)
        )
    )


def prune_daily_brief_history(db: Session, *, keep_limit: int) -> int:
    if keep_limit < 1:
        return 0

    stale_ids = list(
        db.scalars(
            select(AIDailyBrief.id)
            .order_by(AIDailyBrief.brief_date.desc(), AIDailyBrief.created_at.desc())
            .offset(keep_limit)
        )
    )
    if not stale_ids:
        return 0

    db.execute(delete(AIDailyBrief).where(AIDailyBrief.id.in_(stale_ids)))
    return len(stale_ids)


def daily_brief_response_from_model(db: Session, brief: AIDailyBrief) -> AIDailyBriefResponse:
    item_ids = [uuid.UUID(value) for value in (brief.top_item_ids_json or []) if value]
    rows = db.execute(
        select(
            Item.id,
            Item.title,
            Item.url,
            Item.published_at,
            Feed.name.label("feed_name"),
            ItemAIEnrichment.relevance_score.label("relevance_score"),
            ItemAIEnrichment.relevance_label.label("relevance_label"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .where(Item.id.in_(item_ids))
    ).all() if item_ids else []
    row_by_id = {row.id: row for row in rows}
    items = [
        AIDailyBriefItemResponse(
            id=item_id,
            title=row_by_id[item_id].title,
            feed_name=row_by_id[item_id].feed_name,
            url=row_by_id[item_id].url,
            published_at=row_by_id[item_id].published_at,
            relevance_score=float(row_by_id[item_id].relevance_score) if row_by_id[item_id].relevance_score is not None else None,
            relevance_label=row_by_id[item_id].relevance_label,
        )
        for item_id in item_ids
        if item_id in row_by_id
    ]
    return AIDailyBriefResponse(
        id=brief.id,
        brief_date=brief.brief_date,
        status=brief.status,
        window_start=brief.window_start,
        window_end=brief.window_end,
        title=brief.title,
        brief_text=brief.brief_text,
        key_points=list(brief.key_points_json or []),
        recommended_actions=list(brief.recommended_actions_json or []),
        item_count=int(brief.item_count or 0),
        items=items,
        model=brief.model,
        generated_at=brief.generated_at,
        error=brief.error,
    )


def get_ai_usage_summary(db: Session) -> AIUsageSummaryResponse:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=24)

    totals_row = db.execute(
        select(
            func.count(AIUsageEvent.id).label("total_requests"),
            func.sum(case((AIUsageEvent.success.is_(True), 1), else_=0)).label("successful_requests"),
            func.sum(case((AIUsageEvent.success.is_(False), 1), else_=0)).label("failed_requests"),
            func.sum(func.coalesce(AIUsageEvent.prompt_tokens, 0)).label("total_prompt_tokens"),
            func.sum(func.coalesce(AIUsageEvent.completion_tokens, 0)).label("total_completion_tokens"),
            func.sum(func.coalesce(AIUsageEvent.total_tokens, 0)).label("total_tokens"),
            func.avg(AIUsageEvent.latency_ms).label("average_latency_ms"),
            func.max(AIUsageEvent.created_at).label("last_request_at"),
        )
    ).one()
    requests_last_24h = db.scalar(select(func.count(AIUsageEvent.id)).where(AIUsageEvent.created_at >= window_start)) or 0

    feature_rows = db.execute(
        select(
            AIUsageEvent.feature_type,
            func.count(AIUsageEvent.id).label("total_requests"),
            func.sum(case((AIUsageEvent.success.is_(True), 1), else_=0)).label("successful_requests"),
            func.sum(case((AIUsageEvent.success.is_(False), 1), else_=0)).label("failed_requests"),
            func.sum(func.coalesce(AIUsageEvent.total_tokens, 0)).label("total_tokens"),
            func.avg(AIUsageEvent.latency_ms).label("average_latency_ms"),
            func.max(AIUsageEvent.created_at).label("last_request_at"),
        )
        .group_by(AIUsageEvent.feature_type)
        .order_by(AIUsageEvent.feature_type.asc())
    ).all()

    features = [
        AIUsageFeatureSummary(
            feature_type=row.feature_type,
            total_requests=int(row.total_requests or 0),
            successful_requests=int(row.successful_requests or 0),
            failed_requests=int(row.failed_requests or 0),
            total_tokens=int(row.total_tokens or 0),
            average_latency_ms=round(float(row.average_latency_ms or 0.0), 2),
            last_request_at=row.last_request_at,
        )
        for row in feature_rows
    ]

    total_requests = int(totals_row.total_requests or 0)
    successful_requests = int(totals_row.successful_requests or 0)
    failed_requests = int(totals_row.failed_requests or 0)
    success_rate = (successful_requests / total_requests * 100.0) if total_requests else 0.0
    return AIUsageSummaryResponse(
        total_requests=total_requests,
        successful_requests=successful_requests,
        failed_requests=failed_requests,
        success_rate_pct=round(success_rate, 2),
        requests_last_24h=int(requests_last_24h or 0),
        total_prompt_tokens=int(totals_row.total_prompt_tokens or 0),
        total_completion_tokens=int(totals_row.total_completion_tokens or 0),
        total_tokens=int(totals_row.total_tokens or 0),
        average_latency_ms=round(float(totals_row.average_latency_ms or 0.0), 2),
        last_request_at=totals_row.last_request_at,
        features=features,
    )


def _build_item_enrichment_messages(
    active: ActiveAISettings,
    *,
    item: Item,
    article: Article,
    classification: ItemClassification | None,
    feed: Feed | None,
    tag_names: list[str],
) -> list[dict[str, str]]:
    feature_instructions: list[str] = []
    if active.summary_enabled:
        feature_instructions.append(
            "summary_text: 2-4 sentences explaining the article, emphasizing practical security impact."
        )
    if active.relevance_enabled:
        feature_instructions.append(
            "relevance_score: a number between 0 and 1 for how relevant the article is to the company profile; "
            "relevance_reasons: 1-4 short bullet-style reasons."
        )

    return [
        {"role": "system", "content": build_item_enrichment_system_prompt(active)},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "item_enrichment",
                    "requested_output": feature_instructions,
                    "company_context": _build_company_context(active),
                    "item": {
                        "title": item.title,
                        "summary": _truncate_text(item.summary, MAX_ITEM_SUMMARY_CHARS),
                        "article_text": _truncate_text(article.text, MAX_ITEM_ARTICLE_PROMPT_CHARS),
                        "feed_name": feed.name if feed is not None else None,
                        "url": item.canonical_url or item.url,
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


def _build_daily_brief_messages(
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
            "summary": _truncate_text(row.ai_summary or row.summary, MAX_BRIEF_ITEM_SUMMARY_CHARS),
            "relevance_score": float(row.relevance_score) if row.relevance_score is not None else None,
            "relevance_label": row.relevance_label,
            "url": row.url,
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
                    "company_context": _build_company_context(active),
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "items": compact_items,
                }
            ),
        },
    ]


def _build_company_context(active: ActiveAISettings) -> dict[str, object]:
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


def _request_json_with_usage(
    db: Session,
    active: ActiveAISettings,
    *,
    feature_type: str,
    messages: list[dict[str, str]],
    item_id: uuid.UUID | None = None,
    daily_brief_id: uuid.UUID | None = None,
) -> AICompletionResult:
    try:
        completion = _call_ai_json(active, messages=messages)
    except AIIntegrationError as exc:
        _record_usage_event(
            db,
            feature_type=feature_type,
            success=False,
            provider=active.provider_type,
            model=active.model,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            error=str(exc),
        )
        raise

    _record_usage_event(
        db,
        feature_type=feature_type,
        success=True,
        provider=completion.provider,
        model=completion.model,
        item_id=item_id,
        daily_brief_id=daily_brief_id,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        latency_ms=completion.latency_ms,
    )
    return completion


def _call_ai_json(active: ActiveAISettings, *, messages: list[dict[str, str]]) -> AICompletionResult:
    if not active.ai_enabled:
        raise AIIntegrationError("AI features are disabled")
    if not active.ai_configured or not active.base_url or not active.model:
        raise AIIntegrationError("AI settings are incomplete")

    request_payload = {
        "model": active.model,
        "messages": messages,
        "temperature": active.temperature,
        "max_tokens": active.max_completion_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if active.api_key:
        headers["Authorization"] = f"Bearer {active.api_key}"

    started_at = time.perf_counter()
    try:
        with httpx.Client(timeout=active.request_timeout_seconds) as client:
            response = client.post(_build_chat_completion_url(active.base_url), headers=headers, json=request_payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AIIntegrationError(f"AI request failed: {exc}") from exc

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AIIntegrationError("AI endpoint returned non-JSON output") from exc

    try:
        choice = payload["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIIntegrationError("AI endpoint returned an unexpected response shape") from exc

    content = _extract_message_content(message.get("content"))
    parsed = _parse_ai_json_content(content)
    usage = payload.get("usage") or {}
    prompt_char_count = sum(len(entry.get("content") or "") for entry in messages)
    return AICompletionResult(
        payload=parsed,
        provider=active.provider_type,
        model=payload.get("model") or active.model,
        latency_ms=latency_ms,
        prompt_tokens=_coerce_optional_int(usage.get("prompt_tokens")),
        completion_tokens=_coerce_optional_int(usage.get("completion_tokens")),
        total_tokens=_coerce_optional_int(usage.get("total_tokens")),
        prompt_char_count=prompt_char_count,
        response_char_count=len(content),
    )


def _build_chat_completion_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def _extract_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                text_value = entry.get("text") or entry.get("content")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "\n".join(part for part in parts if part)
    raise AIIntegrationError("AI endpoint did not return text content")


def _parse_ai_json_content(content: str) -> dict[str, object]:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    try:
        parsed = json.loads(candidate)
    except ValueError as exc:
        raise AIIntegrationError("AI response did not contain valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AIIntegrationError("AI response JSON must be an object")
    return parsed


def _record_usage_event(
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


def _replace_daily_brief_source_items(
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


def _load_item_tag_names(db: Session, *, item_id: uuid.UUID) -> list[str]:
    rows = db.execute(
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == item_id)
        .order_by(Tag.name.asc())
    ).all()
    return [name for (name,) in rows]


def _compute_item_source_hash(
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


def _score_to_label(score: float | None, active: ActiveAISettings) -> str | None:
    if score is None:
        return None
    if score >= active.relevance_high_threshold:
        return "high"
    if score >= active.relevance_medium_threshold:
        return "medium"
    return "low"


def _coerce_score(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        numeric = 0.0
    if numeric > 1:
        numeric = 1.0
    return round(numeric, 3)


def _coerce_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        source = value
    else:
        source = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in source:
        text = str(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _truncate_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.split()).strip()
    if not compact:
        return None
    return compact[:limit]
