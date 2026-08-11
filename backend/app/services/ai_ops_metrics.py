from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_settings import AISettings
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.ai import (
    AICacheStatsResponse,
    AICoverageStatsResponse,
    AIEndpointHealthResponse,
    AIFailureGroupResponse,
    AIFeatureHealthRowResponse,
    AILiveStatusResponse,
    AIOverviewKpiResponse,
    AIOverviewPerModelResponse,
    AIOpsOverviewResponse,
    AIRelevanceDistributionResponse,
    AIRelevanceFeedResponse,
    AIStorageStatsResponse,
    AITimeSeriesPointResponse,
    AITokenEfficiencyResponse,
)
from app.services.ai_ops_common import (
    AI_STATUS_ERROR,
    AI_STATUS_READY,
    AI_STATUS_SKIPPED,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TRIGGER_AUTO,
    _coerce_utc,
    _percentile,
)


def build_ai_ops_overview(
    db: Session,
    *,
    days: int,
    live_status_loader: Callable[[Session], AILiveStatusResponse],
) -> AIOpsOverviewResponse:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, days))
    usage_events = list(
        db.scalars(select(AIUsageEvent).where(AIUsageEvent.created_at >= since))
    )
    live = live_status_loader(db)

    successful_events = [event for event in usage_events if event.success]
    total_requests = len(usage_events)
    success_rate = (
        (len(successful_events) / total_requests * 100.0) if total_requests else 0.0
    )
    latency_values = [
        float(event.latency_ms)
        for event in successful_events
        if event.latency_ms is not None
    ]
    total_tokens = sum(int(event.total_tokens or 0) for event in usage_events)
    last_successful_run_at = db.scalar(
        select(AITaskRun.finished_at)
        .where(AITaskRun.status == AI_STATUS_READY)
        .order_by(AITaskRun.finished_at.desc())
    )

    kpis = AIOverviewKpiResponse(
        total_requests=total_requests,
        success_rate_pct=round(success_rate, 2),
        total_tokens=total_tokens,
        average_latency_ms=round(sum(latency_values) / len(latency_values), 2)
        if latency_values
        else 0.0,
        p95_latency_ms=round(_percentile(latency_values, 0.95), 2)
        if latency_values
        else 0.0,
        active_runs=live.active_count,
        queued_runs=live.queued_count,
        last_successful_run_at=last_successful_run_at,
    )

    return AIOpsOverviewResponse(
        kpis=kpis,
        live=live,
        per_model=_build_per_model_usage(usage_events),
        time_series=_build_time_series(usage_events, db, since=since, now=now),
        token_efficiency=_build_token_efficiency(usage_events),
        relevance_distribution=_build_relevance_distribution(db),
        coverage=_build_coverage_stats(db),
        failures=[],
        endpoint_health=_build_endpoint_health(usage_events),
        feature_health=_build_feature_health(db),
        storage=_build_storage_stats(db),
        cache=_build_cache_stats(db),
    )


def list_ai_failures(
    db: Session, *, days: int = 30, limit: int = 25
) -> list[AIFailureGroupResponse]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    groups: dict[tuple[str | None, str | None, str | None, str], dict[str, Any]] = {}

    for event in db.scalars(
        select(AIUsageEvent).where(
            AIUsageEvent.created_at >= since, AIUsageEvent.success.is_(False)
        )
    ):
        error = _normalize_error_text(event.error)
        key = (None, event.feature_type, event.model, error)
        entry = groups.setdefault(
            key,
            {
                "task_type": None,
                "feature_type": event.feature_type,
                "model": event.model,
                "error": error,
                "count": 0,
                "last_seen_at": None,
            },
        )
        entry["count"] += 1
        if entry["last_seen_at"] is None or (
            event.created_at and event.created_at > entry["last_seen_at"]
        ):
            entry["last_seen_at"] = event.created_at

    for run in db.scalars(
        select(AITaskRun).where(
            AITaskRun.created_at >= since,
            or_(AITaskRun.status == AI_STATUS_ERROR, AITaskRun.error.is_not(None)),
        )
    ):
        error = _normalize_error_text(run.error)
        key = (run.task_type, None, run.model, error)
        entry = groups.setdefault(
            key,
            {
                "task_type": run.task_type,
                "feature_type": None,
                "model": run.model,
                "error": error,
                "count": 0,
                "last_seen_at": None,
            },
        )
        entry["count"] += 1
        if entry["last_seen_at"] is None or (
            run.finished_at and run.finished_at > entry["last_seen_at"]
        ):
            entry["last_seen_at"] = run.finished_at or run.updated_at

    ordered = sorted(
        groups.values(),
        key=lambda value: (
            value["count"],
            value["last_seen_at"] or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return [
        AIFailureGroupResponse(
            task_type=row["task_type"],
            feature_type=row["feature_type"],
            model=row["model"],
            error=row["error"],
            count=int(row["count"]),
            last_seen_at=row["last_seen_at"],
        )
        for row in ordered[:limit]
    ]


def _build_per_model_usage(
    events: list[AIUsageEvent],
) -> list[AIOverviewPerModelResponse]:
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        key = event.model or "unknown"
        bucket = buckets.setdefault(
            key,
            {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "total_tokens": 0,
                "latencies": [],
                "last_request_at": None,
            },
        )
        bucket["total_requests"] += 1
        bucket["successful_requests"] += 1 if event.success else 0
        bucket["failed_requests"] += 0 if event.success else 1
        bucket["total_tokens"] += int(event.total_tokens or 0)
        if event.latency_ms is not None:
            bucket["latencies"].append(float(event.latency_ms))
        if bucket["last_request_at"] is None or (
            event.created_at and event.created_at > bucket["last_request_at"]
        ):
            bucket["last_request_at"] = event.created_at
    results: list[AIOverviewPerModelResponse] = []
    for model, bucket in buckets.items():
        total_requests = int(bucket["total_requests"])
        results.append(
            AIOverviewPerModelResponse(
                model=model,
                total_requests=total_requests,
                successful_requests=int(bucket["successful_requests"]),
                failed_requests=int(bucket["failed_requests"]),
                success_rate_pct=round(
                    (bucket["successful_requests"] / total_requests * 100.0)
                    if total_requests
                    else 0.0,
                    2,
                ),
                total_tokens=int(bucket["total_tokens"]),
                average_latency_ms=(
                    round(sum(bucket["latencies"]) / len(bucket["latencies"]), 2)
                    if bucket["latencies"]
                    else 0.0
                ),
                last_request_at=bucket["last_request_at"],
            )
        )
    return sorted(results, key=lambda entry: entry.total_tokens, reverse=True)


def _build_time_series(
    events: list[AIUsageEvent],
    db: Session,
    *,
    since: datetime,
    now: datetime,
) -> list[AITimeSeriesPointResponse]:
    buckets: dict[str, dict[str, Any]] = {}
    cursor = since.date()
    while cursor <= now.date():
        key = cursor.isoformat()
        buckets[key] = _empty_time_series_bucket()
        cursor += timedelta(days=1)

    for event in events:
        bucket_key = _coerce_utc(event.created_at).date().isoformat()
        bucket = buckets.setdefault(bucket_key, _empty_time_series_bucket())
        bucket["requests"] += 1
        bucket["failures"] += 0 if event.success else 1
        bucket["total_tokens"] += int(event.total_tokens or 0)
        if event.latency_ms is not None:
            bucket["latencies"].append(float(event.latency_ms))

    daily_runs = list(
        db.scalars(
            select(AITaskRun).where(
                AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
                AITaskRun.created_at >= since,
            )
        )
    )
    for run in daily_runs:
        bucket_key = _coerce_utc(run.created_at).date().isoformat()
        bucket = buckets.setdefault(bucket_key, _empty_time_series_bucket())
        if run.status == AI_STATUS_READY:
            bucket["daily_brief_successes"] += 1
        elif run.status == AI_STATUS_ERROR:
            bucket["daily_brief_failures"] += 1
        elif run.status == AI_STATUS_SKIPPED:
            bucket["daily_brief_skips"] += 1

    return [
        AITimeSeriesPointResponse(
            bucket=key,
            requests=int(value["requests"]),
            failures=int(value["failures"]),
            total_tokens=int(value["total_tokens"]),
            average_latency_ms=(
                round(sum(value["latencies"]) / len(value["latencies"]), 2)
                if value["latencies"]
                else 0.0
            ),
            p95_latency_ms=round(_percentile(value["latencies"], 0.95), 2)
            if value["latencies"]
            else 0.0,
            daily_brief_successes=int(value["daily_brief_successes"]),
            daily_brief_failures=int(value["daily_brief_failures"]),
            daily_brief_skips=int(value["daily_brief_skips"]),
        )
        for key, value in sorted(buckets.items())
    ]


def _empty_time_series_bucket() -> dict[str, Any]:
    return {
        "requests": 0,
        "failures": 0,
        "total_tokens": 0,
        "latencies": [],
        "daily_brief_successes": 0,
        "daily_brief_failures": 0,
        "daily_brief_skips": 0,
    }


def _build_token_efficiency(events: list[AIUsageEvent]) -> AITokenEfficiencyResponse:
    prompt_tokens = [
        int(event.prompt_tokens or 0)
        for event in events
        if event.prompt_tokens is not None
    ]
    completion_tokens = [
        int(event.completion_tokens or 0)
        for event in events
        if event.completion_tokens is not None
    ]
    total_tokens = [
        int(event.total_tokens or 0)
        for event in events
        if event.total_tokens is not None
    ]
    by_feature: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if event.total_tokens is not None:
            by_feature[event.feature_type].append(int(event.total_tokens))
    top_feature = None
    top_feature_avg = 0.0
    for feature, values in by_feature.items():
        avg = sum(values) / len(values)
        if avg > top_feature_avg:
            top_feature = feature
            top_feature_avg = avg
    avg_prompt = sum(prompt_tokens) / len(prompt_tokens) if prompt_tokens else 0.0
    avg_completion = (
        sum(completion_tokens) / len(completion_tokens) if completion_tokens else 0.0
    )
    return AITokenEfficiencyResponse(
        average_prompt_tokens=round(avg_prompt, 2),
        average_completion_tokens=round(avg_completion, 2),
        average_total_tokens=round(sum(total_tokens) / len(total_tokens), 2)
        if total_tokens
        else 0.0,
        prompt_to_completion_ratio=round(avg_prompt / avg_completion, 2)
        if avg_completion
        else 0.0,
        top_expensive_feature=top_feature,
        top_expensive_feature_avg_tokens=round(top_feature_avg, 2),
    )


def _build_relevance_distribution(db: Session) -> AIRelevanceDistributionResponse:
    enrichments = list(
        db.execute(
            select(
                ItemAIEnrichment.relevance_label,
                ItemAIEnrichment.relevance_score,
                Feed.name,
            )
            .join(Item, Item.id == ItemAIEnrichment.item_id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(
                ItemAIEnrichment.status == AI_STATUS_READY,
                ItemAIEnrichment.relevance_label.is_not(None),
            )
        )
    )
    high_count = medium_count = low_count = 0
    total_score = 0.0
    score_count = 0
    by_feed: dict[str, dict[str, Any]] = {}
    for label, score, feed_name in enrichments:
        if label == "high":
            high_count += 1
        elif label == "medium":
            medium_count += 1
        elif label == "low":
            low_count += 1
        if score is not None:
            total_score += float(score)
            score_count += 1
        bucket = by_feed.setdefault(
            feed_name,
            {
                "total_items": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "score_total": 0.0,
                "score_count": 0,
            },
        )
        bucket["total_items"] += 1
        if label == "high":
            bucket["high_count"] += 1
        elif label == "medium":
            bucket["medium_count"] += 1
        elif label == "low":
            bucket["low_count"] += 1
        if score is not None:
            bucket["score_total"] += float(score)
            bucket["score_count"] += 1
    feed_rows = [
        AIRelevanceFeedResponse(
            feed_name=feed_name,
            total_items=int(bucket["total_items"]),
            high_count=int(bucket["high_count"]),
            medium_count=int(bucket["medium_count"]),
            low_count=int(bucket["low_count"]),
            average_score=(
                round(bucket["score_total"] / bucket["score_count"], 3)
                if bucket["score_count"]
                else 0.0
            ),
        )
        for feed_name, bucket in sorted(
            by_feed.items(), key=lambda entry: entry[1]["total_items"], reverse=True
        )[:10]
    ]
    return AIRelevanceDistributionResponse(
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
        average_score=round(total_score / score_count, 3) if score_count else 0.0,
        by_feed=feed_rows,
    )


def _build_coverage_stats(db: Session) -> AICoverageStatsResponse:
    from app.models.article import Article

    eligible_items = int(
        db.scalar(
            select(func.count(Item.id))
            .join(Article, Article.item_id == Item.id)
            .where(Article.text.is_not(None))
        )
        or 0
    )
    enriched_items = int(
        db.scalar(
            select(func.count(ItemAIEnrichment.item_id)).where(
                ItemAIEnrichment.status == AI_STATUS_READY
            )
        )
        or 0
    )
    pending_items = int(
        db.scalar(
            select(func.count(ItemAIEnrichment.item_id)).where(
                ItemAIEnrichment.status == "pending"
            )
        )
        or 0
    )
    failed_items = int(
        db.scalar(
            select(func.count(ItemAIEnrichment.item_id)).where(
                ItemAIEnrichment.status == AI_STATUS_ERROR
            )
        )
        or 0
    )
    oldest_pending_at = db.scalar(
        select(ItemAIEnrichment.generated_at)
        .where(ItemAIEnrichment.status == "pending")
        .order_by(ItemAIEnrichment.generated_at.asc())
    )
    last_successful_enrichment_at = db.scalar(
        select(ItemAIEnrichment.generated_at)
        .where(ItemAIEnrichment.status == AI_STATUS_READY)
        .order_by(ItemAIEnrichment.generated_at.desc())
    )
    last_successful_daily_brief_at = db.scalar(
        select(AIDailyBrief.generated_at)
        .where(AIDailyBrief.status == AI_STATUS_READY)
        .order_by(AIDailyBrief.generated_at.desc())
    )
    last_ai_run_at = db.scalar(
        select(AITaskRun.finished_at).order_by(AITaskRun.finished_at.desc())
    )
    skip_counts = _load_skip_counts(db)
    return AICoverageStatsResponse(
        eligible_items=eligible_items,
        enriched_items=enriched_items,
        pending_items=pending_items,
        failed_items=failed_items,
        skipped_no_article_count=int(
            skip_counts.get("no_article", 0) + skip_counts.get("no_article_text", 0)
        ),
        skipped_ai_disabled_count=int(skip_counts.get("ai_disabled", 0)),
        skipped_not_configured_count=int(skip_counts.get("ai_not_configured", 0)),
        skipped_auto_enrich_disabled_count=int(
            skip_counts.get("auto_enrich_disabled", 0)
        ),
        skipped_unchanged_count=int(
            skip_counts.get("unchanged", 0)
            + skip_counts.get("source_hash_unchanged", 0)
        ),
        oldest_pending_at=oldest_pending_at,
        last_successful_enrichment_at=last_successful_enrichment_at,
        last_successful_daily_brief_at=last_successful_daily_brief_at,
        last_ai_run_at=last_ai_run_at,
    )


def _load_skip_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(AITaskRun.reason, func.count(AITaskRun.id))
        .where(AITaskRun.status == AI_STATUS_SKIPPED, AITaskRun.reason.is_not(None))
        .group_by(AITaskRun.reason)
    ).all()
    return {reason: int(count) for reason, count in rows if reason}


def _build_endpoint_health(events: list[AIUsageEvent]) -> AIEndpointHealthResponse:
    successful = [event for event in events if event.success]
    failed = [event for event in events if not event.success]
    last_success_at = max((event.created_at for event in successful), default=None)
    last_error_event = max(
        failed,
        key=lambda event: event.created_at or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )
    recent_window = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = [
        event for event in events if _coerce_utc(event.created_at) >= recent_window
    ]
    median_latency_ms = (
        round(
            median(
                [
                    event.latency_ms
                    for event in successful
                    if event.latency_ms is not None
                ]
            ),
            2,
        )
        if successful
        else 0.0
    )
    timeout_failures = sum(
        1 for event in failed if "timeout" in (event.error or "").lower()
    )
    last_auth_error = next(
        (
            event.error
            for event in sorted(
                failed,
                key=lambda row: (
                    row.created_at or datetime.min.replace(tzinfo=timezone.utc)
                ),
                reverse=True,
            )
            if _looks_like_auth_error(event.error)
        ),
        None,
    )
    return AIEndpointHealthResponse(
        last_success_at=last_success_at,
        last_error_at=last_error_event.created_at if last_error_event else None,
        rolling_failure_rate_pct=(
            round(
                (sum(1 for event in recent if not event.success) / len(recent) * 100.0),
                2,
            )
            if recent
            else 0.0
        ),
        median_latency_ms=median_latency_ms,
        timeout_failures=timeout_failures,
        last_auth_error=last_auth_error,
        last_provider_error=last_error_event.error if last_error_event else None,
    )


def _build_feature_health(db: Session) -> list[AIFeatureHealthRowResponse]:
    settings = db.scalar(select(AISettings).limit(1))
    enabled = {
        "summaries": bool(settings.summary_enabled) if settings else False,
        "relevance": bool(settings.relevance_enabled) if settings else False,
        "daily_brief": bool(settings.daily_brief_enabled) if settings else False,
        "auto_enrichment": bool(settings.auto_enrich_new_items) if settings else False,
    }
    feature_to_filters: dict[str, Select[Any]] = {
        "summaries": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT
        ),
        "relevance": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT
        ),
        "daily_brief": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF
        ),
        "auto_enrichment": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.trigger_source == AI_TRIGGER_AUTO,
        ),
    }
    rows: list[AIFeatureHealthRowResponse] = []
    for feature_key, query in feature_to_filters.items():
        last_run = db.scalar(query.order_by(AITaskRun.created_at.desc()))
        last_success = db.scalar(
            query.where(AITaskRun.status == AI_STATUS_READY).order_by(
                AITaskRun.finished_at.desc()
            )
        )
        last_failure = db.scalar(
            query.where(AITaskRun.status == AI_STATUS_ERROR).order_by(
                AITaskRun.finished_at.desc()
            )
        )
        rows.append(
            AIFeatureHealthRowResponse(
                feature_key=feature_key,
                enabled=enabled[feature_key],
                last_run_at=last_run.created_at if last_run else None,
                last_success_at=last_success.finished_at if last_success else None,
                last_failure_at=last_failure.finished_at if last_failure else None,
                last_status=last_run.status if last_run else None,
            )
        )
    return rows


def _build_storage_stats(db: Session) -> AIStorageStatsResponse:
    settings = db.scalar(select(AISettings).limit(1))
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    return AIStorageStatsResponse(
        retained_daily_briefs=int(db.scalar(select(func.count(AIDailyBrief.id))) or 0),
        daily_brief_history_limit=int(settings.daily_brief_history_limit)
        if settings
        else 0,
        enrichment_rows=int(
            db.scalar(select(func.count(ItemAIEnrichment.item_id))) or 0
        ),
        usage_event_rows=int(db.scalar(select(func.count(AIUsageEvent.id))) or 0),
        task_history_rows=int(db.scalar(select(func.count(AITaskRun.id))) or 0),
        growth_last_7d=int(
            db.scalar(
                select(func.count(AITaskRun.id)).where(
                    AITaskRun.created_at >= seven_days_ago
                )
            )
            or 0
        ),
        growth_last_30d=int(
            db.scalar(
                select(func.count(AITaskRun.id)).where(
                    AITaskRun.created_at >= thirty_days_ago
                )
            )
            or 0
        ),
    )


def _build_cache_stats(db: Session) -> AICacheStatsResponse:
    reused_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status == AI_STATUS_SKIPPED,
                AITaskRun.reason.in_(["unchanged", "source_hash_unchanged"]),
            )
        )
        or 0
    )
    recomputed_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status == AI_STATUS_READY,
            )
        )
        or 0
    )
    denominator = reused_count + recomputed_count
    return AICacheStatsResponse(
        reused_count=reused_count,
        recomputed_count=recomputed_count,
        no_op_rate_pct=round((reused_count / denominator * 100.0), 2)
        if denominator
        else 0.0,
    )


def _normalize_error_text(value: str | None) -> str:
    if not value:
        return "unknown_error"
    normalized = value.strip()
    if len(normalized) > 200:
        normalized = normalized[:197] + "..."
    return normalized


def _looks_like_auth_error(value: str | None) -> bool:
    lowered = (value or "").lower()
    return any(
        fragment in lowered
        for fragment in ["401", "403", "unauthorized", "forbidden", "auth"]
    )
