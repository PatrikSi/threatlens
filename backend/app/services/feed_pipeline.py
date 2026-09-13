import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.article_recovery import article_repair_predicate
from app.services.dedupe import content_hash, dedupe_key
from app.services.classification_recovery import require_item_classification
from app.services.url_utils import extract_url_domain, normalize_url

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
    next_fetch_at: Callable[[Feed, datetime], datetime | None],
) -> bool:
    feed = db.scalar(select(Feed).where(Feed.id == feed_id).with_for_update())
    if feed is None or not feed.enabled:
        return False

    backoff_until = feed.dispatch_backoff_until
    if backoff_until is not None:
        if backoff_until.tzinfo is None:
            backoff_until = backoff_until.replace(tzinfo=timezone.utc)
        if backoff_until > now:
            feed.next_fetch_at = backoff_until
            db.add(feed)
            db.flush()
            return False

    if not is_feed_due(feed, now):
        feed.next_fetch_at = next_fetch_at(feed, now)
        db.add(feed)
        db.flush()
        return False

    feed.dispatch_claimed_at = now
    feed.dispatch_backoff_until = now + timedelta(seconds=max(60, int(claim_seconds)))
    feed.next_fetch_at = None
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
    return list(db.scalars(select(Item.id).outerjoin(Article, Article.item_id == Item.id)
        .where(article_repair_predicate(dispatch_after_seconds=dispatch_after_seconds, now=now))
        .order_by(Item.first_seen_at.asc()).limit(limit)).all())


def upsert_item_from_parsed(db: Session, feed: Feed, parsed) -> tuple[Item, bool, bool]:
    item_url = normalize_url(parsed.url) or ""
    item_domain = extract_url_domain(item_url)
    key = dedupe_key(str(feed.id), parsed.guid, item_url, parsed.title, parsed.published_at)
    hash_value = content_hash(parsed.title, parsed.summary, item_url)

    item = db.scalar(select(Item).where(Item.dedupe_key == key))
    if item is None:
        candidate = Item(
            feed_id=feed.id,
            source_guid=parsed.guid,
            url=item_url,
            url_domain=item_domain,
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
        if item.title != parsed.title or item.summary != parsed.summary:
            require_item_classification(item)
        item.url = item_url or item.url
        item.url_domain = item_domain or item.url_domain
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
    feed.next_fetch_at = feed.dispatch_backoff_until
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
