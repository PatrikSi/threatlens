import time
import uuid
import secrets
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx
import redis
from celery.exceptions import MaxRetriesExceededError
from croniter import croniter
import feedparser
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.services.connectors.rss import RSSConnector
from app.services.algorithm_tags import sync_item_algorithm_tags
from app.services.classification import classify_item_content
from app.services.dedupe import content_hash, dedupe_key
from app.services.extraction import extract_canonical_url, extract_readable_text
from app.services.feed_probe import FeedProbeError, probe_feed_metadata
from app.services.ioc_extraction import extract_iocs
from app.services.tag_feedback import load_feedback_adjustments
from app.services.safe_fetch import RedirectError, SafeFetchError, safe_stream_with_redirects
from app.services.url_utils import is_fetchable_url, normalize_url
from app.tasks.celery_app import celery_app

settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
logger = logging.getLogger(__name__)


class ResponseTooLargeError(Exception):
    pass


class FeedResponseTooLargeError(Exception):
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
    degraded_mode = False
    while time.monotonic() < deadline:
        try:
            current = redis_client.incr(key)
            redis_client.expire(key, 30)
        except redis.RedisError as exc:
            logger.warning("domain_slot_unavailable domain=%s error=%s", domain, exc)
            degraded_mode = True
            break
        if current <= settings.per_domain_concurrency:
            acquired = True
            break
        try:
            redis_client.decr(key)
        except redis.RedisError as exc:
            logger.warning("domain_slot_counter_revert_failed domain=%s error=%s", domain, exc)
            degraded_mode = True
            break
        time.sleep(0.2)

    if degraded_mode:
        yield
        return

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


@contextmanager
def feed_lock(feed_id: str, ttl_seconds: int = 900):
    key = f"threatlens:feed:lock:{feed_id}"
    token = secrets.token_hex(16)

    acquired = False
    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
    except redis.RedisError:
        # Best-effort locking: continue if Redis is unavailable.
        acquired = True

    if not acquired:
        yield False
        return

    try:
        yield True
    finally:
        try:
            redis_client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                token,
            )
        except redis.RedisError:
            pass


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_feeds")
def dispatch_due_feeds():
    now = datetime.now(timezone.utc)
    queued = 0

    with db_session() as db:
        feeds = db.scalars(select(Feed).where(Feed.enabled.is_(True))).all()
        for feed in feeds:
            if queued >= settings.dispatch_due_feeds_batch_size:
                break
            if _is_feed_due(feed, now):
                fetch_feed.delay(str(feed.id))
                queued += 1

    return {"queued": queued}


@celery_app.task(name="app.tasks.feed_tasks.dispatch_unclassified_items")
def dispatch_unclassified_items():
    queued = 0
    with db_session() as db:
        item_ids = db.scalars(
            select(Item.id)
            .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
            .where(ItemClassification.item_id.is_(None))
            .order_by(Item.first_seen_at.asc())
            .limit(settings.dispatch_unclassified_items_batch_size)
        ).all()

    for item_id in item_ids:
        classify_item.delay(str(item_id))
        queued += 1

    return {"queued": queued}


@celery_app.task(name="app.tasks.feed_tasks.dispatch_items_missing_iocs")
def dispatch_items_missing_iocs():
    queued = 0
    with db_session() as db:
        item_ids = db.scalars(
            select(Item.id)
            .outerjoin(ItemIOC, ItemIOC.item_id == Item.id)
            .where(ItemIOC.item_id.is_(None))
            .order_by(Item.first_seen_at.asc())
            .limit(settings.dispatch_items_missing_iocs_batch_size)
        ).all()

    for item_id in item_ids:
        extract_item_iocs.delay(str(item_id))
        queued += 1

    return {"queued": queued}


@celery_app.task(name="app.tasks.feed_tasks.dispatch_feed_metadata_backfill")
def dispatch_feed_metadata_backfill():
    queued = 0
    with db_session() as db:
        feeds = db.scalars(
            select(Feed)
            .where(Feed.enabled.is_(True))
            .order_by(Feed.created_at.asc())
            .limit(settings.dispatch_feed_metadata_scan_limit)
        ).all()

    for feed in feeds:
        if queued >= settings.dispatch_feed_metadata_queue_limit:
            break
        if not _needs_metadata_backfill(feed):
            continue
        backfill_feed_metadata.delay(str(feed.id))
        queued += 1

    return {"queued": queued}


@celery_app.task(name="app.tasks.feed_tasks.record_beat_heartbeat")
def record_beat_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    try:
        redis_client.set(settings.beat_heartbeat_key, now, ex=settings.beat_heartbeat_ttl_seconds)
    except redis.RedisError as exc:
        logger.warning("beat_heartbeat_write_failed error=%s", exc)
        return {"status": "error", "reason": "redis_unavailable"}
    return {"status": "ok", "at": now}


@celery_app.task(name="app.tasks.feed_tasks.backfill_feed_metadata")
def backfill_feed_metadata(feed_id: str):
    with feed_lock(feed_id) as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "already_fetching", "feed_id": feed_id}

        with db_session() as db:
            feed = db.scalar(select(Feed).where(Feed.id == uuid.UUID(feed_id)))
            if feed is None or not feed.enabled:
                return {"status": "skipped", "reason": "not_found_or_disabled", "feed_id": feed_id}

            if not _needs_metadata_backfill(feed):
                return {"status": "skipped", "reason": "metadata_present", "feed_id": feed_id}

            try:
                metadata = probe_feed_metadata(feed.url)
            except FeedProbeError as exc:
                return {"status": "error", "feed_id": feed_id, "reason": str(exc)}

            changed = _apply_probe_metadata(feed, metadata)
            if changed:
                db.add(feed)
                db.commit()
            return {"status": "ok", "feed_id": feed_id, "updated": changed}


def _is_feed_due(feed: Feed, now: datetime) -> bool:
    if feed.fetch_mode == "schedule":
        return _is_scheduled_feed_due(feed, now)

    if feed.last_fetch_at is None:
        return True

    last_fetch_at = feed.last_fetch_at
    if last_fetch_at.tzinfo is None:
        last_fetch_at = last_fetch_at.replace(tzinfo=timezone.utc)

    raw_interval = getattr(feed, "fetch_interval_seconds", 1800)
    try:
        interval_seconds = int(raw_interval)
    except (TypeError, ValueError):
        interval_seconds = 1800
    interval_seconds = max(60, interval_seconds)

    elapsed = (now - last_fetch_at).total_seconds()
    return elapsed >= interval_seconds


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


def _needs_metadata_backfill(feed: Feed) -> bool:
    placeholder_name = not feed.name.strip() or feed.name.strip() == feed.url.strip()
    return placeholder_name or not feed.site_url


def _apply_probe_metadata(feed: Feed, metadata) -> bool:
    changed = False
    is_placeholder_name = not feed.name.strip() or feed.name.strip() == feed.url.strip()

    if is_placeholder_name and metadata.name:
        feed.name = metadata.name
        changed = True
    if not feed.description and metadata.description:
        feed.description = metadata.description
        changed = True
    if not feed.site_url and metadata.site_url:
        feed.site_url = metadata.site_url
        changed = True
    if not feed.language and metadata.language:
        feed.language = metadata.language
        changed = True
    if not feed.etag and metadata.etag:
        feed.etag = metadata.etag
        changed = True
    if not feed.last_modified and metadata.last_modified:
        feed.last_modified = metadata.last_modified
        changed = True

    return changed


def _backfill_feed_metadata_from_body(feed: Feed, body: bytes) -> bool:
    parsed = feedparser.parse(body)
    metadata = parsed.feed if hasattr(parsed, "feed") else {}

    changed = False
    feed_title = _clean_text(metadata.get("title"))
    description = _clean_text(metadata.get("subtitle") or metadata.get("description"))
    site_url = _clean_text(metadata.get("link"))
    language = _clean_text(metadata.get("language"))

    if (not feed.name.strip() or feed.name.strip() == feed.url.strip()) and feed_title:
        feed.name = feed_title
        changed = True
    if not feed.description and description:
        feed.description = description
        changed = True
    if not feed.site_url and site_url:
        feed.site_url = site_url
        changed = True
    if not feed.language and language:
        feed.language = language
        changed = True

    return changed


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@celery_app.task(name="app.tasks.feed_tasks.fetch_feed", bind=True)
def fetch_feed(self, feed_id: str):
    with feed_lock(feed_id) as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "already_fetching", "feed_id": feed_id}

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
                with httpx.Client(timeout=timeout, headers={"User-Agent": settings.fetch_user_agent}) as client:
                    response = safe_stream_with_redirects(
                        client,
                        "GET",
                        feed.url,
                        headers=headers,
                        allow_private_network=settings.allow_private_network_fetch,
                        max_redirects=settings.outbound_max_redirects,
                    )
                    try:
                        status_code = response.status_code
                        final_url = str(response.url)

                        if status_code == 304:
                            now = datetime.now(timezone.utc)
                            feed.last_fetch_at = now
                            feed.last_success_at = now
                            feed.error_count = 0
                            feed.last_error = None
                            db.add(feed)
                            db.commit()
                            return {"status": "not_modified", "feed_id": feed_id}

                        if status_code != 200:
                            _mark_feed_failure(db, feed, f"http_status:{status_code}")
                            return {"status": "error", "feed_id": feed_id}

                        body_chunks: list[bytes] = []
                        body_size = 0
                        for chunk in response.iter_bytes():
                            body_size += len(chunk)
                            if body_size > settings.feed_max_bytes:
                                raise FeedResponseTooLargeError("feed response exceeds configured cap")
                            body_chunks.append(chunk)
                        body_bytes = b"".join(body_chunks)
                    finally:
                        response.close()
            except (httpx.HTTPError, SafeFetchError, RedirectError, TimeoutError) as exc:
                try:
                    logger.warning("feed_fetch_retrying feed_id=%s retries=%s error=%s", feed_id, self.request.retries, exc)
                    raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
                except MaxRetriesExceededError:
                    logger.error("feed_fetch_failed feed_id=%s error=%s", feed_id, exc)
                    _mark_feed_failure(db, feed, f"network_error:{exc}")
                    return {"status": "error", "feed_id": feed_id}
            except FeedResponseTooLargeError as exc:
                logger.error("feed_fetch_too_large feed_id=%s error=%s", feed_id, exc)
                _mark_feed_failure(db, feed, str(exc))
                return {"status": "error", "feed_id": feed_id}

            connector = RSSConnector()
            parsed_items, _ = connector.poll({"body": body_bytes}, None)
            _backfill_feed_metadata_from_body(feed, body_bytes)

            changed_item_ids: list[uuid.UUID] = []
            for parsed in parsed_items:
                item, changed = _upsert_item_from_parsed(db, feed, parsed)
                if changed:
                    changed_item_ids.append(item.id)

            now = datetime.now(timezone.utc)
            feed.etag = response.headers.get("etag") or feed.etag
            feed.last_modified = response.headers.get("last-modified") or feed.last_modified
            feed.last_success_at = now
            feed.last_fetch_at = now
            feed.error_count = 0
            feed.last_error = None

            db.add(feed)
            db.commit()

        for item_id in changed_item_ids:
            fetch_article.delay(str(item_id))

        return {"status": "ok", "feed_id": feed_id, "new_or_updated_items": len(changed_item_ids), "final_url": final_url}


@celery_app.task(name="app.tasks.feed_tasks.fetch_article", bind=True)
def fetch_article(self, item_id: str):
    with db_session() as db:
        item = db.scalar(select(Item).where(Item.id == uuid.UUID(item_id)))
        if item is None:
            return {"status": "skipped", "reason": "not_found", "item_id": item_id}

        existing_article = db.scalar(select(Article).where(Article.item_id == item.id))
        if existing_article is not None and item.status == "content_fetched":
            classify_item.delay(item_id)
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
            classify_item.delay(item_id)
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
                    headers={"User-Agent": settings.fetch_user_agent},
                ) as client:
                    response = safe_stream_with_redirects(
                        client,
                        "GET",
                        target_url,
                        allow_private_network=settings.allow_private_network_fetch,
                        max_redirects=settings.outbound_max_redirects,
                    )
                    try:
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
                    finally:
                        response.close()
        except (httpx.HTTPError, TimeoutError, SafeFetchError, RedirectError) as exc:
            try:
                logger.warning("article_fetch_retrying item_id=%s retries=%s error=%s", item_id, self.request.retries, exc)
                raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
            except MaxRetriesExceededError:
                logger.error("article_fetch_failed item_id=%s error=%s", item_id, exc)
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
                classify_item.delay(item_id)
                return {"status": "error", "item_id": item_id}
        except ResponseTooLargeError as exc:
            logger.error("article_fetch_too_large item_id=%s error=%s", item_id, exc)
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
            classify_item.delay(item_id)
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
            classify_item.delay(item_id)
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
            classify_item.delay(item_id)
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

        if canonical and is_fetchable_url(canonical, allow_private_network=settings.allow_private_network_fetch):
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

    classify_item.delay(item_id)
    return {"status": "ok", "item_id": item_id}


@celery_app.task(name="app.tasks.feed_tasks.classify_item")
def classify_item(item_id: str):
    with db_session() as db:
        try:
            parsed_item_id = uuid.UUID(item_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_item_id", "item_id": item_id}

        item = db.scalar(select(Item).where(Item.id == parsed_item_id))
        if item is None:
            return {"status": "skipped", "reason": "not_found", "item_id": item_id}

        article = db.scalar(select(Article).where(Article.item_id == parsed_item_id))
        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
        feed_name = feed.name if feed is not None else ""
        feed_url = feed.url if feed is not None else ""

        result = classify_item_content(
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
            feed_name=feed_name,
        )

        row = db.scalar(select(ItemClassification).where(ItemClassification.item_id == parsed_item_id))
        if row is not None and row.source_hash == result.source_hash and row.rules_version == result.rules_version:
            feedback_adjustments = load_feedback_adjustments(
                db,
                tag_names=[row.primary_category, *(row.secondary_categories or [])],
            )
            sync_item_algorithm_tags(
                db,
                item_id=parsed_item_id,
                primary_category=row.primary_category,
                secondary_categories=row.secondary_categories,
                classification_confidence=row.confidence,
                title=item.title,
                summary=item.summary,
                article_text=article.text if article else None,
                feed_name=feed_name,
                feed_url=feed_url,
                feedback_adjustments=feedback_adjustments,
            )
            db.commit()
            extract_item_iocs.delay(item_id)
            return {"status": "skipped", "reason": "up_to_date", "item_id": item_id, "category": row.primary_category}

        if row is None:
            row = ItemClassification(item_id=parsed_item_id)

        row.primary_category = result.primary_category
        row.secondary_categories = result.secondary_categories
        row.confidence = result.confidence
        row.scores_json = result.scores
        row.matched_terms_json = result.matched_terms
        row.source_hash = result.source_hash
        row.rules_version = result.rules_version
        row.classified_at = datetime.now(timezone.utc)

        db.add(row)
        feedback_adjustments = load_feedback_adjustments(
            db,
            tag_names=[result.primary_category, *(result.secondary_categories or [])],
        )
        sync_item_algorithm_tags(
            db,
            item_id=parsed_item_id,
            primary_category=result.primary_category,
            secondary_categories=result.secondary_categories,
            classification_confidence=result.confidence,
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
            feed_name=feed_name,
            feed_url=feed_url,
            feedback_adjustments=feedback_adjustments,
        )
        db.commit()

    extract_item_iocs.delay(item_id)
    return {"status": "ok", "item_id": item_id, "category": result.primary_category}


@celery_app.task(name="app.tasks.feed_tasks.extract_item_iocs")
def extract_item_iocs(item_id: str):
    with db_session() as db:
        try:
            parsed_item_id = uuid.UUID(item_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_item_id", "item_id": item_id}

        item = db.scalar(select(Item).where(Item.id == parsed_item_id))
        if item is None:
            return {"status": "skipped", "reason": "not_found", "item_id": item_id}

        article = db.scalar(select(Article).where(Article.item_id == parsed_item_id))
        extracted = extract_iocs(
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
        )

        by_key: dict[tuple[str, str], dict[str, object]] = {}
        for match in extracted:
            key = (match.type, match.value_norm)
            record = by_key.get(key)
            if record is None:
                by_key[key] = {
                    "value_raw": match.value_raw,
                    "source_sections": {match.source_section},
                    "occurrences": 1,
                    "confidence": match.confidence,
                }
                continue

            record["source_sections"] = set(record["source_sections"]).union({match.source_section})
            record["occurrences"] = int(record["occurrences"]) + 1
            record["confidence"] = max(float(record["confidence"]), match.confidence)

        linked_ioc_ids: set[uuid.UUID] = set()
        ioc_values_by_type: dict[str, list[str]] = {}
        now = datetime.now(timezone.utc)
        for (ioc_type, ioc_value_norm), info in by_key.items():
            ioc_values_by_type.setdefault(ioc_type, []).append(ioc_value_norm)
            ioc = _get_or_create_ioc(
                db,
                ioc_type=ioc_type,
                ioc_value_norm=ioc_value_norm,
                ioc_value_raw=str(info["value_raw"]),
                now=now,
            )

            linked_ioc_ids.add(ioc.id)
            source_sections = ",".join(sorted(set(info["source_sections"])))
            link = db.scalar(select(ItemIOC).where(ItemIOC.item_id == parsed_item_id, ItemIOC.ioc_id == ioc.id))
            if link is None:
                link = ItemIOC(item_id=parsed_item_id, ioc_id=ioc.id)

            link.source_section = source_sections
            link.occurrences = int(info["occurrences"])
            link.confidence = float(info["confidence"])
            db.add(link)

        if linked_ioc_ids:
            db.query(ItemIOC).filter(ItemIOC.item_id == parsed_item_id, ItemIOC.ioc_id.notin_(linked_ioc_ids)).delete(
                synchronize_session=False
            )
        else:
            db.query(ItemIOC).filter(ItemIOC.item_id == parsed_item_id).delete(synchronize_session=False)

        classification = db.scalar(select(ItemClassification).where(ItemClassification.item_id == parsed_item_id))
        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
        feedback_hints = [
            classification.primary_category if classification else "",
            *((classification.secondary_categories or []) if classification else []),
        ]
        for ioc_type, values in ioc_values_by_type.items():
            feedback_hints.append(f"ioc:{ioc_type}")
            feedback_hints.extend(values[:6])
        feedback_adjustments = load_feedback_adjustments(db, tag_names=feedback_hints)
        sync_item_algorithm_tags(
            db,
            item_id=parsed_item_id,
            primary_category=classification.primary_category if classification else "threat_intelligence_research",
            secondary_categories=classification.secondary_categories if classification else [],
            classification_confidence=classification.confidence if classification else 0.35,
            ioc_values_by_type=ioc_values_by_type,
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
            feed_name=feed.name if feed else "",
            feed_url=feed.url if feed else "",
            feedback_adjustments=feedback_adjustments,
        )

        db.commit()

    return {"status": "ok", "item_id": item_id, "ioc_count": len(by_key)}


def _upsert_item_from_parsed(db: Session, feed: Feed, parsed) -> tuple[Item, bool]:
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
            return candidate, True

        # Another worker inserted the same dedupe key concurrently.
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
        return item, True

    return item, False


def _insert_item_with_conflict_retry(db: Session, item: Item) -> bool:
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
        return True
    except IntegrityError:
        logger.info("dedupe_conflict_detected dedupe_key=%s", item.dedupe_key)
        return False


def _get_or_create_ioc(
    db: Session,
    *,
    ioc_type: str,
    ioc_value_norm: str,
    ioc_value_raw: str,
    now: datetime,
) -> IOC:
    ioc = db.scalar(select(IOC).where(IOC.type == ioc_type, IOC.value_norm == ioc_value_norm))
    if ioc is None:
        candidate = IOC(
            type=ioc_type,
            value_raw=ioc_value_raw,
            value_norm=ioc_value_norm,
            first_seen_at=now,
            last_seen_at=now,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate
        except IntegrityError:
            ioc = db.scalar(select(IOC).where(IOC.type == ioc_type, IOC.value_norm == ioc_value_norm))
            if ioc is None:
                raise

    ioc.last_seen_at = now
    db.add(ioc)
    db.flush()
    return ioc


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
