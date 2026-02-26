import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx
import redis
from celery.exceptions import MaxRetriesExceededError
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.connectors.rss import RSSConnector
from app.services.dedupe import content_hash, dedupe_key
from app.services.extraction import extract_canonical_url, extract_readable_text
from app.services.url_utils import is_fetchable_url, normalize_url
from app.tasks.celery_app import celery_app

settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


class ResponseTooLargeError(Exception):
    pass


@contextmanager
def db_session() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def domain_slot(domain: str, max_wait_seconds: int = 30):
    if not domain:
        yield
        return

    key = f"threatlens:domain:{domain}"
    deadline = time.monotonic() + max_wait_seconds

    acquired = False
    while time.monotonic() < deadline:
        try:
            current = redis_client.incr(key)
            redis_client.expire(key, 30)
        except redis.RedisError as exc:
            raise TimeoutError(f"domain slot redis error for {domain}: {exc}") from exc
        if current <= settings.per_domain_concurrency:
            acquired = True
            break
        redis_client.decr(key)
        time.sleep(0.2)

    if not acquired:
        raise TimeoutError(f"domain slot timeout for {domain}")

    try:
        yield
    finally:
        try:
            remaining = redis_client.decr(key)
            if remaining <= 0:
                redis_client.delete(key)
        except redis.RedisError:
            pass


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_feeds")
def dispatch_due_feeds():
    now = datetime.now(timezone.utc)
    queued = 0

    with db_session() as db:
        feeds = db.scalars(select(Feed).where(Feed.enabled.is_(True))).all()
        for feed in feeds:
            if _is_feed_due(feed, now):
                fetch_feed.delay(str(feed.id))
                queued += 1

    return {"queued": queued}


def _is_feed_due(feed: Feed, now: datetime) -> bool:
    if feed.fetch_mode == "schedule":
        return _is_scheduled_feed_due(feed, now)

    if feed.last_fetch_at is None:
        return True

    elapsed = (now - feed.last_fetch_at).total_seconds()
    return elapsed >= feed.fetch_interval_seconds


def _is_scheduled_feed_due(feed: Feed, now: datetime) -> bool:
    if not feed.schedule_cron:
        return False

    base = feed.last_fetch_at or now.replace(hour=0, minute=0, second=0, microsecond=0)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    if not croniter.is_valid(feed.schedule_cron):
        return False

    next_run = croniter(feed.schedule_cron, base).get_next(datetime)
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    return next_run <= now


@celery_app.task(name="app.tasks.feed_tasks.fetch_feed", bind=True)
def fetch_feed(self, feed_id: str):
    with db_session() as db:
        feed = db.scalar(select(Feed).where(Feed.id == uuid.UUID(feed_id)))
        if feed is None or not feed.enabled:
            return {"status": "skipped", "reason": "not_found_or_disabled", "feed_id": feed_id}

        headers: dict[str, str] = {}
        if feed.etag:
            headers["If-None-Match"] = feed.etag
        if feed.last_modified:
            headers["If-Modified-Since"] = feed.last_modified

        if not is_fetchable_url(feed.url, allow_private_network=settings.allow_private_network_fetch):
            _mark_feed_failure(db, feed, "unsafe_feed_url")
            return {"status": "error", "feed_id": feed_id}

        try:
            timeout = httpx.Timeout(
                connect=settings.feed_connect_timeout_seconds,
                read=settings.feed_read_timeout_seconds,
                write=settings.feed_read_timeout_seconds,
                pool=settings.feed_connect_timeout_seconds,
            )
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": settings.fetch_user_agent},
            ) as client:
                response = client.get(feed.url, headers=headers)
        except httpx.HTTPError as exc:
            try:
                raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
            except MaxRetriesExceededError:
                _mark_feed_failure(db, feed, f"network_error:{exc}")
                return {"status": "error", "feed_id": feed_id}

        now = datetime.now(timezone.utc)
        feed.last_fetch_at = now

        if response.status_code == 304:
            feed.last_success_at = now
            feed.error_count = 0
            feed.last_error = None
            db.add(feed)
            db.commit()
            return {"status": "not_modified", "feed_id": feed_id}

        if response.status_code != 200:
            _mark_feed_failure(db, feed, f"http_status:{response.status_code}")
            return {"status": "error", "feed_id": feed_id}

        connector = RSSConnector()
        parsed_items, _ = connector.poll({"body": response.content}, None)

        changed_item_ids: list[uuid.UUID] = []
        for parsed in parsed_items:
            item_url = parsed.url or ""
            key = dedupe_key(str(feed.id), parsed.guid, item_url, parsed.title, parsed.published_at)
            hash_value = content_hash(parsed.title, parsed.summary, item_url)

            item = db.scalar(select(Item).where(Item.dedupe_key == key))
            if item is None:
                item = Item(
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
                db.add(item)
                db.flush()
                changed_item_ids.append(item.id)
                continue

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
                changed_item_ids.append(item.id)

        feed.etag = response.headers.get("etag") or feed.etag
        feed.last_modified = response.headers.get("last-modified") or feed.last_modified
        feed.last_success_at = now
        feed.error_count = 0
        feed.last_error = None

        db.add(feed)
        db.commit()

    for item_id in changed_item_ids:
        fetch_article.delay(str(item_id))

    return {"status": "ok", "feed_id": feed_id, "new_or_updated_items": len(changed_item_ids)}


@celery_app.task(name="app.tasks.feed_tasks.fetch_article", bind=True)
def fetch_article(self, item_id: str):
    with db_session() as db:
        item = db.scalar(select(Item).where(Item.id == uuid.UUID(item_id)))
        if item is None:
            return {"status": "skipped", "reason": "not_found", "item_id": item_id}

        existing_article = db.scalar(select(Article).where(Article.item_id == item.id))
        if existing_article is not None and item.status == "content_fetched":
            return {"status": "skipped", "reason": "already_fetched", "item_id": item_id}

        target_url = item.canonical_url or item.url
        if not is_fetchable_url(target_url, allow_private_network=settings.allow_private_network_fetch):
            _store_article_error(
                db,
                item,
                final_url=target_url or "",
                http_status=0,
                content_type=None,
                fetch_ms=0,
                error="unsafe_article_url",
            )
            return {"status": "error", "item_id": item_id}

        domain = urlsplit(target_url).hostname or "unknown"
        start = time.perf_counter()

        try:
            with domain_slot(domain):
                timeout = httpx.Timeout(
                    connect=settings.article_connect_timeout_seconds,
                    read=settings.article_read_timeout_seconds,
                    write=settings.article_read_timeout_seconds,
                    pool=settings.article_connect_timeout_seconds,
                )
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    max_redirects=5,
                    headers={"User-Agent": settings.fetch_user_agent},
                ) as client:
                    with client.stream("GET", target_url) as response:
                        status_code = response.status_code
                        content_type = response.headers.get("content-type")
                        final_url = str(response.url)

                        body_chunks = []
                        body_size = 0
                        for chunk in response.iter_bytes():
                            body_size += len(chunk)
                            if body_size > settings.article_max_bytes:
                                raise ResponseTooLargeError("response body exceeds configured cap")
                            body_chunks.append(chunk)

                        body_bytes = b"".join(body_chunks)
        except (httpx.HTTPError, TimeoutError) as exc:
            try:
                raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
            except MaxRetriesExceededError:
                fetch_ms = int((time.perf_counter() - start) * 1000)
                _store_article_error(
                    db,
                    item,
                    final_url=target_url,
                    http_status=0,
                    content_type=None,
                    fetch_ms=fetch_ms,
                    error=f"network_or_rate_limit_error:{exc}",
                )
                return {"status": "error", "item_id": item_id}
        except ResponseTooLargeError as exc:
            fetch_ms = int((time.perf_counter() - start) * 1000)
            _store_article_error(
                db,
                item,
                final_url=target_url,
                http_status=0,
                content_type=None,
                fetch_ms=fetch_ms,
                error=str(exc),
            )
            return {"status": "error", "item_id": item_id}

        fetch_ms = int((time.perf_counter() - start) * 1000)

        if status_code != 200:
            _store_article_error(
                db,
                item,
                final_url=final_url,
                http_status=status_code,
                content_type=content_type,
                fetch_ms=fetch_ms,
                error=f"http_status:{status_code}",
            )
            return {"status": "error", "item_id": item_id}

        if "text/html" not in (content_type or "").lower():
            _store_article_error(
                db,
                item,
                final_url=final_url,
                http_status=status_code,
                content_type=content_type,
                fetch_ms=fetch_ms,
                error="non_html_response",
            )
            return {"status": "error", "item_id": item_id}

        html = body_bytes.decode("utf-8", errors="ignore")
        canonical = extract_canonical_url(html)
        if canonical:
            canonical = normalize_url(urljoin(final_url, canonical))

        extracted = extract_readable_text(html)

        article = db.scalar(select(Article).where(Article.item_id == item.id))
        if article is None:
            article = Article(item_id=item.id, final_url=final_url, http_status=status_code)

        article.final_url = final_url
        article.retrieved_at = datetime.now(timezone.utc)
        article.http_status = status_code
        article.content_type = content_type
        article.title_extracted = extracted.get("title")
        article.text = extracted.get("text")
        article.extraction_method = extracted.get("method")
        article.language = extracted.get("language")
        article.word_count = extracted.get("word_count")
        article.fetch_ms = fetch_ms
        article.error = extracted.get("error")

        if canonical:
            item.canonical_url = canonical

        if article.text:
            item.status = "content_fetched"
            item.last_error = None
        else:
            item.status = "error"
            item.last_error = article.error

        db.add(article)
        db.add(item)
        db.commit()

    return {"status": "ok", "item_id": item_id}


def _store_article_error(
    db: Session,
    item: Item,
    final_url: str,
    http_status: int,
    content_type: str | None,
    fetch_ms: int,
    error: str,
):
    article = db.scalar(select(Article).where(Article.item_id == item.id))
    if article is None:
        article = Article(item_id=item.id, final_url=final_url, http_status=http_status)

    article.final_url = final_url
    article.retrieved_at = datetime.now(timezone.utc)
    article.http_status = http_status
    article.content_type = content_type
    article.title_extracted = None
    article.language = None
    article.fetch_ms = fetch_ms
    article.error = error
    article.text = None
    article.extraction_method = "none"
    article.word_count = None

    item.status = "error"
    item.last_error = error

    db.add(article)
    db.add(item)
    db.commit()


def _mark_feed_failure(db: Session, feed: Feed, error: str):
    feed.last_fetch_at = datetime.now(timezone.utc)
    feed.error_count += 1
    feed.last_error = error
    db.add(feed)
    db.commit()
