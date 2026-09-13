"""Bounded, permission-aware AI operational aggregates for the statistics workspace."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.schemas.ai_statistics import AIFeatureStatistics, AIQueueStatistics, AIStatisticsResponse
from app.services.ai_failure_categories import DEADLINE_CATEGORIES, TIMEOUT_CATEGORIES
from app.services.ai_telemetry_data_policy import ai_task_run_access_predicate, ai_usage_event_access_predicate
from app.services.data_access_policy import DataAccessContext

FEATURES = ("summary", "relevance", "item_enrichment", "daily_brief", "report", "connection_test", "reprocess")
BUDGET_FAILURES = ("budget_request_too_large", "provider_concurrency_budget", "provider_hourly_token_budget", "provider_budget_unavailable")
LATENCY_BUCKETS = (("under_1s", 0, 1000), ("1_to_5s", 1000, 5000), ("5_to_15s", 5000, 15000),
                   ("15_to_60s", 15000, 60000), ("60s_or_more", 60000, None))


def build_ai_statistics(db: Session, *, days: int, data_access: DataAccessContext) -> AIStatisticsResponse:
    now = datetime.now(timezone.utc)
    days = min(365, max(1, days))
    since = now - timedelta(days=days)
    event = AIUsageEvent
    feature = case((event.feature_type.in_(FEATURES), event.feature_type), else_="other")
    successful, failed = event.success.is_(True), event.success.is_(False)
    filters = (event.created_at >= since, event.created_at < now, ai_usage_event_access_predicate(data_access))
    rows = db.execute(select(
        feature.label("feature"), func.count().label("requests"),
        func.count().filter(successful).label("successful"), func.count().filter(failed).label("failed"),
        func.count(event.total_tokens).label("known_usage_requests"),
        func.count().filter(event.total_tokens.is_(None)).label("unknown_usage_requests"),
        *(func.coalesce(func.sum(getattr(event, key)), 0).label(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")),
        func.count().filter(event.provider_io_outcome == "not_sent").label("not_sent"),
        func.count().filter(event.provider_io_outcome == "ambiguous").label("ambiguous"),
        func.count().filter(failed, event.failure_category.in_(DEADLINE_CATEGORIES)).label("deadline_failures"),
        func.count().filter(failed, event.failure_category.in_(TIMEOUT_CATEGORIES)).label("timeout_failures"),
        func.count().filter(failed, event.failure_category == "truncated_output").label("truncated_outputs"),
        func.count().filter(failed, event.failure_category.in_(BUDGET_FAILURES)).label("budget_rejections"),
        func.count(event.latency_ms).filter(successful).label("latency_samples"),
        *(func.percentile_cont(percentile).within_group(event.latency_ms).filter(successful).label(f"p{label}_latency_ms")
          for label, percentile in ((50, .5), (95, .95), (99, .99))),
    ).where(*filters).group_by(feature).order_by(feature))
    features = [AIFeatureStatistics(**row._mapping) for row in rows]
    histogram = db.execute(select(*(
        func.count().filter(successful, event.latency_ms >= lower,
                            event.latency_ms < upper if upper is not None else event.latency_ms.is_not(None)).label(label)
        for label, lower, upper in LATENCY_BUCKETS
    )).where(*filters)).one()
    run_feature = case((AITaskRun.task_type.in_(FEATURES), AITaskRun.task_type), else_="other")
    queues = db.execute(select(
        run_feature.label("feature"),
        func.count().filter(AITaskRun.status == "queued").label("queued"),
        func.count().filter(AITaskRun.status == "running").label("running"),
        func.min(AITaskRun.queued_at).filter(AITaskRun.status == "queued").label("oldest_queued_at"),
        func.min(AITaskRun.started_at).filter(AITaskRun.status == "running").label("oldest_running_at"),
    ).where(AITaskRun.status.in_(("queued", "running")), ai_task_run_access_predicate(data_access))
      .group_by(run_feature).order_by(run_feature))
    receipt = AIProviderAttemptReceipt
    retries = db.execute(select(
        func.count().filter(receipt.attempt_number > 1).label("retries"),
        func.coalesce(func.sum(receipt.pre_io_failure_count), 0).label("pre_io"),
    ).join(AITaskRun, AITaskRun.id == receipt.task_run_id_snapshot)
      .where(receipt.created_at >= since, receipt.created_at < now, ai_task_run_access_predicate(data_access))).one()
    return AIStatisticsResponse(
        since=since, until=now, days=days, features=features,
        queues=[AIQueueStatistics(**row._mapping) for row in queues],
        provider_retry_attempts=retries.retries, recovered_pre_io_failures=retries.pre_io,
        latency_histogram=dict(histogram._mapping),
    )
