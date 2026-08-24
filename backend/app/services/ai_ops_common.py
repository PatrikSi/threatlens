from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

AI_TASK_TYPE_ITEM_ENRICHMENT = "item_enrichment"
AI_TASK_TYPE_DAILY_BRIEF = "daily_brief"
AI_TASK_TYPE_REPORT = "report"
AI_TASK_TYPE_REPORT_SUPERSEDED = "report_superseded"
AI_TASK_TYPE_CONNECTION_TEST = "connection_test"
AI_TASK_TYPE_REPROCESS = "reprocess"

AI_TRIGGER_AUTO = "auto"
AI_TRIGGER_MANUAL = "manual"
AI_TRIGGER_SCHEDULED = "scheduled"

AI_DAILY_BRIEF_BACKFILL_SCOPE = "daily_brief_backfill"
AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY = "parent_progress_eligible"
AI_PROVIDER_CLAIM_METADATA_KEY = "provider_claim"
AI_PROVIDER_CLAIM_ITEM_ENRICHMENT = "item_ai_enrichment"
AI_PROVIDER_CLAIM_DAILY_BRIEF = "daily_brief"

AI_STATUS_QUEUED = "queued"
AI_STATUS_RUNNING = "running"
AI_STATUS_READY = "ready"
AI_STATUS_ERROR = "error"
AI_STATUS_SKIPPED = "skipped"
AI_TERMINAL_STATUSES = {AI_STATUS_READY, AI_STATUS_ERROR, AI_STATUS_SKIPPED}

AI_TASK_NAMES = {
    "app.tasks.feed_tasks.generate_item_ai_enrichment": AI_TASK_TYPE_ITEM_ENRICHMENT,
    "app.tasks.feed_tasks.reprocess_recent_ai_items": AI_TASK_TYPE_REPROCESS,
    "app.tasks.feed_tasks.backfill_daily_ai_briefs": AI_TASK_TYPE_REPROCESS,
    "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation": AI_TASK_TYPE_DAILY_BRIEF,
    "app.tasks.feed_tasks.generate_intelligence_report": AI_TASK_TYPE_REPORT,
}
AI_CONNECTION_TEST_BLOCKING_TASK_TYPES = {
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_REPORT,
    AI_TASK_TYPE_REPROCESS,
}

STALE_AI_RUN_GRACE_PERIOD = timedelta(minutes=10)
STALE_AI_RUN_FALLBACK_GRACE_PERIOD = timedelta(hours=1)

INELIGIBLE_REASONS = {
    "ai_disabled",
    "ai_not_configured",
    "feature_disabled",
    "item_not_found",
    "no_article",
    "no_article_text",
    "not_eligible",
    "not_found",
    "auto_enrich_disabled",
    "invalid_item_id",
}


@dataclass(frozen=True)
class AIConnectionTestWorkload:
    running_task_count: int
    queued_task_count: int

    @property
    def has_active_work(self) -> bool:
        return self.running_task_count > 0 or self.queued_task_count > 0


def _extract_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _merge_metadata(
    current: dict[str, Any] | None, updates: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in updates.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio))))
    return float(ordered[index])


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
