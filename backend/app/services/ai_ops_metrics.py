from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import Date, Select, String, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.db.text_projection import stripped_text
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
    AI_TASK_TYPE_REPORT,
    AI_TRIGGER_AUTO,
)
from app.services.ai_telemetry_data_policy import (
    ai_task_run_access_predicate,
    ai_usage_event_access_predicate,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)


def build_ai_ops_overview(
    db: Session,
    *,
    days: int,
    live_status_loader: Callable[[Session], AILiveStatusResponse],
    data_access: DataAccessContext | None = None,
) -> AIOpsOverviewResponse:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, days))
    usage_filters = _usage_filters(since, data_access)
    totals = db.execute(
        select(
            func.count().label("requests"),
            func.count().filter(AIUsageEvent.success.is_(True)).label("successes"),
            func.sum(AIUsageEvent.total_tokens).label("tokens"),
            func.avg(AIUsageEvent.latency_ms)
            .filter(AIUsageEvent.success.is_(True))
            .label("latency"),
        ).where(*usage_filters)
    ).one()
    live = live_status_loader(db)
    p95_latency = db.scalar(
        _latency_percentiles(since, data_access, successful_only=True)
    )
    last_successful_run_at = db.scalar(
        select(AITaskRun.finished_at)
        .where(
            AITaskRun.status == AI_STATUS_READY,
            _run_access_predicate(data_access),
        )
        .order_by(AITaskRun.finished_at.desc())
        .limit(1)
    )

    kpis = AIOverviewKpiResponse(
        total_requests=totals.requests,
        success_rate_pct=_percentage(totals.successes, totals.requests),
        total_tokens=int(totals.tokens or 0),
        average_latency_ms=_rounded(totals.latency),
        p95_latency_ms=_rounded(p95_latency),
        active_runs=live.active_count,
        queued_runs=live.queued_count,
        last_successful_run_at=last_successful_run_at,
    )

    return AIOpsOverviewResponse(
        kpis=kpis,
        live=live,
        per_model=_build_per_model_usage(db, since=since, data_access=data_access),
        time_series=_build_time_series(
            db,
            since=since,
            now=now,
            data_access=data_access,
        ),
        token_efficiency=_build_token_efficiency(
            db, since=since, data_access=data_access
        ),
        relevance_distribution=_build_relevance_distribution(
            db, data_access=data_access
        ),
        coverage=_build_coverage_stats(db, data_access=data_access),
        failures=[],
        endpoint_health=_build_endpoint_health(
            db, since=since, now=now, data_access=data_access
        ),
        feature_health=_build_feature_health(db, data_access=data_access),
        storage=_build_storage_stats(db, data_access=data_access),
        cache=_build_cache_stats(db, data_access=data_access),
    )


def list_ai_failures(
    db: Session,
    *,
    days: int = 30,
    limit: int = 25,
    data_access: DataAccessContext | None = None,
) -> list[AIFailureGroupResponse]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    usage_error = _normalized_error_expression(AIUsageEvent.error)
    usage = (
        select(
            literal(None, String).label("task_type"), AIUsageEvent.feature_type,
            AIUsageEvent.model, usage_error.label("error"),
            func.count().label("count"), func.max(AIUsageEvent.created_at).label("last_seen_at"),
        )
        .where(AIUsageEvent.created_at >= since, AIUsageEvent.success.is_(False),
               _usage_access_predicate(data_access))
        .group_by(AIUsageEvent.feature_type, AIUsageEvent.model, usage_error)
    )
    run_error = _normalized_error_expression(AITaskRun.error)
    runs = (
        select(
            AITaskRun.task_type, literal(None, String).label("feature_type"),
            AITaskRun.model, run_error.label("error"), func.count().label("count"),
            func.max(func.coalesce(AITaskRun.finished_at, AITaskRun.updated_at, AITaskRun.created_at)).label("last_seen_at"),
        )
        .where(AITaskRun.created_at >= since,
               or_(AITaskRun.status == AI_STATUS_ERROR, AITaskRun.error.is_not(None)),
               _run_access_predicate(data_access))
        .group_by(AITaskRun.task_type, AITaskRun.model, run_error)
    )
    groups = union_all(usage, runs).subquery()
    rows = db.execute(select(groups).order_by(
        groups.c.count.desc(), groups.c.last_seen_at.desc().nulls_last(),
        groups.c.task_type.asc().nulls_first(), groups.c.feature_type.asc().nulls_first(),
        groups.c.model.asc().nulls_first(), groups.c.error,
    ).limit(max(0, min(limit, 200))))
    return [AIFailureGroupResponse(**row._mapping) for row in rows]


def _normalized_error_expression(column):
    trimmed = stripped_text(column)
    return case(
        (or_(column.is_(None), column == ""), literal("unknown_error")),
        else_=case((func.char_length(trimmed) > 200, func.substr(trimmed, 1, 197) + "..."), else_=trimmed),
    )


def _usage_filters(since: datetime, data_access: DataAccessContext | None):
    return AIUsageEvent.created_at >= since, _usage_access_predicate(data_access)


def _rounded(value, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _percentage(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100.0, 2) if denominator else 0.0


def _utc_day(column):
    # TIMESTAMPTZ grouping must not depend on the connection's session timezone.
    return cast(func.timezone("UTC", column), Date)


def _latency_percentiles(
    since: datetime,
    data_access: DataAccessContext | None,
    *,
    successful_only: bool = False,
    by_day: bool = False,
):
    day = _utc_day(AIUsageEvent.created_at)
    partition = day if by_day else None
    ranked = select(
        AIUsageEvent.latency_ms.label("latency"),
        day.label("day"),
        func.row_number()
        .over(partition_by=partition, order_by=AIUsageEvent.latency_ms)
        .label("rank"),
        func.count().over(partition_by=partition).label("count"),
    ).where(*_usage_filters(since, data_access), AIUsageEvent.latency_ms.is_not(None))
    if successful_only:
        ranked = ranked.where(AIUsageEvent.success.is_(True))
    values = ranked.subquery()
    # Preserve _percentile's round((n - 1) * .95) zero-based rank, including
    # Python's ties-to-even rule. percentile_disc(.95) differs for e.g. n=31.
    numerator = (values.c.count - 1) * 19
    lower = func.floor(numerator / 20)
    remainder = func.mod(numerator, 20)
    rounded = lower + case(
        (remainder > 10, 1),
        (remainder == 10, func.mod(lower, 2)),
        else_=0,
    )
    columns = (values.c.day, values.c.latency) if by_day else (values.c.latency,)
    return select(*columns).where(values.c.rank == rounded + 1)


def _build_per_model_usage(
    db: Session,
    *,
    since: datetime,
    data_access: DataAccessContext | None = None,
) -> list[AIOverviewPerModelResponse]:
    model = func.coalesce(func.nullif(AIUsageEvent.model, ""), "unknown")
    rows = db.execute(
        select(
            model.label("model"),
            func.count().label("requests"),
            func.count().filter(AIUsageEvent.success.is_(True)).label("successes"),
            func.sum(AIUsageEvent.total_tokens).label("tokens"),
            func.avg(AIUsageEvent.latency_ms).label("latency"),
            func.max(AIUsageEvent.created_at).label("last_request"),
        )
        .where(*_usage_filters(since, data_access))
        .group_by(model)
        .order_by(
            func.coalesce(func.sum(AIUsageEvent.total_tokens), 0).desc(),
            model,
        )
    )
    return [
        AIOverviewPerModelResponse(
            model=row.model,
            total_requests=row.requests,
            successful_requests=row.successes,
            failed_requests=row.requests - row.successes,
            success_rate_pct=_percentage(row.successes, row.requests),
            total_tokens=int(row.tokens or 0),
            average_latency_ms=_rounded(row.latency),
            last_request_at=row.last_request,
        )
        for row in rows
    ]


def _build_time_series(
    db: Session,
    *,
    since: datetime,
    now: datetime,
    data_access: DataAccessContext | None = None,
) -> list[AITimeSeriesPointResponse]:
    buckets: dict[str, AITimeSeriesPointResponse] = {}
    cursor = since.date()
    while cursor <= now.date():
        key = cursor.isoformat()
        buckets[key] = _empty_time_series_bucket(key)
        cursor += timedelta(days=1)
    day = _utc_day(AIUsageEvent.created_at)
    for row in db.execute(
        select(
            day.label("day"),
            func.count().label("requests"),
            func.count().filter(AIUsageEvent.success.is_(False)).label("failures"),
            func.sum(AIUsageEvent.total_tokens).label("tokens"),
            func.avg(AIUsageEvent.latency_ms).label("latency"),
        )
        .where(*_usage_filters(since, data_access))
        .group_by(day)
    ):
        key = row.day.isoformat()
        bucket = buckets.setdefault(key, _empty_time_series_bucket(key))
        bucket.requests = row.requests
        bucket.failures = row.failures
        bucket.total_tokens = int(row.tokens or 0)
        bucket.average_latency_ms = _rounded(row.latency)
    for day_value, latency in db.execute(
        _latency_percentiles(since, data_access, by_day=True)
    ):
        buckets[day_value.isoformat()].p95_latency_ms = _rounded(latency)
    run_day = _utc_day(AITaskRun.created_at)
    for row in db.execute(
        select(
            run_day.label("day"),
            func.count().filter(AITaskRun.status == AI_STATUS_READY).label("successes"),
            func.count().filter(AITaskRun.status == AI_STATUS_ERROR).label("failures"),
            func.count().filter(AITaskRun.status == AI_STATUS_SKIPPED).label("skips"),
        )
        .where(
            AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            AITaskRun.created_at >= since,
            _run_access_predicate(data_access),
        )
        .group_by(run_day)
    ):
        key = row.day.isoformat()
        bucket = buckets.setdefault(key, _empty_time_series_bucket(key))
        bucket.daily_brief_successes = row.successes
        bucket.daily_brief_failures = row.failures
        bucket.daily_brief_skips = row.skips
    return [buckets[key] for key in sorted(buckets)]


def _empty_time_series_bucket(key: str) -> AITimeSeriesPointResponse:
    return AITimeSeriesPointResponse(
        bucket=key,
        requests=0,
        failures=0,
        total_tokens=0,
        average_latency_ms=0.0,
        p95_latency_ms=0.0,
        daily_brief_successes=0,
        daily_brief_failures=0,
        daily_brief_skips=0,
    )


def _build_token_efficiency(
    db: Session,
    *,
    since: datetime,
    data_access: DataAccessContext | None = None,
) -> AITokenEfficiencyResponse:
    row = db.execute(
        select(
            func.avg(AIUsageEvent.prompt_tokens).label("prompt"),
            func.avg(AIUsageEvent.completion_tokens).label("completion"),
            func.avg(AIUsageEvent.total_tokens).label("total"),
        ).where(*_usage_filters(since, data_access))
    ).one()
    top = db.execute(
        select(
            AIUsageEvent.feature_type,
            func.avg(AIUsageEvent.total_tokens).label("average"),
        )
        .where(*_usage_filters(since, data_access))
        .group_by(AIUsageEvent.feature_type)
        .having(
            func.avg(AIUsageEvent.total_tokens) > 0,
        )
        .order_by(func.avg(AIUsageEvent.total_tokens).desc(), AIUsageEvent.feature_type)
        .limit(1)
    ).first()
    return AITokenEfficiencyResponse(
        average_prompt_tokens=_rounded(row.prompt),
        average_completion_tokens=_rounded(row.completion),
        average_total_tokens=_rounded(row.total),
        prompt_to_completion_ratio=_rounded(row.prompt / row.completion)
        if row.prompt and row.completion
        else 0.0,
        top_expensive_feature=top.feature_type if top else None,
        top_expensive_feature_avg_tokens=_rounded(top.average) if top else 0.0,
    )


def _build_relevance_distribution(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> AIRelevanceDistributionResponse:
    counts = (
        select(
            func.count().label("items"),
            func.count()
            .filter(ItemAIEnrichment.relevance_label == "high")
            .label("high"),
            func.count()
            .filter(ItemAIEnrichment.relevance_label == "medium")
            .label("medium"),
            func.count().filter(ItemAIEnrichment.relevance_label == "low").label("low"),
            func.avg(ItemAIEnrichment.relevance_score).label("score"),
        )
        .select_from(ItemAIEnrichment)
        .join(Item, Item.id == ItemAIEnrichment.item_id)
        .join(
            Feed,
            Feed.id == Item.feed_id,
        )
        .where(
            ItemAIEnrichment.status == AI_STATUS_READY,
            ItemAIEnrichment.relevance_label.is_not(None),
            _feed_access_predicate(data_access),
        )
    )
    totals = db.execute(counts).one()
    feeds = db.execute(
        counts.add_columns(Feed.name)
        .group_by(Feed.name)
        .order_by(
            func.count().desc(),
            Feed.name,
        )
        .limit(10)
    )
    return AIRelevanceDistributionResponse(
        high_count=totals.high,
        medium_count=totals.medium,
        low_count=totals.low,
        average_score=_rounded(totals.score, 3),
        by_feed=[
            AIRelevanceFeedResponse(
                feed_name=row.name,
                total_items=row.items,
                high_count=row.high,
                medium_count=row.medium,
                low_count=row.low,
                average_score=_rounded(row.score, 3),
            )
            for row in feeds
        ],
    )


def _build_coverage_stats(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> AICoverageStatsResponse:
    from app.models.article import Article

    eligible_items = int(
        db.scalar(
            select(func.count(Item.id))
            .join(Article, Article.item_id == Item.id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(Article.text.is_not(None), _feed_access_predicate(data_access))
        )
        or 0
    )
    enriched_items = int(
        db.scalar(
            select(func.count(ItemAIEnrichment.item_id))
            .join(Item, Item.id == ItemAIEnrichment.item_id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(
                ItemAIEnrichment.status == AI_STATUS_READY,
                _feed_access_predicate(data_access),
            )
        )
        or 0
    )
    pending_items = int(
        db.scalar(
            select(func.count(ItemAIEnrichment.item_id))
            .join(Item, Item.id == ItemAIEnrichment.item_id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(
                ItemAIEnrichment.status == "pending",
                _feed_access_predicate(data_access),
            )
        )
        or 0
    )
    failed_items = int(
        db.scalar(
            select(func.count(ItemAIEnrichment.item_id))
            .join(Item, Item.id == ItemAIEnrichment.item_id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(
                ItemAIEnrichment.status == AI_STATUS_ERROR,
                _feed_access_predicate(data_access),
            )
        )
        or 0
    )
    oldest_pending_at = db.scalar(
        select(ItemAIEnrichment.generated_at)
        .join(Item, Item.id == ItemAIEnrichment.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            ItemAIEnrichment.status == "pending",
            _feed_access_predicate(data_access),
        )
        .order_by(ItemAIEnrichment.generated_at.asc())
        .limit(1)
    )
    last_successful_enrichment_at = db.scalar(
        select(ItemAIEnrichment.generated_at)
        .join(Item, Item.id == ItemAIEnrichment.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            ItemAIEnrichment.status == AI_STATUS_READY,
            _feed_access_predicate(data_access),
        )
        .order_by(ItemAIEnrichment.generated_at.desc())
        .limit(1)
    )
    last_successful_daily_brief_at = db.scalar(
        select(AIDailyBrief.generated_at)
        .where(
            AIDailyBrief.status == AI_STATUS_READY,
            _brief_access_predicate(data_access),
        )
        .order_by(AIDailyBrief.generated_at.desc())
        .limit(1)
    )
    last_ai_run_at = db.scalar(
        select(AITaskRun.finished_at)
        .where(_run_access_predicate(data_access))
        .order_by(AITaskRun.finished_at.desc())
        .limit(1)
    )
    skip_counts = _load_skip_counts(db, data_access=data_access)
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


def _load_skip_counts(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> dict[str, int]:
    rows = db.execute(
        select(AITaskRun.reason, func.count(AITaskRun.id))
        .where(
            AITaskRun.status == AI_STATUS_SKIPPED,
            AITaskRun.reason.is_not(None),
            _run_access_predicate(data_access),
        )
        .group_by(AITaskRun.reason)
    ).all()
    return {reason: int(count) for reason, count in rows if reason}


def _build_endpoint_health(
    db: Session,
    *,
    since: datetime,
    now: datetime,
    data_access: DataAccessContext | None = None,
) -> AIEndpointHealthResponse:
    filters = _usage_filters(since, data_access)
    recent = AIUsageEvent.created_at >= now - timedelta(hours=24)
    failed = AIUsageEvent.success.is_(False)
    row = db.execute(
        select(
            func.max(AIUsageEvent.created_at)
            .filter(AIUsageEvent.success.is_(True))
            .label("last_success"),
            func.count().filter(recent).label("recent"),
            func.count().filter(recent, failed).label("recent_failures"),
            func.percentile_cont(0.5)
            .within_group(AIUsageEvent.latency_ms)
            .filter(AIUsageEvent.success.is_(True))
            .label("median"),
            func.count()
            .filter(failed, func.lower(AIUsageEvent.error).contains("timeout"))
            .label("timeouts"),
        ).where(*filters)
    ).one()
    last_error = db.execute(
        select(AIUsageEvent.created_at, AIUsageEvent.error)
        .where(
            *filters,
            failed,
        )
        .order_by(AIUsageEvent.created_at.desc(), AIUsageEvent.id)
        .limit(1)
    ).first()
    last_auth_error = db.scalar(
        select(AIUsageEvent.error)
        .where(
            *filters,
            failed,
            or_(
                *(
                    func.lower(AIUsageEvent.error).contains(fragment)
                    for fragment in ("401", "403", "unauthorized", "forbidden", "auth")
                )
            ),
        )
        .order_by(AIUsageEvent.created_at.desc(), AIUsageEvent.id)
        .limit(1)
    )
    return AIEndpointHealthResponse(
        last_success_at=row.last_success,
        last_error_at=last_error.created_at if last_error else None,
        rolling_failure_rate_pct=_percentage(row.recent_failures, row.recent),
        median_latency_ms=_rounded(row.median),
        timeout_failures=row.timeouts,
        last_auth_error=last_auth_error,
        last_provider_error=last_error.error if last_error else None,
    )


def _build_feature_health(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> list[AIFeatureHealthRowResponse]:
    settings = db.scalar(select(AISettings).limit(1))
    enabled = {
        "summaries": bool(settings.summary_enabled) if settings else False,
        "relevance": bool(settings.relevance_enabled) if settings else False,
        "daily_brief": bool(settings.daily_brief_enabled) if settings else False,
        "reporting": bool(settings.reporting_enabled) if settings else False,
        "auto_enrichment": bool(settings.auto_enrich_new_items) if settings else False,
    }
    feature_to_filters: dict[str, Select[Any]] = {
        "summaries": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            _run_access_predicate(data_access),
        ),
        "relevance": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            _run_access_predicate(data_access),
        ),
        "daily_brief": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            _run_access_predicate(data_access),
        ),
        "reporting": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_REPORT,
            _run_access_predicate(data_access),
        ),
        "auto_enrichment": select(AITaskRun).where(
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.trigger_source == AI_TRIGGER_AUTO,
            _run_access_predicate(data_access),
        ),
    }
    rows: list[AIFeatureHealthRowResponse] = []
    for feature_key, query in feature_to_filters.items():
        last_run = db.scalar(query.order_by(AITaskRun.created_at.desc()).limit(1))
        last_success = db.scalar(
            query.where(AITaskRun.status == AI_STATUS_READY)
            .order_by(AITaskRun.finished_at.desc())
            .limit(1)
        )
        last_failure = db.scalar(
            query.where(AITaskRun.status == AI_STATUS_ERROR)
            .order_by(AITaskRun.finished_at.desc())
            .limit(1)
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


def _build_storage_stats(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> AIStorageStatsResponse:
    settings = db.scalar(select(AISettings).limit(1))
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    return AIStorageStatsResponse(
        retained_daily_briefs=int(
            db.scalar(
                select(func.count(AIDailyBrief.id)).where(
                    _brief_access_predicate(data_access)
                )
            )
            or 0
        ),
        daily_brief_history_limit=int(settings.daily_brief_history_limit)
        if settings
        else 0,
        enrichment_rows=int(
            db.scalar(
                select(func.count(ItemAIEnrichment.item_id))
                .join(Item, Item.id == ItemAIEnrichment.item_id)
                .join(Feed, Feed.id == Item.feed_id)
                .where(_feed_access_predicate(data_access))
            )
            or 0
        ),
        usage_event_rows=int(
            db.scalar(
                select(func.count(AIUsageEvent.id)).where(
                    _usage_access_predicate(data_access)
                )
            )
            or 0
        ),
        task_history_rows=int(
            db.scalar(
                select(func.count(AITaskRun.id)).where(
                    _run_access_predicate(data_access)
                )
            )
            or 0
        ),
        growth_last_7d=int(
            db.scalar(
                select(func.count(AITaskRun.id)).where(
                    AITaskRun.created_at >= seven_days_ago,
                    _run_access_predicate(data_access),
                )
            )
            or 0
        ),
        growth_last_30d=int(
            db.scalar(
                select(func.count(AITaskRun.id)).where(
                    AITaskRun.created_at >= thirty_days_ago,
                    _run_access_predicate(data_access),
                )
            )
            or 0
        ),
    )


def _build_cache_stats(
    db: Session,
    *,
    data_access: DataAccessContext | None = None,
) -> AICacheStatsResponse:
    reused_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status == AI_STATUS_SKIPPED,
                AITaskRun.reason.in_(["unchanged", "source_hash_unchanged"]),
                _run_access_predicate(data_access),
            )
        )
        or 0
    )
    recomputed_count = int(
        db.scalar(
            select(func.count(AITaskRun.id)).where(
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status == AI_STATUS_READY,
                _run_access_predicate(data_access),
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


def _run_access_predicate(data_access: DataAccessContext | None):
    return (
        ai_task_run_access_predicate(data_access) if data_access is not None else True
    )


def _usage_access_predicate(data_access: DataAccessContext | None):
    return (
        ai_usage_event_access_predicate(data_access)
        if data_access is not None
        else True
    )


def _feed_access_predicate(data_access: DataAccessContext | None):
    return (
        handling_label_access_predicate(Feed.handling_label_id, data_access)
        if data_access is not None
        else True
    )


def _brief_access_predicate(data_access: DataAccessContext | None):
    return (
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_DAILY_BRIEF,
            AIDailyBrief.id,
            data_access,
        )
        if data_access is not None
        else True
    )
