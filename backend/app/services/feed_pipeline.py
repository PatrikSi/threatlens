import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.article_recovery import (
    article_fast_retryable_error_filter,
    article_fetch_repair_cutoff,
    article_fetch_repair_floor,
    article_soft_repair_cutoff,
    article_soft_retryable_error_filter,
)
from app.services.dedupe import content_hash, dedupe_key

logger = logging.getLogger(__name__)

FEED_FAILURE_BACKOFF_MIN_SECONDS = 300
FEED_FAILURE_BACKOFF_MAX_SECONDS = 21_600


def clear_feed_dispatch_claim(feed: Feed) -> None:
    feed.dispatch_claimed_at = None
    feed.dispatch_backoff_until = None


def claim_feed_for_dispatch(
    db: Session,
    *,
    feed_id: uuid.UUID,
    now: datetime,
    claim_seconds: int,
    is_feed_due: Callable[[Feed, datetime], bool],
) -> bool:
    feed = db.scalar(select(Feed).where(Feed.id == feed_id).with_for_update())
    if feed is None or not feed.enabled:
        return False

    backoff_until = feed.dispatch_backoff_until
    if backoff_until is not None:
        if backoff_until.tzinfo is None:
            backoff_until = backoff_until.replace(tzinfo=timezone.utc)
        if backoff_until > now:
            return False

    if not is_feed_due(feed, now):
        return False

    feed.dispatch_claimed_at = now
    feed.dispatch_backoff_until = now + timedelta(seconds=max(60, int(claim_seconds)))
    db.add(feed)
    db.flush()
    return True


def list_item_ids_missing_articles(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
    dispatch_after_seconds: int,
) -> list[uuid.UUID]:
    repair_cutoff = article_fetch_repair_cutoff(dispatch_after_seconds=dispatch_after_seconds, now=now)
    soft_repair_cutoff = article_soft_repair_cutoff(dispatch_after_seconds=dispatch_after_seconds, now=now)
    repair_floor = article_fetch_repair_floor(now=now)
    return list(
        db.scalars(
            select(Item.id)
            .outerjoin(Article, Article.item_id == Item.id)
            .where(
                or_(
                    and_(
                        Article.item_id.is_(None),
                        Item.first_seen_at >= repair_floor,
                        Item.first_seen_at <= repair_cutoff,
                    ),
                    and_(
                        Article.item_id.is_not(None),
                        Article.text.is_not(None),
                        Article.retrieved_at.is_not(None),
                        Article.retrieved_at < Item.updated_at,
                        Item.status != "content_fetched",
                        Item.updated_at >= repair_floor,
                        Item.updated_at <= repair_cutoff,
                    ),
                    and_(
                        Article.text.is_(None),
                        Article.retrieved_at.is_not(None),
                        Article.retrieved_at >= repair_floor,
                        or_(
                            and_(
                                Article.retrieved_at <= repair_cutoff,
                                article_fast_retryable_error_filter(),
                            ),
                            and_(
                                Article.retrieved_at <= soft_repair_cutoff,
                                article_soft_retryable_error_filter(),
                            ),
                        ),
                    ),
                ),
            )
            .order_by(Item.first_seen_at.asc())
            .limit(limit)
        ).all()
    )


def upsert_item_from_parsed(db: Session, feed: Feed, parsed) -> tuple[Item, bool, bool]:
    item_url = parsed.url or ""
    key = dedupe_key(str(feed.id), parsed.guid, item_url, parsed.title, parsed.published_at)
    hash_value = content_hash(parsed.title, parsed.summary, item_url)

    item = db.scalar(select(Item).where(Item.dedupe_key == key))
    if item is None:
        candidate = Item(
            feed_id=feed.id,
            source_guid=parsed.guid,
            url=item_url,
            title=parsed.title,
            summary=parsed.summary,
            published_at=parsed.published_at,
            dedupe_key=key,
            content_hash=hash_value,
            status="new",
            last_error=None,
        )
        if _insert_item_with_conflict_retry(db, candidate):
            return candidate, True, True

        item = db.scalar(select(Item).where(Item.dedupe_key == key))
        if item is None:
            raise RuntimeError(f"item conflict recovery failed for dedupe key {key}")

    if item.content_hash != hash_value:
        item.url = item_url or item.url
        item.title = parsed.title
        item.summary = parsed.summary
        item.published_at = parsed.published_at
        item.content_hash = hash_value
        item.status = "new"
        item.last_error = None
        db.add(item)
        db.flush()
        return item, True, False

    return item, False, False


def mark_feed_failure(db: Session, feed: Feed, error: str) -> int:
    now = datetime.now(timezone.utc)
    feed.last_fetch_at = now
    feed.error_count += 1
    feed.last_error = error
    feed.dispatch_claimed_at = None
    feed.dispatch_backoff_until = now + timedelta(seconds=_sustained_feed_failure_backoff_seconds(feed))
    db.add(feed)
    db.flush()
    return feed.error_count


def _sustained_feed_failure_backoff_seconds(feed: Feed) -> int:
    raw_interval = getattr(feed, "fetch_interval_seconds", 1800)
    try:
        interval_seconds = int(raw_interval)
    except (TypeError, ValueError):
        interval_seconds = 1800
    interval_seconds = max(60, interval_seconds)

    failure_count = max(1, int(feed.error_count or 0))
    multiplier = 2 ** min(failure_count - 1, 5)
    return min(
        FEED_FAILURE_BACKOFF_MAX_SECONDS,
        max(FEED_FAILURE_BACKOFF_MIN_SECONDS, interval_seconds) * multiplier,
    )


def _insert_item_with_conflict_retry(db: Session, item: Item) -> bool:
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
        return True
    except IntegrityError:
        logger.info("dedupe_conflict_detected dedupe_key=%s", item.dedupe_key)
        return False
