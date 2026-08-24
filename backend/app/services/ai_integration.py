from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.schemas.ai import AITestConnectionResponse
from app.services import ai_normalization as _ai_normalization
from app.services import ai_prompting as _ai_prompting
from app.services import ai_provider_client as _ai_provider_client
from app.services.ai_config import ActiveAISettings, load_active_ai_settings
from app.services.ai_ops import (
    AI_PROVIDER_CLAIM_DAILY_BRIEF,
    AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
    AI_PROVIDER_CLAIM_METADATA_KEY,
    ai_task_run_stop_reason,
    record_ai_task_event,
)
from app.services.daily_brief_notifications import emit_daily_brief_ready_event
from app.services.ai_provider_exchange import (
    build_provider_exchange_payload as _build_provider_exchange_payload,
)
from app.services.ai_persistence import (
    compute_item_source_hash as _compute_item_source_hash,
    load_item_tag_names as _load_item_tag_names,
    record_usage_event as _record_usage_event,
    replace_daily_brief_source_items as _replace_daily_brief_source_items,
)
from app.services.ai_provider_client import (
    AICompletionResult as AICompletionResult,
    AIIntegrationError as AIIntegrationError,
    call_ai_json as _provider_call_ai_json,
)
from app.services.ai_reporting import (
    daily_brief_response_from_model as daily_brief_response_from_model,
    get_ai_usage_summary as get_ai_usage_summary,
    get_latest_daily_brief as get_latest_daily_brief,
    get_recent_daily_briefs as get_recent_daily_briefs,
    prune_daily_brief_history as prune_daily_brief_history,
)
from app.services.safe_fetch import build_safe_http_client

MAX_ITEM_ARTICLE_PROMPT_CHARS = _ai_prompting.MAX_ITEM_ARTICLE_PROMPT_CHARS
MAX_ITEM_SUMMARY_CHARS = _ai_prompting.MAX_ITEM_SUMMARY_CHARS
MAX_BRIEF_ITEM_SUMMARY_CHARS = _ai_prompting.MAX_BRIEF_ITEM_SUMMARY_CHARS

_build_item_enrichment_messages = _ai_prompting.build_item_enrichment_messages
_build_daily_brief_messages = _ai_prompting.build_daily_brief_messages
_build_company_context = _ai_prompting.build_company_context

_score_to_label = _ai_normalization.score_to_label
_coerce_score = _ai_normalization.coerce_score
_coerce_optional_int = _ai_normalization.coerce_optional_int
_normalize_optional_text = _ai_normalization.normalize_optional_text
_normalize_string_list = _ai_normalization.normalize_string_list
_normalize_list_entry_text = _ai_normalization.normalize_list_entry_text
_extract_text_from_structured_list_entry = (
    _ai_normalization.extract_text_from_structured_list_entry
)
_truncate_text = _ai_normalization.truncate_text

_build_chat_completion_url = _ai_provider_client.build_chat_completion_url
_ai_status_code_is_retryable = _ai_provider_client.ai_status_code_is_retryable
_extract_provider_error_message = _ai_provider_client.extract_provider_error_message
_looks_like_provider_auth_error = _ai_provider_client.looks_like_provider_auth_error
_extract_message_content = _ai_provider_client.extract_message_content
_parse_ai_json_content = _ai_provider_client.parse_ai_json_content
_strip_code_fence_wrapper = _ai_provider_client.strip_code_fence_wrapper
_extract_first_json_object = _ai_provider_client.extract_first_json_object
_repair_unclosed_json_object = _ai_provider_client.repair_unclosed_json_object
_scan_json_object_balance = _ai_provider_client.scan_json_object_balance

FEATURE_ITEM_ENRICHMENT = "item_enrichment"
FEATURE_DAILY_BRIEF = "daily_brief"
FEATURE_REPORT = "report"
FEATURE_CONNECTION_TEST = "connection_test"

DAILY_BRIEF_PENDING_STALE_AFTER = timedelta(minutes=15)
AI_PROVIDER_RETRY_BASE_DELAY_SECONDS = 0.5
AI_PROVIDER_RETRY_MAX_DELAY_SECONDS = 8.0


class AITaskRunStoppedError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.code = "canceled" if reason == "canceled" else "task_stopped"


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
    integration_event_id: uuid.UUID | None = None


def is_stale_daily_brief_pending(brief: AIDailyBrief, *, now: datetime) -> bool:
    if brief.status != "pending":
        return False

    reference = brief.updated_at or brief.created_at or brief.generated_at
    if reference is None:
        return True
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return now - reference >= DAILY_BRIEF_PENDING_STALE_AFTER


def test_ai_connection(
    db: Session, *, task_run_id: uuid.UUID | None = None
) -> AITestConnectionResponse:
    active = load_active_ai_settings(db)
    if not active.ai_enabled:
        raise AIIntegrationError("AI features are disabled")
    if not active.ai_configured:
        raise AIIntegrationError(
            "Configure the AI base URL and model before testing the connection"
        )

    try:
        completion = _request_json_with_usage(
            db,
            active,
            feature_type=FEATURE_CONNECTION_TEST,
            task_run_id=task_run_id,
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
                            "instructions": 'Return {"ok": true, "message": "ready"}.',
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
        error=None
        if completion.payload.get("ok") is True
        else "Unexpected response from AI endpoint",
    )


def generate_item_ai_enrichment(
    db: Session, *, item_id: uuid.UUID, force: bool = False
) -> ItemAIEnrichment | None:
    return run_item_ai_enrichment(db, item_id=item_id, force=force).enrichment


def run_item_ai_enrichment(
    db: Session,
    *,
    item_id: uuid.UUID,
    force: bool = False,
    task_run_id: uuid.UUID | None = None,
) -> AIItemEnrichmentResult:
    active = load_active_ai_settings(db)
    if not active.ai_enabled or not active.ai_configured:
        return AIItemEnrichmentResult(
            enrichment=None,
            status="skipped",
            reason="ai_not_configured" if active.ai_enabled else "ai_disabled",
            input_text_chars=0,
        )
    if not active.summary_enabled and not active.relevance_enabled:
        return AIItemEnrichmentResult(
            enrichment=None,
            status="skipped",
            reason="feature_disabled",
            input_text_chars=0,
        )

    item = db.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        return AIItemEnrichmentResult(
            enrichment=None,
            status="skipped",
            reason="item_not_found",
            input_text_chars=0,
        )

    article = db.scalar(select(Article).where(Article.item_id == item_id))
    if article is None or not (article.text or "").strip():
        return AIItemEnrichmentResult(
            enrichment=None,
            status="skipped",
            reason="no_article" if article is None else "no_article_text",
            input_text_chars=len((article.text or "")) if article is not None else 0,
        )

    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
    classification = db.scalar(
        select(ItemClassification).where(ItemClassification.item_id == item_id)
    )
    enrichment = db.scalar(
        select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item_id)
    )
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
            if task_run_id is None:
                return AIItemEnrichmentResult(
                    enrichment=enrichment,
                    status="skipped",
                    reason="already_pending",
                    input_text_chars=len(article.text or ""),
                )
            record_ai_task_event(
                db,
                run_id=task_run_id,
                event_type="pending_recovered",
                payload={"item_id": str(item_id)},
            )
    input_text_chars = len(article.text or "")
    messages = _build_item_enrichment_messages(
        active,
        item=item,
        article=article,
        classification=classification,
        feed=feed,
        tag_names=tag_names,
    )
    claim_updated_at = datetime.now(timezone.utc)
    stop_reason = _prepare_provider_claim(
        db,
        task_run_id=task_run_id,
        stage="before_pending_state",
        resource_type=AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
        resource_id=item_id,
        claim_updated_at=claim_updated_at,
    )
    if stop_reason is not None:
        return AIItemEnrichmentResult(
            enrichment=enrichment,
            status="skipped",
            reason=stop_reason,
            input_text_chars=input_text_chars,
        )
    if enrichment is None:
        enrichment = _ensure_item_ai_enrichment_row(db, item_id=item_id)
    db.execute(
        update(ItemAIEnrichment)
        .where(ItemAIEnrichment.item_id == item_id)
        .values(
            status="pending",
            source_hash=source_hash,
            error=None,
            provider=active.provider_type,
            model=active.model,
            updated_at=claim_updated_at,
        )
    )
    db.commit()

    try:
        completion = _request_json_with_usage(
            db,
            active,
            feature_type=FEATURE_ITEM_ENRICHMENT,
            item_id=item_id,
            task_run_id=task_run_id,
            messages=messages,
        )
    except AITaskRunStoppedError as exc:
        return AIItemEnrichmentResult(
            enrichment=_load_item_enrichment(db, item_id=item_id),
            status="skipped",
            reason=exc.reason,
            input_text_chars=input_text_chars,
        )
    except AIIntegrationError as exc:
        stop_reason = _record_task_run_stop_observed(
            db,
            task_run_id=task_run_id,
            stage="after_provider_error",
            lock=True,
        )
        if stop_reason is not None:
            return AIItemEnrichmentResult(
                enrichment=_load_item_enrichment(db, item_id=item_id),
                status="skipped",
                reason=stop_reason,
                input_text_chars=input_text_chars,
            )
        generated_at = claim_updated_at
        finalized = db.execute(
            update(ItemAIEnrichment)
            .where(
                ItemAIEnrichment.item_id == item_id,
                ItemAIEnrichment.status == "pending",
                ItemAIEnrichment.updated_at == claim_updated_at,
            )
            .values(
                status="error",
                error=str(exc),
                generated_at=generated_at,
                updated_at=generated_at,
            )
        )
        enrichment = _load_item_enrichment(db, item_id=item_id)
        if finalized.rowcount != 1:
            _record_provider_result_discarded(
                db,
                task_run_id=task_run_id,
                resource_type=AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
                resource_id=item_id,
            )
            return AIItemEnrichmentResult(
                enrichment=enrichment,
                status="skipped",
                reason="stale_result_discarded",
                input_text_chars=input_text_chars,
            )
        return AIItemEnrichmentResult(
            enrichment=enrichment,
            status="error",
            reason="request_failed",
            input_text_chars=input_text_chars,
        )
    stop_reason = _record_task_run_stop_observed(
        db,
        task_run_id=task_run_id,
        stage="after_provider_response",
        lock=True,
    )
    if stop_reason is not None:
        return AIItemEnrichmentResult(
            enrichment=_load_item_enrichment(db, item_id=item_id),
            status="skipped",
            reason=stop_reason,
            input_text_chars=input_text_chars,
            prompt_char_count=completion.prompt_char_count,
            response_char_count=completion.response_char_count,
        )

    summary_text = (
        _normalize_optional_text(completion.payload.get("summary_text"))
        if active.summary_enabled
        else None
    )
    relevance_score = (
        _coerce_score(completion.payload.get("relevance_score"))
        if active.relevance_enabled
        else None
    )
    relevance_label = (
        _score_to_label(relevance_score, active)
        if relevance_score is not None
        else None
    )
    relevance_reasons = (
        _normalize_string_list(completion.payload.get("relevance_reasons"))
        if active.relevance_enabled
        else []
    )

    generated_at = claim_updated_at
    finalized = db.execute(
        update(ItemAIEnrichment)
        .where(
            ItemAIEnrichment.item_id == item_id,
            ItemAIEnrichment.status == "pending",
            ItemAIEnrichment.updated_at == claim_updated_at,
        )
        .values(
            status="ready",
            summary_text=summary_text,
            relevance_score=relevance_score,
            relevance_label=relevance_label,
            relevance_reasons_json=relevance_reasons[:4],
            provider=completion.provider,
            model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            latency_ms=completion.latency_ms,
            error=None,
            generated_at=generated_at,
            updated_at=generated_at,
        )
    )
    enrichment = _load_item_enrichment(db, item_id=item_id)
    if finalized.rowcount != 1:
        _record_provider_result_discarded(
            db,
            task_run_id=task_run_id,
            resource_type=AI_PROVIDER_CLAIM_ITEM_ENRICHMENT,
            resource_id=item_id,
        )
        return AIItemEnrichmentResult(
            enrichment=enrichment,
            status="skipped",
            reason="stale_result_discarded",
            input_text_chars=input_text_chars,
            prompt_char_count=completion.prompt_char_count,
            response_char_count=completion.response_char_count,
        )
    return AIItemEnrichmentResult(
        enrichment=enrichment,
        status="ready",
        reason=None,
        input_text_chars=input_text_chars,
        prompt_char_count=completion.prompt_char_count,
        response_char_count=completion.response_char_count,
    )


def _ensure_item_ai_enrichment_row(
    db: Session, *, item_id: uuid.UUID
) -> ItemAIEnrichment:
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
    enrichment = db.scalar(
        select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item_id)
    )
    if enrichment is None:
        raise AIIntegrationError("Failed to initialize AI enrichment state")
    return enrichment


def generate_daily_brief(
    db: Session,
    *,
    force: bool = False,
    reference_time: datetime | None = None,
) -> AIDailyBrief | None:
    return run_daily_brief_generation(
        db, force=force, reference_time=reference_time
    ).brief


def run_daily_brief_generation(
    db: Session,
    *,
    force: bool = False,
    reference_time: datetime | None = None,
    task_run_id: uuid.UUID | None = None,
    emit_notification: bool = True,
) -> AIDailyBriefGenerationResult:
    active = load_active_ai_settings(db)
    if (
        not active.ai_enabled
        or not active.ai_configured
        or not active.daily_brief_enabled
    ):
        if not active.ai_enabled:
            return AIDailyBriefGenerationResult(
                brief=None,
                status="skipped",
                reason="ai_disabled",
                items_considered=0,
                items_selected=0,
            )
        if not active.ai_configured:
            return AIDailyBriefGenerationResult(
                brief=None,
                status="skipped",
                reason="ai_not_configured",
                items_considered=0,
                items_selected=0,
            )
        return AIDailyBriefGenerationResult(
            brief=None,
            status="skipped",
            reason="feature_disabled",
            items_considered=0,
            items_selected=0,
        )
    stop_reason = _record_task_run_stop_observed(
        db, task_run_id=task_run_id, stage="before_brief_selection"
    )
    if stop_reason is not None:
        return AIDailyBriefGenerationResult(
            brief=None,
            status="skipped",
            reason=stop_reason,
            items_considered=0,
            items_selected=0,
        )

    now = reference_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    brief_date = now.date()

    existing = db.scalar(
        select(AIDailyBrief).where(AIDailyBrief.brief_date == brief_date)
    )
    if existing is not None and existing.status == "ready" and not force:
        prune_daily_brief_history(db, keep_limit=active.daily_brief_history_limit)
        notification_event = (
            emit_daily_brief_ready_event(db, brief=existing)
            if emit_notification
            else None
        )
        return AIDailyBriefGenerationResult(
            brief=existing,
            status="skipped",
            reason="already_generated",
            items_considered=int(existing.item_count or 0),
            items_selected=len(existing.top_item_ids_json or []),
            integration_event_id=notification_event.id
            if notification_event is not None
            else None,
        )
    if (
        existing is not None
        and existing.status == "pending"
        and not force
        and not is_stale_daily_brief_pending(existing, now=now)
    ):
        return AIDailyBriefGenerationResult(
            brief=existing,
            status="skipped",
            reason="already_running",
            items_considered=int(existing.item_count or 0),
            items_selected=len(existing.top_item_ids_json or []),
        )

    window_end = now
    window_start = now - timedelta(hours=active.daily_brief_window_hours)
    item_window_at = func.coalesce(Item.published_at, Item.first_seen_at)
    total_items = (
        db.scalar(
            select(func.count(Item.id)).where(
                item_window_at >= window_start, item_window_at <= window_end
            )
        )
        or 0
    )
    if total_items <= 0:
        return AIDailyBriefGenerationResult(
            brief=existing
            if existing is not None and existing.status == "ready"
            else None,
            status="skipped",
            reason="no_items",
            items_considered=0,
            items_selected=0,
        )

    source_audit_limit = max(
        active.daily_brief_max_items,
        int(get_settings().ai_daily_brief_source_audit_limit or 0),
    )
    source_audit_limit = max(1, min(int(total_items), source_audit_limit))
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
        .where(item_window_at >= window_start, item_window_at <= window_end)
        .order_by(
            ItemAIEnrichment.relevance_score.desc().nullslast(), item_window_at.desc()
        )
        .limit(source_audit_limit)
    ).all()
    item_rows = item_rows_all[: active.daily_brief_max_items]
    if not item_rows:
        return AIDailyBriefGenerationResult(
            brief=existing
            if existing is not None and existing.status == "ready"
            else None,
            status="skipped",
            reason="no_items",
            items_considered=int(total_items),
            items_selected=0,
        )

    brief = existing or AIDailyBrief(
        id=uuid.uuid4(),
        brief_date=brief_date,
        window_start=window_start,
        window_end=window_end,
    )
    brief_id = brief.id
    claim_updated_at = datetime.now(timezone.utc)
    stop_reason = _prepare_provider_claim(
        db,
        task_run_id=task_run_id,
        stage="before_provider_request",
        resource_type=AI_PROVIDER_CLAIM_DAILY_BRIEF,
        resource_id=brief_id,
        claim_updated_at=claim_updated_at,
    )
    if stop_reason is not None:
        return AIDailyBriefGenerationResult(
            brief=existing,
            status="skipped",
            reason=stop_reason,
            items_considered=len(item_rows_all),
            items_selected=len(item_rows),
        )
    brief.status = "pending"
    brief.window_start = window_start
    brief.window_end = window_end
    brief.item_count = int(total_items)
    brief.error = None
    brief.provider = active.provider_type
    brief.model = active.model
    brief.updated_at = claim_updated_at
    db.add(brief)
    messages = _build_daily_brief_messages(
        active,
        item_rows=item_rows,
        window_start=window_start,
        window_end=window_end,
    )
    selected_item_ids = {str(row.id) for row in item_rows}
    try:
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        competing_brief = db.scalar(
            select(AIDailyBrief).where(AIDailyBrief.brief_date == brief_date)
        )
        if competing_brief is None:
            raise
        return AIDailyBriefGenerationResult(
            brief=competing_brief,
            status="skipped",
            reason=(
                "already_generated"
                if competing_brief.status == "ready"
                else "already_running"
            ),
            items_considered=int(competing_brief.item_count or total_items),
            items_selected=len(competing_brief.top_item_ids_json or []),
        )

    try:
        completion = _request_json_with_usage(
            db,
            active,
            feature_type=FEATURE_DAILY_BRIEF,
            daily_brief_id=brief_id,
            task_run_id=task_run_id,
            messages=messages,
        )
    except AITaskRunStoppedError as exc:
        return AIDailyBriefGenerationResult(
            brief=_load_daily_brief(db, brief_id=brief_id),
            status="skipped",
            reason=exc.reason,
            items_considered=len(item_rows_all),
            items_selected=len(item_rows),
        )
    except AIIntegrationError as exc:
        stop_reason = _record_task_run_stop_observed(
            db,
            task_run_id=task_run_id,
            stage="after_provider_error",
            lock=True,
        )
        if stop_reason is not None:
            return AIDailyBriefGenerationResult(
                brief=_load_daily_brief(db, brief_id=brief_id),
                status="skipped",
                reason=stop_reason,
                items_considered=len(item_rows_all),
                items_selected=len(item_rows),
            )
        generated_at = now
        finalized = db.execute(
            update(AIDailyBrief)
            .where(
                AIDailyBrief.id == brief_id,
                AIDailyBrief.status == "pending",
                AIDailyBrief.updated_at == claim_updated_at,
            )
            .values(
                status="error",
                error=str(exc),
                generated_at=generated_at,
                updated_at=generated_at,
            )
        )
        brief = _load_daily_brief(db, brief_id=brief_id)
        if finalized.rowcount != 1 or brief is None:
            _record_provider_result_discarded(
                db,
                task_run_id=task_run_id,
                resource_type=AI_PROVIDER_CLAIM_DAILY_BRIEF,
                resource_id=brief_id,
            )
            return AIDailyBriefGenerationResult(
                brief=brief,
                status="skipped",
                reason="stale_result_discarded",
                items_considered=len(item_rows_all),
                items_selected=len(item_rows),
            )
        _replace_daily_brief_source_items(
            db,
            brief=brief,
            item_rows_all=item_rows_all,
            selected_item_ids=selected_item_ids,
        )
        prune_daily_brief_history(db, keep_limit=active.daily_brief_history_limit)
        return AIDailyBriefGenerationResult(
            brief=brief,
            status="error",
            reason="request_failed",
            items_considered=len(item_rows_all),
            items_selected=len(item_rows),
        )
    stop_reason = _record_task_run_stop_observed(
        db,
        task_run_id=task_run_id,
        stage="after_provider_response",
        lock=True,
    )
    if stop_reason is not None:
        return AIDailyBriefGenerationResult(
            brief=_load_daily_brief(db, brief_id=brief_id),
            status="skipped",
            reason=stop_reason,
            items_considered=len(item_rows_all),
            items_selected=len(item_rows),
            prompt_char_count=completion.prompt_char_count,
            response_char_count=completion.response_char_count,
        )

    generated_at = now
    finalized = db.execute(
        update(AIDailyBrief)
        .where(
            AIDailyBrief.id == brief_id,
            AIDailyBrief.status == "pending",
            AIDailyBrief.updated_at == claim_updated_at,
        )
        .values(
            status="ready",
            title=_normalize_optional_text(completion.payload.get("title"))
            or "Daily Brief",
            brief_text=_normalize_optional_text(completion.payload.get("brief_text")),
            key_points_json=_normalize_string_list(
                completion.payload.get("key_points")
            )[:6],
            recommended_actions_json=_normalize_string_list(
                completion.payload.get("recommended_actions")
            )[:6],
            top_item_ids_json=[str(row.id) for row in item_rows],
            provider=completion.provider,
            model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            latency_ms=completion.latency_ms,
            error=None,
            generated_at=generated_at,
            updated_at=generated_at,
        )
    )
    brief = _load_daily_brief(db, brief_id=brief_id)
    if finalized.rowcount != 1 or brief is None:
        _record_provider_result_discarded(
            db,
            task_run_id=task_run_id,
            resource_type=AI_PROVIDER_CLAIM_DAILY_BRIEF,
            resource_id=brief_id,
        )
        return AIDailyBriefGenerationResult(
            brief=brief,
            status="skipped",
            reason="stale_result_discarded",
            items_considered=len(item_rows_all),
            items_selected=len(item_rows),
            prompt_char_count=completion.prompt_char_count,
            response_char_count=completion.response_char_count,
        )
    _replace_daily_brief_source_items(
        db,
        brief=brief,
        item_rows_all=item_rows_all,
        selected_item_ids=selected_item_ids,
    )
    prune_daily_brief_history(db, keep_limit=active.daily_brief_history_limit)
    notification_event = (
        emit_daily_brief_ready_event(db, brief=brief) if emit_notification else None
    )
    return AIDailyBriefGenerationResult(
        brief=brief,
        status="ready",
        reason=None,
        items_considered=len(item_rows_all),
        items_selected=len(item_rows),
        prompt_char_count=completion.prompt_char_count,
        response_char_count=completion.response_char_count,
        integration_event_id=notification_event.id
        if notification_event is not None
        else None,
    )


def _record_task_run_stop_observed(
    db: Session,
    *,
    task_run_id: uuid.UUID | None,
    stage: str,
    lock: bool = False,
) -> str | None:
    if task_run_id is None:
        return None
    statement = select(AITaskRun).where(AITaskRun.id == task_run_id)
    if lock:
        statement = statement.with_for_update()
    run = db.scalar(statement.execution_options(populate_existing=True))
    stop_reason = ai_task_run_stop_reason(run)
    if stop_reason is None:
        return None
    _record_task_run_stop_event(
        db, run=run, task_run_id=task_run_id, stage=stage, stop_reason=stop_reason
    )
    return stop_reason


def _record_task_run_stop_event(
    db: Session,
    *,
    run: AITaskRun | None,
    task_run_id: uuid.UUID,
    stage: str,
    stop_reason: str,
) -> None:
    if stop_reason == "canceled":
        event_type = "cancel_observed"
        payload = {"stage": stage}
    else:
        event_type = "terminal_run_observed"
        payload = {
            "stage": stage,
            "status": run.status if run is not None else None,
            "reason": run.reason if run is not None else None,
            "resolved_reason": stop_reason,
        }
    record_ai_task_event(
        db,
        run_id=task_run_id,
        event_type=event_type,
        payload=payload,
    )


def _prepare_provider_claim(
    db: Session,
    *,
    task_run_id: uuid.UUID | None,
    stage: str,
    resource_type: str,
    resource_id: uuid.UUID,
    claim_updated_at: datetime,
) -> str | None:
    if task_run_id is None:
        return None
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    stop_reason = ai_task_run_stop_reason(run)
    if stop_reason is not None:
        _record_task_run_stop_event(
            db,
            run=run,
            task_run_id=task_run_id,
            stage=stage,
            stop_reason=stop_reason,
        )
        return stop_reason
    if run is None:
        return None
    run.metadata_json = {
        **dict(run.metadata_json or {}),
        AI_PROVIDER_CLAIM_METADATA_KEY: {
            "resource_type": resource_type,
            "resource_id": str(resource_id),
            "updated_at": claim_updated_at.isoformat(),
        },
    }
    if resource_type == AI_PROVIDER_CLAIM_DAILY_BRIEF:
        run.daily_brief_id = resource_id
    db.add(run)
    return None


def _record_provider_result_discarded(
    db: Session,
    *,
    task_run_id: uuid.UUID | None,
    resource_type: str,
    resource_id: uuid.UUID,
) -> None:
    if task_run_id is None:
        return
    record_ai_task_event(
        db,
        run_id=task_run_id,
        event_type="provider_result_discarded",
        payload={
            "reason": "stale_claim",
            "resource_type": resource_type,
            "resource_id": str(resource_id),
        },
    )


def _load_item_enrichment(
    db: Session, *, item_id: uuid.UUID
) -> ItemAIEnrichment | None:
    return db.scalar(
        select(ItemAIEnrichment)
        .where(ItemAIEnrichment.item_id == item_id)
        .execution_options(populate_existing=True)
    )


def _load_daily_brief(db: Session, *, brief_id: uuid.UUID) -> AIDailyBrief | None:
    return db.scalar(
        select(AIDailyBrief)
        .where(AIDailyBrief.id == brief_id)
        .execution_options(populate_existing=True)
    )


def _request_json_with_usage(
    db: Session,
    active: ActiveAISettings,
    *,
    feature_type: str,
    messages: list[dict[str, str]],
    item_id: uuid.UUID | None = None,
    daily_brief_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
    task_run_id: uuid.UUID | None = None,
    max_completion_tokens: int | None = None,
    max_retry_completion_tokens: int | None = None,
    max_provider_attempts: int | None = None,
    execution_checkpoint: Callable[[], None] | None = None,
    execution_commit: Callable[[], None] | None = None,
) -> AICompletionResult:
    max_attempts = max(1, active.request_max_retries + 1)
    if max_provider_attempts is not None:
        if max_provider_attempts < 1:
            raise AIIntegrationError(
                "AI provider attempt budget is exhausted", retryable=False
            )
        max_attempts = min(max_attempts, max_provider_attempts)
    last_error: AIIntegrationError | None = None
    request_max_tokens = max_completion_tokens or active.max_completion_tokens
    _commit_ai_progress(db, execution_commit)

    for attempt in range(1, max_attempts + 1):
        if execution_checkpoint is not None:
            execution_checkpoint()
        if attempt > 1:
            stop_reason = _record_task_run_stop_observed(
                db,
                task_run_id=task_run_id,
                stage="before_provider_retry",
            )
            if stop_reason is not None:
                _commit_ai_progress(db, execution_commit)
                raise AITaskRunStoppedError(stop_reason)
            _commit_ai_progress(db, execution_commit)
        try:
            call_kwargs: dict[str, object] = {"messages": messages}
            if request_max_tokens != active.max_completion_tokens:
                call_kwargs["max_completion_tokens"] = request_max_tokens
            completion = _call_ai_json(active, **call_kwargs)
        except AIIntegrationError as exc:
            if execution_checkpoint is not None:
                execution_checkpoint()
            last_error = exc
            next_request_max_tokens = _next_retry_max_completion_tokens(
                feature_type=feature_type,
                current=request_max_tokens,
                error=exc,
                maximum=max_retry_completion_tokens,
            )
            report_truncation_has_headroom = not (
                feature_type == FEATURE_REPORT
                and exc.retry_hint == "expand_completion_budget"
                and next_request_max_tokens <= request_max_tokens
            )
            should_retry = (
                attempt < max_attempts
                and _ai_error_is_retryable(exc)
                and report_truncation_has_headroom
            )
            retry_delay_seconds = (
                _provider_retry_delay_seconds(attempt=attempt) if should_retry else None
            )
            payload = {
                **exc.debug_payload(),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "requested_max_tokens": request_max_tokens,
            }
            if next_request_max_tokens != request_max_tokens:
                payload["next_max_tokens"] = next_request_max_tokens
            if retry_delay_seconds is not None:
                payload["retry_delay_seconds"] = round(retry_delay_seconds, 3)
            if task_run_id is not None:
                record_ai_task_event(
                    db,
                    run_id=task_run_id,
                    event_type="provider_exchange_retry"
                    if should_retry
                    else "provider_exchange_failed",
                    message=str(exc),
                    payload=payload,
                )
            _record_usage_event(
                db,
                feature_type=feature_type,
                success=False,
                provider=active.provider_type,
                model=active.model,
                item_id=item_id,
                daily_brief_id=daily_brief_id,
                report_id=report_id,
                error=str(exc),
            )
            _commit_ai_progress(db, execution_commit)
            if should_retry:
                request_max_tokens = next_request_max_tokens
                if retry_delay_seconds is not None and retry_delay_seconds > 0:
                    time.sleep(retry_delay_seconds)
                continue
            exc.attempt_count = attempt
            raise

        if execution_checkpoint is not None:
            execution_checkpoint()
        if task_run_id is not None:
            provider_exchange_payload = {
                **_build_provider_exchange_payload(
                    request_url=completion.request_url,
                    request_payload=completion.request_payload,
                    response_body=completion.response_body,
                    response_json=completion.response_json,
                    status_code=completion.status_code,
                    finish_reason=completion.finish_reason,
                ),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "requested_max_tokens": request_max_tokens,
            }
            record_ai_task_event(
                db,
                run_id=task_run_id,
                event_type="provider_exchange",
                payload=provider_exchange_payload,
            )

        _record_usage_event(
            db,
            feature_type=feature_type,
            success=True,
            provider=completion.provider,
            model=completion.model,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            report_id=report_id,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            latency_ms=completion.latency_ms,
        )
        _commit_ai_progress(db, execution_commit)
        return replace(completion, attempt_count=attempt)

    if last_error is None:
        raise AIIntegrationError("AI request failed unexpectedly")
    last_error.attempt_count = max_attempts
    raise last_error


def request_ai_json_with_usage(
    db: Session,
    active: ActiveAISettings,
    *,
    feature_type: str,
    messages: list[dict[str, str]],
    report_id: uuid.UUID | None = None,
    task_run_id: uuid.UUID | None = None,
    max_completion_tokens: int | None = None,
    max_retry_completion_tokens: int | None = None,
    max_provider_attempts: int | None = None,
    execution_checkpoint: Callable[[], None] | None = None,
    execution_commit: Callable[[], None] | None = None,
) -> AICompletionResult:
    """Run a provider exchange with the standard retry, history, and cancellation behavior."""
    return _request_json_with_usage(
        db,
        active,
        feature_type=feature_type,
        messages=messages,
        report_id=report_id,
        task_run_id=task_run_id,
        max_completion_tokens=max_completion_tokens,
        max_retry_completion_tokens=max_retry_completion_tokens,
        max_provider_attempts=max_provider_attempts,
        execution_checkpoint=execution_checkpoint,
        execution_commit=execution_commit,
    )


def _commit_ai_progress(
    db: Session,
    execution_commit: Callable[[], None] | None,
) -> None:
    if execution_commit is not None:
        execution_commit()
        return
    db.commit()


def _provider_retry_delay_seconds(*, attempt: int) -> float:
    capped_attempt = max(1, int(attempt))
    base_delay_seconds = min(
        AI_PROVIDER_RETRY_MAX_DELAY_SECONDS,
        AI_PROVIDER_RETRY_BASE_DELAY_SECONDS * (2 ** (capped_attempt - 1)),
    )
    return base_delay_seconds + random.uniform(0.0, base_delay_seconds)


def _ai_error_is_retryable(error: AIIntegrationError) -> bool:
    if error.retry_hint == "expand_completion_budget":
        return True
    if error.retryable:
        return True
    if error.status_code is not None:
        return (
            error.status_code in {408, 409, 425, 429} or 500 <= error.status_code <= 599
        )
    return False


def _next_retry_max_completion_tokens(
    *,
    feature_type: str,
    current: int,
    error: AIIntegrationError,
    maximum: int | None = None,
) -> int:
    if error.retry_hint != "expand_completion_budget":
        return current
    if feature_type == FEATURE_REPORT:
        if maximum is None or maximum <= current:
            return current
        return min(maximum, max(current + 256, int(current * 1.5)))
    if feature_type == FEATURE_DAILY_BRIEF:
        return min(8192, max(current, current + 512, int(current * 1.5)))
    return min(2048, max(current + 256, int(current * 1.5)))


def _call_ai_json(
    active: ActiveAISettings,
    *,
    messages: list[dict[str, str]],
    max_completion_tokens: int | None = None,
) -> AICompletionResult:
    return _provider_call_ai_json(
        active,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        client_factory=build_safe_http_client,
    )
