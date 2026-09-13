from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item

RETRYABLE_ARTICLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
RETRYABLE_ARTICLE_ERRORS = (
    "coordination_unavailable",
    "network_or_rate_limit_error",
)
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


@dataclass(frozen=True, slots=True)
class ArticleFeedState:
    feed_id: uuid.UUID
    enabled: bool


def lock_article_feed(db: Session, item_id: uuid.UUID) -> ArticleFeedState | None:
    """Fence feed mutations before claiming an item; recheck its feed after claiming."""
    feed = db.execute(
        select(Feed.id, Feed.enabled)
        .join(Item, Item.feed_id == Feed.id)
        .where(Item.id == item_id)
        .with_for_update(of=Feed, read=True)
    ).one_or_none()
    return ArticleFeedState(feed.id, feed.enabled) if feed else None


def article_fetch_repair_cutoff(
    *, dispatch_after_seconds: int, now: datetime | None = None
) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    return current_time - timedelta(seconds=max(0, int(dispatch_after_seconds)))


def article_fetch_repair_floor(*, now: datetime | None = None) -> datetime:
    _ = now
    return _EARLIEST_ARTICLE_REPAIR_AT


def article_soft_repair_cutoff(
    *, dispatch_after_seconds: int, now: datetime | None = None
) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    fast_retry_cutoff = article_fetch_repair_cutoff(
        dispatch_after_seconds=dispatch_after_seconds,
        now=current_time,
    )
    return min(fast_retry_cutoff, current_time - ARTICLE_REPAIR_SOFT_RETRY_DELAY)


def article_fast_retryable_error_filter():
    retryable_http_errors = [
        f"http_status:{status_code}"
        for status_code in sorted(RETRYABLE_ARTICLE_HTTP_STATUSES)
    ]
    return or_(
        Article.error.in_(retryable_http_errors),
        Article.error.in_(RETRYABLE_ARTICLE_ERRORS),
        *[
            Article.error.like(f"{prefix}%")
            for prefix in RETRYABLE_ARTICLE_ERROR_PREFIXES
        ],
    )


def article_soft_retryable_error_filter():
    return or_(
        Article.error.in_(SOFT_REPAIRABLE_ARTICLE_ERRORS),
        *[
            Article.error.like(f"{prefix}%")
            for prefix in SOFT_REPAIRABLE_ARTICLE_ERROR_PREFIXES
        ],
    )


def is_fast_retryable_article_error(error: str | None) -> bool:
    if not error:
        return False
    return (
        error
        in {
            f"http_status:{status_code}"
            for status_code in RETRYABLE_ARTICLE_HTTP_STATUSES
        }
        or error in RETRYABLE_ARTICLE_ERRORS
        or any(error.startswith(prefix) for prefix in RETRYABLE_ARTICLE_ERROR_PREFIXES)
    )


def is_soft_retryable_article_error(error: str | None) -> bool:
    if not error:
        return False
    return error in SOFT_REPAIRABLE_ARTICLE_ERRORS or any(
        error.startswith(prefix) for prefix in SOFT_REPAIRABLE_ARTICLE_ERROR_PREFIXES
    )


def article_repair_predicate(
    *, dispatch_after_seconds: int, now: datetime | None = None
):
    """One eligibility policy for legacy discovery and durable repair admission."""
    from sqlalchemy import and_, exists

    cutoff = article_fetch_repair_cutoff(
        dispatch_after_seconds=dispatch_after_seconds, now=now
    )
    soft_cutoff = article_soft_repair_cutoff(
        dispatch_after_seconds=dispatch_after_seconds, now=now
    )
    floor = article_fetch_repair_floor(now=now)
    pending_article = or_(
        and_(
            Article.item_id.is_(None),
            Item.first_seen_at >= floor,
            Item.first_seen_at <= cutoff,
        ),
        and_(
            Article.item_id.is_not(None),
            Article.content_purged_at.is_(None),
            Article.text.is_not(None),
            Article.retrieved_at.is_not(None),
            Article.retrieved_at < Item.updated_at,
            Item.status != "content_fetched",
            Item.updated_at >= floor,
            Item.updated_at <= cutoff,
        ),
        and_(
            Article.content_purged_at.is_(None),
            Article.text.is_(None),
            Article.retrieved_at.is_not(None),
            Article.retrieved_at >= floor,
            or_(
                and_(
                    Article.retrieved_at <= cutoff,
                    article_fast_retryable_error_filter(),
                ),
                and_(
                    Article.retrieved_at <= soft_cutoff,
                    article_soft_retryable_error_filter(),
                ),
            ),
        ),
    )
    enabled_feed = exists(
        select(Feed.id)
        .where(Feed.id == Item.feed_id, Feed.enabled.is_(True))
        .correlate(Item)
    )
    return and_(enabled_feed, pending_article)
