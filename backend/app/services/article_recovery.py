from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.models.article import Article

RETRYABLE_ARTICLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
RETRYABLE_ARTICLE_ERROR_PREFIXES = (
    "coordination_unavailable:",
    "network_or_rate_limit_error:",
)
SOFT_REPAIRABLE_ARTICLE_ERRORS = (
    "article_fetch_failed",
    "no_extractor_succeeded",
    "non_html_response",
    "response body exceeds configured cap",
)
SOFT_REPAIRABLE_ARTICLE_ERROR_PREFIXES = ("readability_error:",)
ARTICLE_REPAIR_SOFT_RETRY_DELAY = timedelta(hours=1)
_EARLIEST_ARTICLE_REPAIR_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)


def article_fetch_repair_cutoff(*, dispatch_after_seconds: int, now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    return current_time - timedelta(seconds=max(0, int(dispatch_after_seconds)))


def article_fetch_repair_floor(*, now: datetime | None = None) -> datetime:
    _ = now
    return _EARLIEST_ARTICLE_REPAIR_AT


def article_soft_repair_cutoff(*, dispatch_after_seconds: int, now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    fast_retry_cutoff = article_fetch_repair_cutoff(
        dispatch_after_seconds=dispatch_after_seconds,
        now=current_time,
    )
    return min(fast_retry_cutoff, current_time - ARTICLE_REPAIR_SOFT_RETRY_DELAY)


def article_fast_retryable_error_filter():
    retryable_http_errors = [f"http_status:{status_code}" for status_code in sorted(RETRYABLE_ARTICLE_HTTP_STATUSES)]
    return or_(
        Article.error.in_(retryable_http_errors),
        *[Article.error.like(f"{prefix}%") for prefix in RETRYABLE_ARTICLE_ERROR_PREFIXES],
    )


def article_soft_retryable_error_filter():
    return or_(
        Article.error.in_(SOFT_REPAIRABLE_ARTICLE_ERRORS),
        *[Article.error.like(f"{prefix}%") for prefix in SOFT_REPAIRABLE_ARTICLE_ERROR_PREFIXES],
    )


def is_fast_retryable_article_error(error: str | None) -> bool:
    if not error:
        return False
    return error in {f"http_status:{status_code}" for status_code in RETRYABLE_ARTICLE_HTTP_STATUSES} or any(
        error.startswith(prefix) for prefix in RETRYABLE_ARTICLE_ERROR_PREFIXES
    )


def is_soft_retryable_article_error(error: str | None) -> bool:
    if not error:
        return False
    return error in SOFT_REPAIRABLE_ARTICLE_ERRORS or any(
        error.startswith(prefix) for prefix in SOFT_REPAIRABLE_ARTICLE_ERROR_PREFIXES
    )
