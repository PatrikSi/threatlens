from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.safe_fetch import RedirectError, SafeFetchError
from app.tasks.feed_task_coordination import CoordinationUnavailableError


class ResponseTooLargeError(Exception):
    pass


class FeedResponseTooLargeError(Exception):
    pass


def exception_type_name(exc: BaseException) -> str:
    return exc.__class__.__name__


def safe_feed_fetch_error_code(exc: BaseException) -> str:
    if isinstance(exc, CoordinationUnavailableError):
        return "coordination_unavailable"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "network_timeout"
    if isinstance(exc, RedirectError):
        return "redirect_error"
    if isinstance(exc, SafeFetchError):
        return "unsafe_fetch_error"
    return "network_error"


def safe_article_fetch_error_code(exc: BaseException) -> str:
    if isinstance(exc, CoordinationUnavailableError):
        return "coordination_unavailable"
    if isinstance(exc, ResponseTooLargeError):
        return "response_too_large"
    return "network_or_rate_limit_error"


def claim_item_processing_target(db: Session, *, item_id: uuid.UUID) -> tuple[Item | None, str | None]:
    item = db.scalar(select(Item).where(Item.id == item_id).with_for_update(skip_locked=True))
    if item is not None:
        return item, None

    unlocked_item = db.scalar(select(Item).where(Item.id == item_id))
    if unlocked_item is None:
        return None, "not_found"
    return None, "already_running"


def resolve_feed_runtime_url(feed: Feed) -> tuple[str | None, str | None]:
    if feed.url_decryption_error:
        return None, feed.url_decryption_error
    feed_url = feed.url.strip()
    if not feed_url:
        return None, "Feed URL is empty"
    return feed_url, None


def feed_url_digest_still_current(db: Session, *, feed_id: uuid.UUID, expected_url_digest: str | None) -> bool:
    current_url_digest = db.scalar(select(Feed.url_digest).where(Feed.id == feed_id))
    return current_url_digest == expected_url_digest


def article_freshness_token_value(
    article_id: uuid.UUID | None,
    retrieved_at: datetime | None,
) -> tuple[str | None, str | None]:
    if article_id is None or retrieved_at is None:
        return None, None

    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
    return str(article_id), retrieved_at.isoformat()


def article_freshness_token(article: Article | None) -> tuple[str | None, str | None]:
    if article is None:
        return None, None
    return article_freshness_token_value(article.id, article.retrieved_at)


def load_article_freshness_token(db: Session, *, item_id: uuid.UUID) -> tuple[str | None, str | None]:
    row = db.execute(select(Article.id, Article.retrieved_at).where(Article.item_id == item_id)).one_or_none()
    if row is None:
        return None, None

    article_id, retrieved_at = row
    return article_freshness_token_value(article_id, retrieved_at)


def article_was_refetched(
    db: Session,
    *,
    item_id: uuid.UUID,
    expected_token: tuple[str | None, str | None],
) -> bool:
    return load_article_freshness_token(db, item_id=item_id) != expected_token
