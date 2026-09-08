from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.config import get_settings


@dataclass(frozen=True)
class LifecycleOptionDefinition:
    key: str
    label: str
    description: str
    default: bool = True


@dataclass(frozen=True)
class LifecycleTargetDefinition:
    key: str
    label: str
    category: str
    description: str
    cutoff_description: str
    default_days: Callable[[], int]
    enabled_by_default: bool = True
    min_retention_days: int = 1
    max_retention_days: int = 3_650
    available_options: tuple[LifecycleOptionDefinition, ...] = ()

    @property
    def action_description(self) -> str:
        if self.key == "article_content":
            return "Purges extracted payload fields in place; source and deduplication records remain."
        return (
            "Permanently deletes eligible primary records. The run cap counts primary "
            "records, and each batch separately enforces a fixed dependent-row ceiling."
        )

    def default_retention_days(self) -> int:
        return max(
            self.min_retention_days,
            min(self.max_retention_days, int(self.default_days())),
        )

    def default_options(self) -> dict[str, bool]:
        return {option.key: option.default for option in self.available_options}


def _setting(name: str, fallback: int) -> Callable[[], int]:
    return lambda: int(getattr(get_settings(), name, fallback))


ARTICLE_OPTIONS = (
    LifecycleOptionDefinition(
        "protect_starred",
        "Keep starred articles",
        "Retain content for any article starred by a user.",
    ),
    LifecycleOptionDefinition(
        "protect_notes",
        "Keep articles with notes",
        "Retain content when a user note is attached to the article.",
    ),
    LifecycleOptionDefinition(
        "protect_investigations",
        "Keep investigation evidence",
        "Retain content referenced by an investigation.",
    ),
    LifecycleOptionDefinition(
        "protect_reports",
        "Keep report sources",
        "Retain content referenced by a generated report.",
    ),
    LifecycleOptionDefinition(
        "protect_active_alerts",
        "Keep active alert evidence",
        "Retain content referenced by a non-closed alert occurrence.",
    ),
)


TARGETS = (
    LifecycleTargetDefinition(
        "article_content",
        "Fetched article content",
        "intelligence",
        "Remove extracted article payloads while preserving source records and deduplication markers.",
        "Article publication time, falling back to first-seen time.",
        lambda: 365,
        enabled_by_default=False,
        min_retention_days=7,
        available_options=ARTICLE_OPTIONS,
    ),
    LifecycleTargetDefinition(
        "closed_alert_history",
        "Closed alert history",
        "detection",
        "Remove closed, metric-aggregated alert occurrences and their activity.",
        "Alert closure time.",
        lambda: 365,
    ),
    LifecycleTargetDefinition(
        "alert_activity_history",
        "Alert activity history",
        "detection",
        "Remove old alert timeline activity while retaining each occurrence's created baseline.",
        "Activity creation time.",
        lambda: 365,
    ),
    LifecycleTargetDefinition(
        "alert_evaluation_history",
        "Alert evaluation history",
        "detection",
        "Remove terminal alert evaluation requests and their processing activity.",
        "Evaluation completion time.",
        lambda: 30,
    ),
    LifecycleTargetDefinition(
        "alert_metrics",
        "Alert metrics",
        "detection",
        "Remove historical alert metric buckets and their policy cohorts.",
        "Metric bucket start time.",
        lambda: 730,
    ),
    LifecycleTargetDefinition(
        "integration_run_history",
        "Integration run history",
        "integrations",
        "Remove completed connector test and synchronization run history.",
        "Integration run completion time.",
        _setting("integration_run_retention_days", 180),
    ),
    LifecycleTargetDefinition(
        "integration_delivery_history",
        "Integration delivery history",
        "integrations",
        "Remove metric-aggregated terminal deliveries and eligible legacy webhook deliveries after retry and event-lineage safety checks.",
        "Terminal delivery time.",
        _setting("integration_delivery_retention_days", 90),
    ),
    LifecycleTargetDefinition(
        "integration_event_history",
        "Integration event history",
        "integrations",
        "Remove routed or dead-letter events after all dependent deliveries are gone.",
        "Event creation time.",
        _setting("integration_event_retention_days", 30),
    ),
    LifecycleTargetDefinition(
        "integration_metrics",
        "Integration metrics",
        "integrations",
        "Remove connector delivery metric buckets and their policy cohorts.",
        "Metric bucket start time.",
        _setting("integration_metrics_retention_days", 730),
    ),
    LifecycleTargetDefinition(
        "audit_logs",
        "Audit logs",
        "governance",
        "Remove audit records and their captured access-policy lineage.",
        "Audit event creation time.",
        _setting("audit_log_retention_days", 730),
        min_retention_days=30,
    ),
    LifecycleTargetDefinition(
        "action_approval_history",
        "Action approval history",
        "governance",
        "Remove terminal or expired approval requests and dependent receipts.",
        "Approval request creation time.",
        _setting("action_approval_retention_days", 730),
        min_retention_days=30,
    ),
    LifecycleTargetDefinition(
        "ai_task_history",
        "AI task history",
        "governance",
        "Remove terminal AI task runs and settled provider attempt receipts not pinned by reports or approvals.",
        "Task completion or provider receipt update time.",
        _setting("ai_task_history_retention_days", 180),
    ),
    LifecycleTargetDefinition(
        "ai_usage_history",
        "AI usage history",
        "governance",
        "Remove AI usage and cost telemetry after its retention period.",
        "Usage event creation time.",
        _setting("ai_usage_retention_days", 730),
    ),
    LifecycleTargetDefinition(
        "tag_feedback_history",
        "Tag feedback history",
        "governance",
        "Remove historical user feedback signals used by tagging workflows.",
        "Feedback event creation time.",
        _setting("tag_feedback_retention_days", 730),
    ),
    LifecycleTargetDefinition(
        "inactive_auth_sessions",
        "Inactive authentication sessions",
        "security",
        "Remove revoked or expired browser session records; active sessions are never selected.",
        "Revocation time, or the earliest idle or absolute expiry.",
        _setting("auth_session_retention_days", 30),
    ),
    LifecycleTargetDefinition(
        "system_health_samples",
        "System health samples",
        "system",
        "Remove historical health telemetry while retaining current diagnostics.",
        "Health sample time.",
        _setting("operations_health_history_retention_days", 30),
    ),
)

TARGET_BY_KEY = {target.key: target for target in TARGETS}


def target_definition(target_key: str) -> LifecycleTargetDefinition:
    try:
        return TARGET_BY_KEY[target_key]
    except KeyError as exc:
        raise ValueError("Unknown lifecycle target.") from exc


def normalized_options(
    definition: LifecycleTargetDefinition,
    values: dict | None,
) -> dict[str, bool]:
    supplied = values or {}
    unknown = set(supplied) - {option.key for option in definition.available_options}
    if unknown:
        raise ValueError("Unsupported lifecycle safeguard option.")
    return {
        option.key: bool(supplied.get(option.key, option.default))
        for option in definition.available_options
    }


def next_scheduled_at(
    *,
    cadence: str,
    hour_utc: int,
    weekday: int | None,
    after: datetime | None = None,
) -> datetime:
    current = (after or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = current.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if cadence == "daily":
        return candidate if candidate > current else candidate + timedelta(days=1)
    if cadence != "weekly" or weekday is None:
        raise ValueError("Weekly lifecycle schedules require a weekday.")
    candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
    return candidate if candidate > current else candidate + timedelta(days=7)


__all__ = [
    "LifecycleOptionDefinition",
    "LifecycleTargetDefinition",
    "TARGETS",
    "TARGET_BY_KEY",
    "next_scheduled_at",
    "normalized_options",
    "target_definition",
]
