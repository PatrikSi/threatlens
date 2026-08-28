from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging_config import verbose_logging_enabled
from app.schemas.operations import (
    OperationsBacklogSnapshot,
    OperationsComponentCheck,
    OperationsIssue,
    OperationsStorageIndicator,
)


logger = logging.getLogger("app.services.operations")
_T = TypeVar("_T")


def safe_probe(name: str, loader: Callable[[], _T], fallback: _T) -> _T:
    try:
        return loader()
    except Exception as exc:
        log_probe_failure(name, exc)
        return fallback


def safe_db_probe(db: Session, name: str, loader: Callable[[], _T], fallback: _T) -> _T:
    try:
        with db.begin_nested():
            return loader()
    except Exception as exc:
        log_probe_failure(name, exc)
        return fallback


def log_probe_failure(name: str, exc: Exception) -> None:
    settings = get_settings()
    logger.warning(
        "operations_probe_failed probe=%s error_type=%s",
        name,
        type(exc).__name__,
        exc_info=verbose_logging_enabled(settings),
    )


def issue(
    code: str,
    severity: str,
    component: str,
    summary: str,
    effect: str,
    recommended_action: str,
) -> OperationsIssue:
    return OperationsIssue(
        code=code,
        severity=severity,
        component=component,
        summary=summary,
        effect=effect,
        recommended_action=recommended_action,
    )


def overall_status(
    components: list[OperationsComponentCheck],
    storage: list[OperationsStorageIndicator],
    backlogs: list[OperationsBacklogSnapshot],
    issues: list[OperationsIssue],
) -> str:
    if any(entry.severity == "critical" for entry in issues):
        return "critical"
    statuses = [entry.status for entry in (*components, *storage, *backlogs)]
    if any(status in {"degraded", "unavailable", "unknown"} for status in statuses) or issues:
        return "degraded"
    return "healthy"


def ordered_issues(issues: list[OperationsIssue]) -> list[OperationsIssue]:
    severity_order = {"critical": 0, "warning": 1}
    unique = {entry.code: entry for entry in issues}
    return sorted(
        unique.values(),
        key=lambda entry: (severity_order[entry.severity], entry.component, entry.code),
    )


def seconds_since(now: datetime, timestamp: datetime | None) -> int | None:
    if timestamp is None:
        return None
    return max(0, int((as_utc(now) - as_utc(timestamp)).total_seconds()))


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
