"""Permission-aware provider snapshot aggregation; no event history materialization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.ai_usage_event import AIUsageEvent
from app.schemas.ai_provider_usage import AIProviderUsageResponse, AIProviderUsageRow
from app.services.ai_failure_categories import DEADLINE_CATEGORIES, TIMEOUT_CATEGORIES
from app.services.ai_telemetry_data_policy import ai_usage_event_access_predicate
from app.services.data_access_policy import DataAccessContext

# Bound the result width even if old or future events use unknown categories.
FAILURE_CATEGORIES = tuple(sorted(TIMEOUT_CATEGORIES | {
    "provider_auth", "provider_rate_limit", "provider_server", "provider_request", "transport",
    "response_too_large", "invalid_json", "provider_response_error", "invalid_output", "provider_refusal",
    "truncated_output", "budget_request_too_large", "provider_concurrency_budget", "provider_hourly_token_budget",
    "provider_budget_unavailable", "unclassified",
}))


def list_provider_usage(
    db: Session, *, days: int = 30, limit: int = 25, offset: int = 0,
    data_access: DataAccessContext,
) -> AIProviderUsageResponse:
    days, limit, offset = max(1, min(days, 365)), max(1, min(limit, 100)), max(0, offset)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    event = AIUsageEvent
    name = func.coalesce(func.nullif(event.provider_name, ""), "Unknown historical provider")
    model = func.coalesce(func.nullif(event.model, ""), "Unknown model")
    identity = (event.provider_id, event.provider_version, name, model)
    filters = (event.created_at >= since, ai_usage_event_access_predicate(data_access))
    failed, successful = event.success.is_(False), event.success.is_(True)
    category = case((event.failure_category.in_(FAILURE_CATEGORIES), event.failure_category), else_="unclassified")
    aggregate = select(
        event.provider_id, event.provider_version, name.label("provider_name"), model.label("model"),
        func.count().label("total_requests"),
        func.count().filter(successful).label("successful_requests"),
        func.count().filter(failed).label("failed_requests"),
        func.coalesce(func.sum(event.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(event.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(event.total_tokens), 0).label("total_tokens"),
        func.count().filter(event.total_tokens.is_(None)).label("unknown_token_requests"),
        func.avg(event.latency_ms).filter(successful).label("average_latency_ms"),
        func.percentile_cont(0.95).within_group(event.latency_ms).filter(successful).label("p95_latency_ms"),
        func.count().filter(event.provider_io_outcome == "not_sent").label("not_sent_requests"),
        func.count().filter(event.provider_io_outcome == "ambiguous").label("ambiguous_requests"),
        func.count().filter(failed, event.failure_category.in_(DEADLINE_CATEGORIES)).label("deadline_failures"),
        func.max(event.created_at).label("last_request_at"),
        *(func.count().filter(failed, category == key).label(f"category_{key}") for key in FAILURE_CATEGORIES),
    ).where(*filters).group_by(*identity)
    # Count identities independently of pagination, including an empty/out-of-range page.
    groups = select(*identity).where(*filters).group_by(*identity).subquery()
    total = db.scalar(select(func.count()).select_from(groups)) or 0
    rows = db.execute(aggregate.order_by(
        func.count().desc(), name, event.provider_id.asc().nulls_first(),
        event.provider_version.asc().nulls_first(), model,
    ).limit(limit).offset(offset))
    items = []
    for row in rows:
        values = dict(row._mapping)
        categories = {key: values.pop(f"category_{key}") for key in FAILURE_CATEGORIES}
        values["failure_categories"] = {key: count for key, count in categories.items() if count}
        values["success_rate_pct"] = round(values["successful_requests"] / values["total_requests"] * 100, 2)
        for key in ("average_latency_ms", "p95_latency_ms"):
            if values[key] is not None:
                values[key] = round(float(values[key]), 2)
        items.append(AIProviderUsageRow(**values))
    return AIProviderUsageResponse(items=items, total=total, days=days, limit=limit, offset=offset)
