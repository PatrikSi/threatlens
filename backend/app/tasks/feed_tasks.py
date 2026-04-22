import time
import uuid
import secrets
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
import redis
from celery.exceptions import MaxRetriesExceededError
from croniter import croniter
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.ai_daily_brief import AIDailyBrief
from app.models.article import Article
from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.ai_config import load_active_ai_settings
from app.services.ai_integration import run_daily_brief_generation, run_item_ai_enrichment
from app.services.ai_integration import is_stale_daily_brief_pending
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_QUEUED,
    AI_STATUS_READY,
    AI_STATUS_RUNNING,
    AI_STATUS_SKIPPED,
    AI_TASK_TYPE_DAILY_BRIEF,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_AUTO,
    AI_TRIGGER_MANUAL,
    AI_TRIGGER_SCHEDULED,
    ai_task_run_stop_reason,
    finish_ai_task_run,
    get_ai_task_run_stop_reason,
    is_ai_task_run_cancel_requested,
    queue_ai_task_run,
    record_ai_task_event,
    _reconcile_stale_ai_runs,
    start_ai_task_run,
    update_ai_task_run_celery,
)
from app.services.connectors.rss import RSSConnector, RSSFeedParseError
from app.services.algorithm_tags import sync_item_algorithm_tags
from app.services.classification import classify_item_content
from app.services.extraction import extract_canonical_url, extract_readable_text
from app.services.feed_metadata import (
    apply_probe_metadata as _apply_probe_metadata,
    backfill_feed_metadata_from_body as _backfill_feed_metadata_from_body,
    needs_metadata_backfill as _needs_metadata_backfill,
)
from app.services.feed_pipeline import (
    clear_feed_dispatch_claim as _clear_feed_dispatch_claim,
    claim_feed_for_dispatch as _claim_feed_for_dispatch_impl,
    list_item_ids_missing_articles as _list_item_ids_missing_articles_impl,
    mark_feed_failure as _mark_feed_failure,
    upsert_item_from_parsed as _upsert_item_from_parsed,
)
from app.services.feed_probe import FeedProbeError, probe_feed_metadata
from app.services.ioc_extraction import extract_iocs
from app.services.notification_webhooks import (
    FEED_FAILING_NOTIFICATION_THRESHOLD,
    build_daily_digest_context,
    get_matching_notification_webhooks,
    get_matching_notification_webhooks_for_feed,
    has_recent_notification_delivery,
    list_recoverable_notification_delivery_ids,
    process_notification_webhook_delivery,
    reserve_retryable_notification_webhook_delivery,
    reserve_alert_match_notification_deliveries,
    reserve_feed_failing_notification_deliveries,
    reserve_new_item_notification_deliveries,
    reserve_notification_webhook_delivery,
    reserve_webhook_failed_notification_deliveries,
    try_acquire_notification_delivery_lock,
)
from app.services.tag_feedback import load_feedback_adjustments
from app.services.safe_fetch import RedirectError, SafeFetchError, build_safe_http_client, safe_stream_with_redirects
from app.services.url_utils import is_fetchable_url, normalize_url
from app.tasks.celery_app import celery_app

settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
logger = logging.getLogger(__name__)


class ResponseTooLargeError(Exception):
    pass


class FeedResponseTooLargeError(Exception):
    pass


class CoordinationUnavailableError(RuntimeError):
    pass


DAILY_BRIEF_STALE_RETRY_WINDOW = timedelta(minutes=15)


@contextmanager
def db_session() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _lease_renewal_interval_seconds(ttl_seconds: int) -> float:
    ttl_seconds = max(1, int(ttl_seconds))
    return max(0.5, min(15.0, ttl_seconds / 3.0))


@contextmanager
def _redis_lease_heartbeat(key: str, ttl_seconds: int, token: str | None = None):
    stop_event = threading.Event()
    renew_interval_seconds = _lease_renewal_interval_seconds(ttl_seconds)

    def _renew() -> None:
        while not stop_event.wait(renew_interval_seconds):
            try:
                if token is not None:
                    current_token = None
                    get_redis_value = getattr(redis_client, "get", None)
                    if callable(get_redis_value):
                        current_token = get_redis_value(key)
                    if current_token != token:
                        return
                redis_client.expire(key, ttl_seconds)
            except redis.RedisError:
                continue

    _renewal_thread = threading.Thread(
        target=_renew,
        name=f"threatlens-lease-renewal:{key}",
        daemon=True,
    )
    _renewal_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        _renewal_thread.join(timeout=0.1)


def _process_reserved_notification_deliveries(
    db: Session,
    delivery_ids: list[uuid.UUID],
) -> tuple[int, int]:
    delivered = 0
    failed = 0

    for delivery_id in delivery_ids:
        attempt = process_notification_webhook_delivery(db, delivery_id=delivery_id)
        if not getattr(attempt, "claimed", True):
            logger.info(
                "notification_webhook_delivery_already_claimed delivery_id=%s state=%s",
                delivery_id,
                getattr(attempt.delivery, "delivery_state", "unknown"),
            )
            continue
        if attempt.result.success:
            delivered += 1
            continue

        failed += 1
        source_webhook = db.scalar(
            select(NotificationWebhook).where(NotificationWebhook.id == attempt.delivery.webhook_id)
        )
        retry_reservation = (
            reserve_retryable_notification_webhook_delivery(
                db,
                webhook=source_webhook,
                delivery=attempt.delivery,
            )
            if source_webhook is not None and attempt.delivery.event_type_snapshot != "webhook_failed"
            else None
        )
        if retry_reservation is not None:
            if retry_reservation.created:
                db.commit()
                enqueue_notification_webhook_delivery_processing(
                    [retry_reservation.delivery.id],
                    countdown=retry_reservation.countdown_seconds,
                )
                logger.warning(
                    "notification_webhook_delivery_retry_scheduled webhook_id=%s delivery_id=%s retry_delivery_id=%s countdown_seconds=%s",
                    attempt.delivery.webhook_id,
                    attempt.delivery.id,
                    retry_reservation.delivery.id,
                    retry_reservation.countdown_seconds,
                )
            continue
        if attempt.delivery.event_type_snapshot != "webhook_failed":
            failed_delivery_reservations = reserve_webhook_failed_notification_deliveries(
                db,
                failed_delivery=attempt.delivery,
            )
            db.commit()
            enqueue_notification_webhook_delivery_processing(failed_delivery_reservations.delivery_ids)
        logger.warning(
            "notification_webhook_delivery_failed webhook_id=%s delivery_id=%s event_type=%s status_code=%s error=%s",
            attempt.delivery.webhook_id,
            attempt.delivery.id,
            attempt.delivery.event_type_snapshot,
            attempt.result.status_code,
            attempt.result.error,
        )

    return delivered, failed


def _enqueue_classification_task(item_id: str) -> bool:
    try:
        classify_item.delay(item_id)
    except Exception as exc:
        logger.exception("item_classification_enqueue_failed item_id=%s error=%s", item_id, exc)
        return False
    return True


def enqueue_notification_webhook_delivery_processing(
    delivery_ids: list[uuid.UUID],
    *,
    countdown: int | None = None,
) -> bool:
    if not delivery_ids:
        return True

    delivery_id_chunks = _chunk_uuid_list(
        delivery_ids,
        max(1, int(settings.notification_delivery_enqueue_batch_size)),
    )
    all_enqueued = True

    for delivery_id_chunk in delivery_id_chunks:
        serialized_ids = [str(delivery_id) for delivery_id in delivery_id_chunk]
        try:
            if countdown is not None and int(countdown) > 0:
                process_notification_webhook_deliveries.apply_async(args=[serialized_ids], countdown=int(countdown))
            else:
                process_notification_webhook_deliveries.delay(serialized_ids)
        except Exception as exc:
            all_enqueued = False
            logger.exception(
                "notification_webhook_delivery_enqueue_failed delivery_count=%s error=%s",
                len(delivery_id_chunk),
                exc,
            )
    return all_enqueued


def enqueue_article_fetch_processing(item_ids: list[uuid.UUID]) -> bool:
    if not item_ids:
        return True

    all_enqueued = True
    for item_id in item_ids:
        try:
            fetch_article.delay(str(item_id))
        except Exception as exc:
            all_enqueued = False
            logger.exception("article_fetch_enqueue_failed item_id=%s error=%s", item_id, exc)
    return all_enqueued


def _chunk_uuid_list(values: list[uuid.UUID], chunk_size: int) -> list[list[uuid.UUID]]:
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def _claim_feed_for_dispatch(db: Session, *, feed_id: uuid.UUID, now: datetime) -> bool:
    return _claim_feed_for_dispatch_impl(
        db,
        feed_id=feed_id,
        now=now,
        claim_seconds=settings.dispatch_feed_claim_seconds,
        is_feed_due=_is_feed_due,
    )


def _list_item_ids_missing_articles(db: Session, *, limit: int, now: datetime | None = None) -> list[uuid.UUID]:
    return _list_item_ids_missing_articles_impl(
        db,
        limit=limit,
        now=now,
        dispatch_after_seconds=settings.dispatch_items_missing_articles_after_seconds,
    )


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
            raise CoordinationUnavailableError("domain slot unavailable") from exc
        if current <= settings.per_domain_concurrency:
            acquired = True
            break
        try:
            redis_client.decr(key)
        except redis.RedisError as exc:
            raise CoordinationUnavailableError("domain slot unavailable") from exc
        time.sleep(0.2)

    if not acquired:
        raise TimeoutError(f"domain slot timeout for {domain}")

    try:
        with _redis_lease_heartbeat(key, 30):
            yield
    finally:
        try:
            remaining = redis_client.decr(key)
            if remaining <= 0:
                redis_client.delete(key)
        except redis.RedisError:
            pass


@celery_app.task(
    name="app.tasks.feed_tasks.process_notification_webhook_deliveries",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_notification_webhook_deliveries(delivery_ids: list[str]):
    parsed_delivery_ids: list[uuid.UUID] = []
    skipped = 0
    for delivery_id in delivery_ids:
        try:
            parsed_delivery_ids.append(uuid.UUID(delivery_id))
        except ValueError:
            skipped += 1

    if not parsed_delivery_ids:
        return {"status": "skipped", "reason": "no_valid_delivery_ids", "skipped": skipped}

    with db_session() as db:
        delivered, failed = _process_reserved_notification_deliveries(db, parsed_delivery_ids)
        return {
            "status": "ok",
            "scanned": len(parsed_delivery_ids),
            "delivered": delivered,
            "failed": failed,
            "skipped": skipped,
        }


def _update_task_run_celery_id(run_id: uuid.UUID, celery_task_id: str | None) -> None:
    with db_session() as db:
        update_ai_task_run_celery(db, run_id=run_id, celery_task_id=celery_task_id)
        db.commit()


def _task_run_claimed_by_current_worker(run: AITaskRun | None, *, celery_task_id: str | None) -> bool:
    if run is None:
        return False
    if celery_task_id is None:
        return True
    return run.celery_task_id in (None, celery_task_id)


def _queue_item_ai_enrichment_run(
    *,
    item_id: uuid.UUID,
    trigger_source: str,
    reason: str | None,
    actor_user_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    force: bool = False,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> uuid.UUID:
    with db_session() as db:
        run = queue_ai_task_run(
            db,
            task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
            trigger_source=trigger_source,
            actor_user_id=actor_user_id,
            item_id=item_id,
            parent_run_id=parent_run_id,
            model=model,
            metadata=metadata,
            reason=reason,
        )
        db.commit()
        run_id = run.id
    try:
        task = generate_item_ai_enrichment_task.delay(str(item_id), force=force, task_run_id=str(run_id))
    except Exception as exc:
        with db_session() as db:
            finish_ai_task_run(
                db,
                run_id=run_id,
                status=AI_STATUS_ERROR,
                reason="enqueue_failed",
                error=str(exc),
                worker_name="api",
                metadata_updates={"force": bool(force)},
            )
            db.commit()
        raise
    task_id = getattr(task, "id", None)
    if task_id:
        _update_task_run_celery_id(run_id, task_id)
    return run_id


def _safe_enqueue_item_iocs(item_id: uuid.UUID) -> bool:
    try:
        extract_item_iocs.delay(str(item_id))
    except Exception as exc:
        logger.exception("item_ioc_enqueue_failed item_id=%s error=%s", item_id, exc)
        return False
    return True


def _safe_queue_item_ai_enrichment_run(
    *,
    item_id: uuid.UUID,
    trigger_source: str,
    reason: str | None,
    actor_user_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    force: bool = False,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> bool:
    try:
        _queue_item_ai_enrichment_run(
            item_id=item_id,
            trigger_source=trigger_source,
            reason=reason,
            actor_user_id=actor_user_id,
            parent_run_id=parent_run_id,
            force=force,
            model=model,
            metadata=metadata,
        )
    except Exception as exc:
        logger.exception(
            "item_ai_enrichment_enqueue_failed item_id=%s parent_run_id=%s error=%s",
            item_id,
            parent_run_id,
            exc,
        )
        return False
    return True


def _get_ai_run_stop_reason(run_id: uuid.UUID | None) -> str | None:
    if run_id is None:
        return None
    with db_session() as db:
        return get_ai_task_run_stop_reason(db, run_id=run_id)


def _record_skipped_item_ai_enrichment_run(
    *,
    item_id: uuid.UUID,
    trigger_source: str,
    reason: str,
    actor_user_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> uuid.UUID:
    with db_session() as db:
        run = queue_ai_task_run(
            db,
            task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
            trigger_source=trigger_source,
            actor_user_id=actor_user_id,
            item_id=item_id,
            parent_run_id=parent_run_id,
            model=model,
            metadata=metadata,
            reason=reason,
        )
        finish_ai_task_run(
            db,
            run_id=run.id,
            status=AI_STATUS_SKIPPED,
            reason=reason,
            metadata_updates=metadata,
        )
        db.commit()
        return run.id


@contextmanager
def feed_lock(feed_id: str, ttl_seconds: int = 900):
    key = f"threatlens:feed:lock:{feed_id}"
    token = secrets.token_hex(16)

    acquired = False
    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
    except redis.RedisError as exc:
        raise CoordinationUnavailableError("feed lock unavailable") from exc

    if not acquired:
        yield False
        return

    try:
        with _redis_lease_heartbeat(key, ttl_seconds, token):
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


@contextmanager
def daily_ai_brief_lock(ttl_seconds: int = 900):
    key = "threatlens:ai:daily_brief:lock"
    token = secrets.token_hex(16)

    acquired = False
    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
    except redis.RedisError as exc:
        raise CoordinationUnavailableError("daily brief lock unavailable") from exc

    if not acquired:
        yield False
        return

    try:
        with _redis_lease_heartbeat(key, ttl_seconds, token):
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


def _scheduled_daily_ai_brief_due(db: Session, *, now: datetime) -> tuple[bool, str | None]:
    active = load_active_ai_settings(db)
    if not active.ai_enabled:
        return False, "ai_disabled"
    if not active.ai_configured:
        return False, "ai_not_configured"
    if not active.daily_brief_enabled:
        return False, "daily_brief_disabled"

    scheduled_at = now.replace(
        hour=active.daily_brief_schedule_hour_utc,
        minute=active.daily_brief_schedule_minute_utc,
        second=0,
        microsecond=0,
    )
    if now < scheduled_at:
        return False, "scheduled_time_not_reached"

    existing = db.scalar(select(AIDailyBrief).where(AIDailyBrief.brief_date == now.date()))
    if existing is not None:
        if existing.status == "ready":
            return False, "already_generated"
        if existing.status == "pending" and not is_stale_daily_brief_pending(existing, now=now):
            return False, "already_running"

    in_flight_run = db.scalar(
        select(AITaskRun.id)
        .where(
            AITaskRun.task_type == AI_TASK_TYPE_DAILY_BRIEF,
            AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            AITaskRun.queued_at >= scheduled_at,
            AITaskRun.queued_at < scheduled_at + timedelta(days=1),
        )
        .order_by(AITaskRun.queued_at.desc())
        .limit(1)
    )
    if in_flight_run is not None:
        task_run = db.scalar(select(AITaskRun).where(AITaskRun.id == in_flight_run))
        if task_run is not None and not _is_stale_daily_brief_task_run(task_run, now=now):
            return False, "already_running"

    return True, None


def _is_stale_daily_brief_task_run(run: AITaskRun, *, now: datetime) -> bool:
    reference = run.updated_at or run.started_at or run.queued_at or run.created_at
    if reference is None:
        return True
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return now - reference >= DAILY_BRIEF_STALE_RETRY_WINDOW


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_feeds")
def dispatch_due_feeds():
    now = datetime.now(timezone.utc)
    queued = 0

    with db_session() as db:
        feed_ids = db.scalars(select(Feed.id).where(Feed.enabled.is_(True)).order_by(Feed.created_at.asc())).all()
        for feed_id in feed_ids:
            if queued >= settings.dispatch_due_feeds_batch_size:
                break
            if not _claim_feed_for_dispatch(db, feed_id=feed_id, now=now):
                db.rollback()
                continue
            db.commit()
            try:
                fetch_feed.delay(str(feed_id))
            except Exception as exc:
                logger.exception("feed_dispatch_enqueue_failed feed_id=%s error=%s", feed_id, exc)
                claimed_feed = db.scalar(select(Feed).where(Feed.id == feed_id))
                if claimed_feed is not None:
                    _clear_feed_dispatch_claim(claimed_feed)
                    db.add(claimed_feed)
                db.commit()
                continue
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
        if _enqueue_classification_task(str(item_id)):
            queued += 1

    return {"queued": queued}


@celery_app.task(name="app.tasks.feed_tasks.dispatch_items_missing_articles")
def dispatch_items_missing_articles():
    with db_session() as db:
        item_ids = _list_item_ids_missing_articles(
            db,
            limit=settings.dispatch_items_missing_articles_batch_size,
        )

    queued = 0
    for item_id in item_ids:
        try:
            fetch_article.delay(str(item_id))
        except Exception as exc:
            logger.exception("article_fetch_repair_enqueue_failed item_id=%s error=%s", item_id, exc)
            continue
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
        try:
            extract_item_iocs.delay(str(item_id))
        except Exception as exc:
            logger.exception("item_ioc_repair_enqueue_failed item_id=%s error=%s", item_id, exc)
            continue
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
        try:
            backfill_feed_metadata.delay(str(feed.id))
        except Exception as exc:
            logger.exception("feed_metadata_backfill_enqueue_failed feed_id=%s error=%s", feed.id, exc)
            continue
        queued += 1

    return {"queued": queued}


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_new_item_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_new_item_notification_webhooks(item_id: str):
    with db_session() as db:
        try:
            parsed_item_id = uuid.UUID(item_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_item_id", "item_id": item_id}

        item = db.scalar(select(Item).where(Item.id == parsed_item_id))
        if item is None:
            return {"status": "skipped", "reason": "item_not_found", "item_id": item_id}

        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
        if feed is None:
            return {"status": "skipped", "reason": "feed_not_found", "item_id": item_id}

        reservation = reserve_new_item_notification_deliveries(db, item=item, feed=feed)

        db.commit()
        delivered, failed = _process_reserved_notification_deliveries(db, reservation.delivery_ids)

        return {
            "status": "ok",
            "item_id": item_id,
            "matched_webhooks": reservation.matched_webhooks,
            "delivered": delivered,
            "failed": failed,
            "skipped": reservation.skipped,
        }


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_alert_match_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_alert_match_notification_webhooks(item_id: str):
    with db_session() as db:
        try:
            parsed_item_id = uuid.UUID(item_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_item_id", "item_id": item_id}

        item = db.scalar(select(Item).where(Item.id == parsed_item_id))
        if item is None:
            return {"status": "skipped", "reason": "item_not_found", "item_id": item_id}

        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
        if feed is None:
            return {"status": "skipped", "reason": "feed_not_found", "item_id": item_id}

        reservation = reserve_alert_match_notification_deliveries(db, item=item, feed=feed)

        db.commit()
        delivered, failed = _process_reserved_notification_deliveries(db, reservation.delivery_ids)

        return {
            "status": "ok",
            "item_id": item_id,
            "matched_webhooks": reservation.matched_webhooks,
            "delivered": delivered,
            "failed": failed,
            "skipped": reservation.skipped,
        }


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_feed_failing_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_feed_failing_notification_webhooks(feed_id: str):
    with db_session() as db:
        try:
            parsed_feed_id = uuid.UUID(feed_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_feed_id", "feed_id": feed_id}

        feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
        if feed is None:
            return {"status": "skipped", "reason": "feed_not_found", "feed_id": feed_id}
        if feed.error_count < FEED_FAILING_NOTIFICATION_THRESHOLD:
            return {"status": "skipped", "reason": "below_failure_threshold", "feed_id": feed_id}

        reservation = reserve_feed_failing_notification_deliveries(db, feed=feed)

        db.commit()
        delivered, failed = _process_reserved_notification_deliveries(db, reservation.delivery_ids)

        return {
            "status": "ok",
            "feed_id": feed_id,
            "matched_webhooks": reservation.matched_webhooks,
            "delivered": delivered,
            "failed": failed,
            "skipped": reservation.skipped,
        }


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_webhook_failed_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_webhook_failed_notification_webhooks(delivery_id: str):
    with db_session() as db:
        try:
            parsed_delivery_id = uuid.UUID(delivery_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_delivery_id", "delivery_id": delivery_id}

        failed_delivery = db.scalar(select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.id == parsed_delivery_id))
        if failed_delivery is None:
            return {"status": "skipped", "reason": "delivery_not_found", "delivery_id": delivery_id}
        if failed_delivery.success or failed_delivery.event_type_snapshot == "webhook_failed":
            return {"status": "skipped", "reason": "not_eligible", "delivery_id": delivery_id}

        reservation = reserve_webhook_failed_notification_deliveries(db, failed_delivery=failed_delivery)

        db.commit()
        delivered, failed = _process_reserved_notification_deliveries(db, reservation.delivery_ids)

        return {
            "status": "ok",
            "delivery_id": delivery_id,
            "matched_webhooks": reservation.matched_webhooks,
            "delivered": delivered,
            "failed": failed,
            "skipped": reservation.skipped,
        }


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_daily_digest_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_daily_digest_notification_webhooks():
    with db_session() as db:
        webhooks = get_matching_notification_webhooks(db, event_type="daily_digest")
        reserved_delivery_ids: list[uuid.UUID] = []
        skipped = 0
        digest_day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        digest_scope_key = digest_day_start.date().isoformat()

        for webhook in webhooks:
            user = db.scalar(select(User).where(User.id == webhook.user_id))
            if user is None or not user.is_active or not user.is_approved:
                skipped += 1
                continue

            if not try_acquire_notification_delivery_lock(
                db,
                webhook_id=webhook.id,
                event_type="daily_digest",
                scope_key=digest_day_start.date().isoformat(),
            ):
                skipped += 1
                continue

            if has_recent_notification_delivery(
                db,
                webhook_id=webhook.id,
                event_type="daily_digest",
                since=digest_day_start,
                scope_key=digest_scope_key,
            ):
                skipped += 1
                continue

            feed_ids = [uuid.UUID(value) for value in (webhook.feed_ids_json or [])] if webhook.feed_scope == "selected" else None
            digest_context = build_daily_digest_context(db, user_id=user.id, feed_ids=feed_ids)
            if digest_context is None or digest_context.total_items <= 0:
                skipped += 1
                continue

            delivery = reserve_notification_webhook_delivery(
                db,
                webhook=webhook,
                user=user,
                event_type="daily_digest",
                feed=None,
                item=None,
                digest_context=digest_context,
                item_title=f"{digest_context.total_items} items in last 24h",
                feed_name=", ".join(digest_context.feed_names[:3]) or None,
                scope_key=digest_scope_key,
            )
            reserved_delivery_ids.append(delivery.id)

        db.commit()
        delivered, failed = _process_reserved_notification_deliveries(db, reserved_delivery_ids)

        return {"status": "ok", "matched_webhooks": len(webhooks), "delivered": delivered, "failed": failed, "skipped": skipped}


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_pending_notification_webhook_deliveries",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_pending_notification_webhook_deliveries():
    with db_session() as db:
        delivery_ids = list_recoverable_notification_delivery_ids(db)
        delivered, failed = _process_reserved_notification_deliveries(db, delivery_ids)
        return {
            "status": "ok",
            "scanned": len(delivery_ids),
            "delivered": delivered,
            "failed": failed,
        }


@celery_app.task(
    name="app.tasks.feed_tasks.reconcile_ai_task_runs",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_ai_task_runs():
    with db_session() as db:
        reconciled = _reconcile_stale_ai_runs(db)
        return {"status": "ok", "reconciled": reconciled}


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.dispatch_daily_ai_brief_generation",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_daily_ai_brief_generation(
    self,
    force: bool = False,
    task_run_id: str | None = None,
    actor_user_id: str | None = None,
):
    with db_session() as db:
        parsed_run_id = None
        parsed_actor_user_id = None
        if task_run_id:
            try:
                parsed_run_id = uuid.UUID(task_run_id)
            except ValueError:
                parsed_run_id = None
        if actor_user_id:
            try:
                parsed_actor_user_id = uuid.UUID(actor_user_id)
            except ValueError:
                parsed_actor_user_id = None
        is_scheduled_dispatch = parsed_run_id is None and parsed_actor_user_id is None
        if is_scheduled_dispatch and not force:
            due, reason = _scheduled_daily_ai_brief_due(db, now=datetime.now(timezone.utc))
            if not due:
                return {"status": "skipped", "reason": reason}
        run: AITaskRun | None = None
        try:
            with daily_ai_brief_lock() as acquired:
                if not acquired:
                    return {"status": "skipped", "reason": "already_running"}
                if parsed_run_id:
                    run = db.scalar(select(AITaskRun).where(AITaskRun.id == parsed_run_id))
                    if run is None:
                        run = queue_ai_task_run(
                            db,
                            task_type=AI_TASK_TYPE_DAILY_BRIEF,
                            trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                            actor_user_id=parsed_actor_user_id,
                            model=None,
                            metadata={"force": bool(force), "scheduled": parsed_actor_user_id is None},
                        )
                else:
                    run = queue_ai_task_run(
                        db,
                        task_type=AI_TASK_TYPE_DAILY_BRIEF,
                        trigger_source=AI_TRIGGER_MANUAL if parsed_actor_user_id else AI_TRIGGER_SCHEDULED,
                        actor_user_id=parsed_actor_user_id,
                        model=None,
                        metadata={"force": bool(force), "scheduled": parsed_actor_user_id is None},
                    )
                started_run = start_ai_task_run(
                    db,
                    run_id=run.id,
                    worker_name=getattr(self.request, "hostname", None),
                    celery_task_id=getattr(self.request, "id", None),
                    metadata_updates={"force": bool(force)},
                )
                db.commit()
                if not _task_run_claimed_by_current_worker(started_run, celery_task_id=getattr(self.request, "id", None)):
                    return {"status": "skipped", "reason": "already_running", "run_id": task_run_id}
                stop_reason = ai_task_run_stop_reason(started_run)
                if stop_reason is not None:
                    if stop_reason == "canceled":
                        finish_ai_task_run(
                            db,
                            run_id=run.id,
                            status=AI_STATUS_SKIPPED,
                            reason="canceled",
                            worker_name=getattr(self.request, "hostname", None),
                            metadata_updates={"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
                        )
                        db.commit()
                    return {"status": "skipped", "reason": stop_reason}
                active_ai_settings = load_active_ai_settings(db)
                if not active_ai_settings.ai_enabled:
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=AI_STATUS_SKIPPED,
                        reason="ai_disabled",
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "ai_disabled"}
                if not active_ai_settings.ai_configured:
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=AI_STATUS_SKIPPED,
                        reason="ai_not_configured",
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "ai_not_configured"}
                if not active_ai_settings.daily_brief_enabled:
                    finish_ai_task_run(
                        db,
                        run_id=run.id,
                        status=AI_STATUS_SKIPPED,
                        reason="daily_brief_disabled",
                        worker_name=getattr(self.request, "hostname", None),
                    )
                    db.commit()
                    return {"status": "skipped", "reason": "daily_brief_disabled"}

                result = run_daily_brief_generation(db, force=force, task_run_id=run.id)
                finish_ai_task_run(
                    db,
                    run_id=run.id,
                    status=AI_STATUS_READY if result.status == "ready" else AI_STATUS_ERROR if result.status == "error" else AI_STATUS_SKIPPED,
                    reason=result.reason,
                    error=result.brief.error if result.brief is not None and result.status == "error" else None,
                    worker_name=getattr(self.request, "hostname", None),
                    model=result.brief.model if result.brief is not None else active_ai_settings.model,
                    prompt_tokens=result.brief.prompt_tokens if result.brief is not None else None,
                    completion_tokens=result.brief.completion_tokens if result.brief is not None else None,
                    total_tokens=result.brief.total_tokens if result.brief is not None else None,
                    latency_ms=result.brief.latency_ms if result.brief is not None else None,
                    prompt_char_count=result.prompt_char_count,
                    response_char_count=result.response_char_count,
                    metadata_updates={"items_considered": result.items_considered, "items_selected": result.items_selected},
                    daily_brief_id=result.brief.id if result.brief is not None else None,
                )
                db.commit()
                if result.brief is None:
                    return {"status": result.status, "reason": result.reason}
                return {"status": result.status, "reason": result.reason, "brief_date": result.brief.brief_date.isoformat()}
        except CoordinationUnavailableError as exc:
            logger.warning("daily_brief_coordination_unavailable error=%s", exc)
            if run is not None:
                finish_ai_task_run(
                    db,
                    run_id=run.id,
                    status=AI_STATUS_ERROR,
                    reason="coordination_unavailable",
                    error=str(exc),
                    worker_name=getattr(self.request, "hostname", None),
                )
                db.commit()
            return {"status": "error", "reason": "coordination_unavailable"}


@celery_app.task(name="app.tasks.feed_tasks.record_beat_heartbeat")
def record_beat_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    try:
        redis_client.set(settings.beat_heartbeat_key, now, ex=settings.beat_heartbeat_ttl_seconds)
    except redis.RedisError as exc:
        logger.warning("beat_heartbeat_write_failed error=%s", exc)
        return {"status": "error", "reason": "redis_unavailable"}
    return {"status": "ok", "at": now}


@celery_app.task(
    name="app.tasks.feed_tasks.backfill_feed_metadata",
    acks_late=True,
    reject_on_worker_lost=True,
)
def backfill_feed_metadata(feed_id: str):
    try:
        with feed_lock(feed_id) as acquired:
            if not acquired:
                return {"status": "skipped", "reason": "already_fetching", "feed_id": feed_id}

            with db_session() as db:
                try:
                    parsed_feed_id = uuid.UUID(feed_id)
                except ValueError:
                    return {"status": "skipped", "reason": "invalid_feed_id", "feed_id": feed_id}

                feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
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
    except CoordinationUnavailableError as exc:
        logger.warning("backfill_feed_metadata_coordination_unavailable feed_id=%s error=%s", feed_id, exc)
        return {"status": "error", "reason": "coordination_unavailable", "feed_id": feed_id}


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


@celery_app.task(
    name="app.tasks.feed_tasks.fetch_feed",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def fetch_feed(self, feed_id: str, force: bool = False):
    try:
        with feed_lock(feed_id) as acquired:
            if not acquired:
                return {"status": "skipped", "reason": "already_fetching", "feed_id": feed_id}

            reserved_notification_delivery_ids: list[uuid.UUID] = []
            with db_session() as db:
                try:
                    parsed_feed_id = uuid.UUID(feed_id)
                except ValueError:
                    return {"status": "skipped", "reason": "invalid_feed_id", "feed_id": feed_id}

                feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
                if feed is None or not feed.enabled:
                    if feed is not None:
                        _clear_feed_dispatch_claim(feed)
                        db.add(feed)
                        db.commit()
                    return {"status": "skipped", "reason": "not_found_or_disabled", "feed_id": feed_id}
                if not force and not _is_feed_due(feed, datetime.now(timezone.utc)):
                    _clear_feed_dispatch_claim(feed)
                    db.add(feed)
                    db.commit()
                    return {"status": "skipped", "reason": "not_due", "feed_id": feed_id}

                headers: dict[str, str] = {}
                if feed.etag:
                    headers["If-None-Match"] = feed.etag
                if feed.last_modified:
                    headers["If-Modified-Since"] = feed.last_modified

                if not is_fetchable_url(feed.url, allow_private_network=settings.allow_private_network_fetch):
                    _mark_feed_failure_and_enqueue_notifications(db, feed, "unsafe_feed_url")
                    return {"status": "error", "feed_id": feed_id}

                try:
                    timeout = httpx.Timeout(
                        connect=settings.feed_connect_timeout_seconds,
                        read=settings.feed_read_timeout_seconds,
                        write=settings.feed_read_timeout_seconds,
                        pool=settings.feed_connect_timeout_seconds,
                    )
                    with build_safe_http_client(
                        timeout=timeout,
                        headers={"User-Agent": settings.fetch_user_agent},
                        allow_private_network=settings.allow_private_network_fetch,
                    ) as client:
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
                                _clear_feed_dispatch_claim(feed)
                                db.add(feed)
                                db.commit()
                                return {"status": "not_modified", "feed_id": feed_id}

                            if status_code != 200:
                                _mark_feed_failure_and_enqueue_notifications(db, feed, f"http_status:{status_code}")
                                return {"status": "error", "feed_id": feed_id}

                            body_chunks: list[bytes] = []
                            body_size = 0
                            for chunk in response.iter_bytes():
                                body_size += len(chunk)
                                if body_size > settings.feed_max_bytes:
                                    raise FeedResponseTooLargeError("feed response exceeds configured cap")
                                body_chunks.append(chunk)
                            body_bytes = b"".join(body_chunks)
                            response_etag = response.headers.get("etag")
                            response_last_modified = response.headers.get("last-modified")
                        finally:
                            response.close()
                except (httpx.HTTPError, SafeFetchError, RedirectError, TimeoutError, CoordinationUnavailableError) as exc:
                    try:
                        logger.warning("feed_fetch_retrying feed_id=%s retries=%s error=%s", feed_id, self.request.retries, exc)
                        raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
                    except MaxRetriesExceededError:
                        logger.error("feed_fetch_failed feed_id=%s error=%s", feed_id, exc)
                        _mark_feed_failure_and_enqueue_notifications(db, feed, f"network_error:{exc}")
                        return {"status": "error", "feed_id": feed_id}
                except FeedResponseTooLargeError as exc:
                    logger.error("feed_fetch_too_large feed_id=%s error=%s", feed_id, exc)
                    _mark_feed_failure_and_enqueue_notifications(db, feed, str(exc))
                    return {"status": "error", "feed_id": feed_id}

                connector = RSSConnector()
                try:
                    parsed_items, _ = connector.poll({"body": body_bytes}, None)
                except RSSFeedParseError as exc:
                    logger.warning("feed_fetch_invalid_content feed_id=%s error=%s", feed_id, exc)
                    _mark_feed_failure_and_enqueue_notifications(db, feed, str(exc))
                    return {"status": "error", "feed_id": feed_id}
                _backfill_feed_metadata_from_body(feed, body_bytes)

                changed_item_ids: list[uuid.UUID] = []
                new_items: list[Item] = []
                for parsed in parsed_items:
                    item, changed, is_new = _upsert_item_from_parsed(db, feed, parsed)
                    if changed:
                        changed_item_ids.append(item.id)
                    if is_new:
                        new_items.append(item)

                if new_items:
                    feed_notification_webhooks = get_matching_notification_webhooks_for_feed(db, feed_id=feed.id)
                    webhook_user_cache: dict[uuid.UUID, User | None] = {}
                    for new_item in new_items:
                        reservation = reserve_new_item_notification_deliveries(
                            db,
                            item=new_item,
                            feed=feed,
                            webhooks=feed_notification_webhooks,
                            user_cache=webhook_user_cache,
                        )
                        reserved_notification_delivery_ids.extend(reservation.delivery_ids)

                now = datetime.now(timezone.utc)
                feed.etag = response_etag or feed.etag
                feed.last_modified = response_last_modified or feed.last_modified
                feed.last_success_at = now
                feed.last_fetch_at = now
                feed.error_count = 0
                feed.last_error = None
                _clear_feed_dispatch_claim(feed)

                db.add(feed)
                db.commit()

            article_enqueue_ok = enqueue_article_fetch_processing(changed_item_ids)
            notification_enqueue_ok = enqueue_notification_webhook_delivery_processing(reserved_notification_delivery_ids)

            return {
                "status": "ok",
                "feed_id": feed_id,
                "new_or_updated_items": len(changed_item_ids),
                "new_items": len(new_items),
                "final_url": final_url,
                "article_enqueue_failed": bool(changed_item_ids) and not article_enqueue_ok,
                "notification_deliveries_reserved": len(reserved_notification_delivery_ids),
                "notification_enqueue_failed": bool(reserved_notification_delivery_ids) and not notification_enqueue_ok,
            }
    except CoordinationUnavailableError as exc:
        logger.warning("feed_fetch_coordination_unavailable feed_id=%s error=%s", feed_id, exc)
        try:
            raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
        except MaxRetriesExceededError:
            with db_session() as db:
                try:
                    parsed_feed_id = uuid.UUID(feed_id)
                except ValueError:
                    return {"status": "error", "feed_id": feed_id}
                feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
                if feed is not None:
                    _mark_feed_failure_and_enqueue_notifications(db, feed, f"coordination_unavailable:{exc}")
            return {"status": "error", "feed_id": feed_id}


@celery_app.task(
    name="app.tasks.feed_tasks.fetch_article",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def fetch_article(self, item_id: str):
    with db_session() as db:
        try:
            parsed_item_id = uuid.UUID(item_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_item_id", "item_id": item_id}

        item = db.scalar(
            select(Item)
            .where(Item.id == parsed_item_id)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            unlocked_item = db.scalar(select(Item).where(Item.id == parsed_item_id))
            if unlocked_item is None:
                return {"status": "skipped", "reason": "not_found", "item_id": item_id}
            return {"status": "skipped", "reason": "concurrent_fetch_in_progress", "item_id": item_id}

        existing_article = db.scalar(select(Article).where(Article.item_id == item.id))
        if existing_article is not None and item.status == "content_fetched":
            _enqueue_classification_task(item_id)
            return {"status": "skipped", "reason": "already_fetched", "item_id": item_id}

        candidate_urls: list[str] = []
        for candidate in (item.canonical_url, item.url):
            if not candidate:
                continue
            normalized_candidate = normalize_url(candidate) or candidate.strip()
            if normalized_candidate and normalized_candidate not in candidate_urls:
                candidate_urls.append(normalized_candidate)

        if not candidate_urls:
            _store_article_error(
                db,
                item,
                final_url="",
                http_status=0,
                content_type=None,
                fetch_ms=0,
                error="missing_article_url",
            )
            _enqueue_classification_task(item_id)
            return {"status": "error", "item_id": item_id}

        start = time.perf_counter()

        last_attempt_url = candidate_urls[0]
        last_response_error: tuple[str, int, str | None, str] | None = None
        last_retryable_error: Exception | None = None
        status_code = 0
        content_type: str | None = None
        final_url = candidate_urls[0]
        body_bytes = b""
        selected_url: str | None = None

        for index, target_url in enumerate(candidate_urls):
            last_attempt_url = target_url
            if not is_fetchable_url(target_url, allow_private_network=settings.allow_private_network_fetch):
                last_response_error = (target_url, 0, None, "unsafe_article_url")
                continue

            domain = urlsplit(target_url).hostname or "unknown"
            try:
                with domain_slot(domain):
                    timeout = httpx.Timeout(
                        connect=settings.article_connect_timeout_seconds,
                        read=settings.article_read_timeout_seconds,
                        write=settings.article_read_timeout_seconds,
                        pool=settings.article_connect_timeout_seconds,
                    )
                    with build_safe_http_client(
                        timeout=timeout,
                        headers={"User-Agent": settings.fetch_user_agent},
                        allow_private_network=settings.allow_private_network_fetch,
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
            except (httpx.HTTPError, TimeoutError, SafeFetchError, RedirectError, CoordinationUnavailableError) as exc:
                last_retryable_error = exc
                if index + 1 < len(candidate_urls):
                    logger.info(
                        "article_fetch_fallback item_id=%s from_url=%s to_url=%s reason=%s",
                        item_id,
                        target_url,
                        candidate_urls[index + 1],
                        exc,
                    )
                    continue
                try:
                    logger.warning("article_fetch_retrying item_id=%s retries=%s error=%s", item_id, self.request.retries, exc)
                    raise self.retry(exc=exc, countdown=min(2**self.request.retries, 300), max_retries=3)
                except MaxRetriesExceededError:
                    logger.error("article_fetch_failed item_id=%s error=%s", item_id, exc)
                    fetch_ms = int((time.perf_counter() - start) * 1000)
                    _store_article_error(
                        db,
                        item,
                        final_url=last_attempt_url,
                        http_status=0,
                        content_type=None,
                        fetch_ms=fetch_ms,
                        error=f"network_or_rate_limit_error:{exc}",
                    )
                    _enqueue_classification_task(item_id)
                    return {"status": "error", "item_id": item_id}
            except ResponseTooLargeError as exc:
                logger.error("article_fetch_too_large item_id=%s target_url=%s error=%s", item_id, target_url, exc)
                last_response_error = (target_url, 0, None, str(exc))
                if index + 1 < len(candidate_urls):
                    logger.info(
                        "article_fetch_fallback item_id=%s from_url=%s to_url=%s reason=%s",
                        item_id,
                        target_url,
                        candidate_urls[index + 1],
                        exc,
                    )
                    continue
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
                _enqueue_classification_task(item_id)
                return {"status": "error", "item_id": item_id}

            if status_code != 200:
                last_response_error = (final_url, status_code, content_type, f"http_status:{status_code}")
                if index + 1 < len(candidate_urls):
                    logger.info(
                        "article_fetch_fallback item_id=%s from_url=%s to_url=%s reason=http_status:%s",
                        item_id,
                        target_url,
                        candidate_urls[index + 1],
                        status_code,
                    )
                    continue
                break

            if "text/html" not in (content_type or "").lower():
                last_response_error = (final_url, status_code, content_type, "non_html_response")
                if index + 1 < len(candidate_urls):
                    logger.info(
                        "article_fetch_fallback item_id=%s from_url=%s to_url=%s reason=non_html_response",
                        item_id,
                        target_url,
                        candidate_urls[index + 1],
                    )
                    continue
                break

            selected_url = target_url
            break

        if selected_url is None:
            fetch_ms = int((time.perf_counter() - start) * 1000)
            if last_response_error is not None:
                _store_article_error(
                    db,
                    item,
                    final_url=last_response_error[0],
                    http_status=last_response_error[1],
                    content_type=last_response_error[2],
                    fetch_ms=fetch_ms,
                    error=last_response_error[3],
                )
            elif last_retryable_error is not None:
                _store_article_error(
                    db,
                    item,
                    final_url=last_attempt_url,
                    http_status=0,
                    content_type=None,
                    fetch_ms=fetch_ms,
                    error=f"network_or_rate_limit_error:{last_retryable_error}",
                )
            else:
                _store_article_error(
                    db,
                    item,
                    final_url=last_attempt_url,
                    http_status=0,
                    content_type=None,
                    fetch_ms=fetch_ms,
                    error="article_fetch_failed",
                )
            _enqueue_classification_task(item_id)
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
            _enqueue_classification_task(item_id)
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
            _enqueue_classification_task(item_id)
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

    _enqueue_classification_task(item_id)
    return {"status": "ok", "item_id": item_id}


@celery_app.task(
    name="app.tasks.feed_tasks.classify_item",
    acks_late=True,
    reject_on_worker_lost=True,
)
def classify_item(item_id: str):
    alert_delivery_ids: list[uuid.UUID] = []
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
        active_ai_settings = load_active_ai_settings(db)
        ai_enrichment_skip_reason = None
        if not active_ai_settings.ai_enabled:
            ai_enrichment_skip_reason = "ai_disabled"
        elif not active_ai_settings.ai_configured:
            ai_enrichment_skip_reason = "ai_not_configured"
        elif not active_ai_settings.auto_enrich_new_items:
            ai_enrichment_skip_reason = "auto_enrich_disabled"
        elif article is None:
            ai_enrichment_skip_reason = "no_article"
        elif not (article.text or "").strip():
            ai_enrichment_skip_reason = "no_article_text"
        queue_ai_enrichment = ai_enrichment_skip_reason is None

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
                feed_id=item.feed_id,
                classification_confidence=row.confidence,
                title=item.title,
                summary=item.summary,
                article_text=article.text if article else None,
                feed_name=feed_name,
                feed_url=feed_url,
                feedback_adjustments=feedback_adjustments,
            )
            if feed is not None:
                alert_delivery_ids = reserve_alert_match_notification_deliveries(
                    db,
                    item=item,
                    feed=feed,
                ).delivery_ids
            db.commit()
            notification_enqueue_ok = enqueue_notification_webhook_delivery_processing(alert_delivery_ids)
            if alert_delivery_ids and not notification_enqueue_ok:
                logger.warning(
                    "classification_notification_enqueue_failed item_id=%s delivery_count=%s",
                    parsed_item_id,
                    len(alert_delivery_ids),
                )
            ioc_enqueue_ok = _safe_enqueue_item_iocs(parsed_item_id)
            if queue_ai_enrichment:
                ai_enqueue_ok = _safe_queue_item_ai_enrichment_run(
                    item_id=parsed_item_id,
                    trigger_source=AI_TRIGGER_AUTO,
                    reason=None,
                    model=getattr(active_ai_settings, "model", None),
                    metadata={"category": row.primary_category, "feed_name": feed_name, "force": False},
                )
            else:
                ai_enqueue_ok = True
                _record_skipped_item_ai_enrichment_run(
                    item_id=parsed_item_id,
                    trigger_source=AI_TRIGGER_AUTO,
                    reason=ai_enrichment_skip_reason or "not_eligible",
                    model=getattr(active_ai_settings, "model", None),
                    metadata={"category": row.primary_category, "feed_name": feed_name},
                )
            return {
                "status": "skipped",
                "reason": "up_to_date",
                "item_id": item_id,
                "category": row.primary_category,
                "notification_enqueue_failed": bool(alert_delivery_ids) and not notification_enqueue_ok,
                "ioc_enqueue_failed": not ioc_enqueue_ok,
                "ai_enqueue_failed": queue_ai_enrichment and not ai_enqueue_ok,
            }

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
            feed_id=item.feed_id,
            classification_confidence=result.confidence,
            title=item.title,
            summary=item.summary,
            article_text=article.text if article else None,
            feed_name=feed_name,
            feed_url=feed_url,
            feedback_adjustments=feedback_adjustments,
        )
        if feed is not None:
            alert_delivery_ids = reserve_alert_match_notification_deliveries(
                db,
                item=item,
                feed=feed,
            ).delivery_ids
        db.commit()

    notification_enqueue_ok = enqueue_notification_webhook_delivery_processing(alert_delivery_ids)
    if alert_delivery_ids and not notification_enqueue_ok:
        logger.warning(
            "classification_notification_enqueue_failed item_id=%s delivery_count=%s",
            parsed_item_id,
            len(alert_delivery_ids),
        )
    ioc_enqueue_ok = _safe_enqueue_item_iocs(parsed_item_id)
    if queue_ai_enrichment:
        ai_enqueue_ok = _safe_queue_item_ai_enrichment_run(
            item_id=parsed_item_id,
            trigger_source=AI_TRIGGER_AUTO,
            reason=None,
            model=getattr(active_ai_settings, "model", None),
            metadata={"category": result.primary_category, "feed_name": feed_name, "force": False},
        )
    else:
        ai_enqueue_ok = True
        _record_skipped_item_ai_enrichment_run(
            item_id=parsed_item_id,
            trigger_source=AI_TRIGGER_AUTO,
            reason=ai_enrichment_skip_reason or "not_eligible",
            model=getattr(active_ai_settings, "model", None),
            metadata={"category": result.primary_category, "feed_name": feed_name},
        )
    return {
        "status": "ok",
        "item_id": item_id,
        "category": result.primary_category,
        "notification_enqueue_failed": bool(alert_delivery_ids) and not notification_enqueue_ok,
        "ioc_enqueue_failed": not ioc_enqueue_ok,
        "ai_enqueue_failed": queue_ai_enrichment and not ai_enqueue_ok,
    }


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.generate_item_ai_enrichment",
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_item_ai_enrichment_task(self, item_id: str, force: bool = False, task_run_id: str | None = None):
    with db_session() as db:
        parsed_run_id = None
        if task_run_id:
            try:
                parsed_run_id = uuid.UUID(task_run_id)
            except ValueError:
                parsed_run_id = None
        if parsed_run_id:
            started_run = start_ai_task_run(
                db,
                run_id=parsed_run_id,
                worker_name=getattr(self.request, "hostname", None),
                celery_task_id=getattr(self.request, "id", None),
                metadata_updates={"force": bool(force)},
            )
            db.commit()
            if not _task_run_claimed_by_current_worker(started_run, celery_task_id=getattr(self.request, "id", None)):
                return {"status": "skipped", "reason": "already_running", "item_id": item_id}
            stop_reason = ai_task_run_stop_reason(started_run)
            if stop_reason is not None:
                if stop_reason == "canceled":
                    finish_ai_task_run(
                        db,
                        run_id=parsed_run_id,
                        status=AI_STATUS_SKIPPED,
                        reason="canceled",
                        worker_name=getattr(self.request, "hostname", None),
                        metadata_updates={"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
                    )
                    db.commit()
                return {"status": "skipped", "reason": stop_reason, "item_id": item_id}

        try:
            parsed_item_id = uuid.UUID(item_id)
        except ValueError:
            if parsed_run_id:
                finish_ai_task_run(
                    db,
                    run_id=parsed_run_id,
                    status=AI_STATUS_SKIPPED,
                    reason="invalid_item_id",
                    worker_name=getattr(self.request, "hostname", None),
                )
                db.commit()
            return {"status": "skipped", "reason": "invalid_item_id", "item_id": item_id}

        try:
            result = run_item_ai_enrichment(db, item_id=parsed_item_id, force=force, task_run_id=parsed_run_id)
        except Exception as exc:
            db.rollback()
            if parsed_run_id:
                finish_ai_task_run(
                    db,
                    run_id=parsed_run_id,
                    status=AI_STATUS_ERROR,
                    reason="unexpected_error",
                    error=str(exc),
                    worker_name=getattr(self.request, "hostname", None),
                )
                db.commit()
            logger.exception("AI enrichment task failed unexpectedly for item %s", item_id)
            return {"status": "error", "reason": "unexpected_error", "item_id": item_id}
        if parsed_run_id:
            finish_ai_task_run(
                db,
                run_id=parsed_run_id,
                status=AI_STATUS_READY if result.status == "ready" else AI_STATUS_ERROR if result.status == "error" else AI_STATUS_SKIPPED,
                reason=result.reason,
                error=result.enrichment.error if result.enrichment is not None and result.status == "error" else None,
                worker_name=getattr(self.request, "hostname", None),
                model=result.enrichment.model if result.enrichment is not None else None,
                prompt_tokens=result.enrichment.prompt_tokens if result.enrichment is not None else None,
                completion_tokens=result.enrichment.completion_tokens if result.enrichment is not None else None,
                total_tokens=result.enrichment.total_tokens if result.enrichment is not None else None,
                latency_ms=result.enrichment.latency_ms if result.enrichment is not None else None,
                prompt_char_count=result.prompt_char_count,
                response_char_count=result.response_char_count,
                input_text_chars=result.input_text_chars,
                metadata_updates={
                    "summary_available": bool(result.enrichment.summary_text) if result.enrichment is not None else False,
                    "relevance_label": result.enrichment.relevance_label if result.enrichment is not None else None,
                },
            )
        db.commit()
        if result.enrichment is None:
            return {"status": "skipped", "reason": result.reason or "not_eligible", "item_id": item_id}
        return {"status": result.status, "reason": result.reason, "item_id": item_id}


def _parse_uuid_text_list(values: list[str] | None) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in values or []:
        try:
            candidate = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed.append(candidate)
    return parsed


def _parse_datetime_text(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.reprocess_recent_ai_items",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reprocess_recent_ai_items(
    self,
    days: int | None,
    limit: int,
    start_time: str | None = None,
    end_time: str | None = None,
    feed_ids: list[str] | None = None,
    item_ids: list[str] | None = None,
    task_run_id: str | None = None,
    actor_user_id: str | None = None,
):
    runtime_settings = get_settings()
    effective_limit = max(1, min(int(limit), int(runtime_settings.dispatch_ai_reprocess_batch_size)))
    parsed_start_time = _parse_datetime_text(start_time)
    parsed_end_time = _parse_datetime_text(end_time)
    parsed_feed_ids = _parse_uuid_text_list(feed_ids)
    parsed_item_ids = _parse_uuid_text_list(item_ids)
    requested_item_count = len(parsed_item_ids)
    if requested_item_count > effective_limit:
        parsed_item_ids = parsed_item_ids[:effective_limit]
    cutoff = None
    if parsed_start_time is None and parsed_end_time is None and not parsed_item_ids:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 7)))

    with db_session() as db:
        parsed_run_id = None
        parsed_actor_user_id = None
        if task_run_id:
            try:
                parsed_run_id = uuid.UUID(task_run_id)
            except ValueError:
                parsed_run_id = None
        if actor_user_id:
            try:
                parsed_actor_user_id = uuid.UUID(actor_user_id)
            except ValueError:
                parsed_actor_user_id = None
        if parsed_run_id:
            started_run = start_ai_task_run(
                db,
                run_id=parsed_run_id,
                worker_name=getattr(self.request, "hostname", None),
                celery_task_id=getattr(self.request, "id", None),
                metadata_updates={
                    "days": int(days or 0) if days is not None else None,
                    "limit": int(limit),
                    "effective_limit": effective_limit,
                    "start_time": parsed_start_time.isoformat() if parsed_start_time else None,
                    "end_time": parsed_end_time.isoformat() if parsed_end_time else None,
                    "feed_ids": [str(feed_id) for feed_id in parsed_feed_ids],
                    "explicit_item_count": len(parsed_item_ids),
                    "truncated_item_count": max(0, requested_item_count - len(parsed_item_ids)),
                },
            )
            db.commit()
            if not _task_run_claimed_by_current_worker(started_run, celery_task_id=getattr(self.request, "id", None)):
                return {"queued": 0, "queue_errors": 0, "run_id": task_run_id, "reason": "already_running"}
            stop_reason = ai_task_run_stop_reason(started_run)
            if stop_reason is not None:
                if stop_reason == "canceled":
                    finish_ai_task_run(
                        db,
                        run_id=parsed_run_id,
                        status=AI_STATUS_SKIPPED,
                        reason="canceled",
                        worker_name=getattr(self.request, "hostname", None),
                        metadata_updates={"cancel_observed_at": datetime.now(timezone.utc).isoformat()},
                    )
                    db.commit()
                return {"queued": 0, "reason": stop_reason}

        active_ai_settings = load_active_ai_settings(db)
        if not active_ai_settings.ai_enabled:
            if parsed_run_id:
                finish_ai_task_run(
                    db,
                    run_id=parsed_run_id,
                    status=AI_STATUS_SKIPPED,
                    reason="ai_disabled",
                    worker_name=getattr(self.request, "hostname", None),
                )
                db.commit()
            return {"queued": 0, "reason": "ai_disabled"}
        if not active_ai_settings.ai_configured:
            if parsed_run_id:
                finish_ai_task_run(
                    db,
                    run_id=parsed_run_id,
                    status=AI_STATUS_SKIPPED,
                    reason="ai_not_configured",
                    worker_name=getattr(self.request, "hostname", None),
                )
                db.commit()
            return {"queued": 0, "reason": "ai_not_configured"}

        selection_query = select(Item.id).join(Article, Article.item_id == Item.id).where(Article.text.is_not(None))
        if parsed_item_ids:
            selection_query = selection_query.where(Item.id.in_(parsed_item_ids))
            selected_item_ids = set(db.scalars(selection_query).all())
            item_ids = [item_id for item_id in parsed_item_ids if item_id in selected_item_ids]
        else:
            if cutoff is not None:
                selection_query = selection_query.where(Item.first_seen_at >= cutoff)
            if parsed_start_time is not None:
                selection_query = selection_query.where(Item.first_seen_at >= parsed_start_time)
            if parsed_end_time is not None:
                selection_query = selection_query.where(Item.first_seen_at <= parsed_end_time)
            if parsed_feed_ids:
                selection_query = selection_query.where(Item.feed_id.in_(parsed_feed_ids))
            selection_query = selection_query.limit(effective_limit)
            item_ids = db.scalars(selection_query.order_by(Item.first_seen_at.desc())).all()

        if parsed_run_id:
            run = db.scalar(select(AITaskRun).where(AITaskRun.id == parsed_run_id))
            if run is not None:
                run.target_count = len(item_ids)
                db.add(run)
                record_ai_task_event(
                    db,
                    run_id=parsed_run_id,
                    event_type="selection_complete",
                    payload={
                        "target_count": len(item_ids),
                        "days": int(days or 0) if days is not None else None,
                        "limit": int(limit),
                        "effective_limit": effective_limit,
                        "start_time": parsed_start_time.isoformat() if parsed_start_time else None,
                        "end_time": parsed_end_time.isoformat() if parsed_end_time else None,
                        "feed_ids": [str(feed_id) for feed_id in parsed_feed_ids],
                        "explicit_item_count": len(parsed_item_ids),
                        "truncated_item_count": max(0, requested_item_count - len(parsed_item_ids)),
                    },
                )
                db.commit()
        if not item_ids:
            if parsed_run_id:
                finish_ai_task_run(
                    db,
                    run_id=parsed_run_id,
                    status=AI_STATUS_SKIPPED,
                    reason="no_items",
                    worker_name=getattr(self.request, "hostname", None),
                    metadata_updates={
                        "days": int(days or 0) if days is not None else None,
                        "limit": int(limit),
                        "start_time": parsed_start_time.isoformat() if parsed_start_time else None,
                        "end_time": parsed_end_time.isoformat() if parsed_end_time else None,
                        "feed_ids": [str(feed_id) for feed_id in parsed_feed_ids],
                        "explicit_item_count": len(parsed_item_ids),
                        "truncated_item_count": max(0, requested_item_count - len(parsed_item_ids)),
                    },
                )
                db.commit()
            return {"queued": 0, "reason": "no_items"}

    queued = 0
    queue_errors = 0
    for item_id_value in item_ids:
        stop_reason = _get_ai_run_stop_reason(parsed_run_id)
        if stop_reason is not None:
            if parsed_run_id:
                with db_session() as db:
                    record_ai_task_event(
                        db,
                        run_id=parsed_run_id,
                        event_type="queueing_stopped",
                        payload={"reason": stop_reason, "queued": queued, "queue_errors": queue_errors},
                    )
                    if stop_reason == "canceled":
                        finish_ai_task_run(
                            db,
                            run_id=parsed_run_id,
                            status=AI_STATUS_SKIPPED,
                            reason="canceled",
                            worker_name=getattr(self.request, "hostname", None),
                            metadata_updates={
                                "queued": queued,
                                "queue_errors": queue_errors,
                                "cancel_observed_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    db.commit()
            return {"queued": queued, "queue_errors": queue_errors, "run_id": task_run_id, "reason": stop_reason}
        queued_ok = _safe_queue_item_ai_enrichment_run(
            item_id=item_id_value,
            trigger_source=AI_TRIGGER_MANUAL,
            reason=None,
            actor_user_id=parsed_actor_user_id,
            parent_run_id=parsed_run_id,
            force=True,
            model=active_ai_settings.model,
            metadata={
                "days": int(days or 0) if days is not None else None,
                "limit": int(limit),
                "parent_task": "reprocess",
                "start_time": parsed_start_time.isoformat() if parsed_start_time else None,
                "end_time": parsed_end_time.isoformat() if parsed_end_time else None,
                "feed_ids": [str(feed_id) for feed_id in parsed_feed_ids],
                "explicit_item_count": len(parsed_item_ids),
            },
        )
        if queued_ok:
            queued += 1
        else:
            queue_errors += 1
    if parsed_run_id:
        with db_session() as db:
            record_ai_task_event(
                db,
                run_id=parsed_run_id,
                event_type="children_queued",
                payload={"queued": queued, "queue_errors": queue_errors},
            )
            db.commit()
    return {"queued": queued, "queue_errors": queue_errors, "run_id": task_run_id}


@celery_app.task(
    name="app.tasks.feed_tasks.extract_item_iocs",
    acks_late=True,
    reject_on_worker_lost=True,
)
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
            feed_id=item.feed_id,
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


@celery_app.task(
    name="app.tasks.feed_tasks.reapply_recent_item_tags",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reapply_recent_item_tags(days: int = 30, limit: int = 0):
    if days <= 0:
        return {"status": "skipped", "reason": "invalid_days", "days": days}
    if limit < 0:
        return {"status": "skipped", "reason": "invalid_limit", "limit": limit}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    processed = 0

    with db_session() as db:
        query = (
            select(Item.id)
            .where(Item.first_seen_at >= cutoff)
            .order_by(Item.first_seen_at.desc())
        )
        if limit:
            query = query.limit(limit)

        item_ids = list(db.scalars(query).all())
        for item_id_value in item_ids:
            item = db.scalar(select(Item).where(Item.id == item_id_value))
            if item is None:
                continue

            article = db.scalar(select(Article).where(Article.item_id == item.id))
            classification = db.scalar(select(ItemClassification).where(ItemClassification.item_id == item.id))
            feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
            if feed is None:
                continue

            if classification is None:
                result = classify_item_content(
                    title=item.title,
                    summary=item.summary,
                    article_text=article.text if article else None,
                    feed_name=feed.name,
                )
                classification = ItemClassification(item_id=item.id)
                classification.primary_category = result.primary_category
                classification.secondary_categories = result.secondary_categories
                classification.confidence = result.confidence
                classification.scores_json = result.scores
                classification.matched_terms_json = result.matched_terms
                classification.source_hash = result.source_hash
                classification.rules_version = result.rules_version
                classification.classified_at = datetime.now(timezone.utc)
                db.add(classification)

            feedback_adjustments = load_feedback_adjustments(
                db,
                tag_names=[classification.primary_category, *(classification.secondary_categories or [])],
            )
            sync_item_algorithm_tags(
                db,
                item_id=item.id,
                primary_category=classification.primary_category,
                secondary_categories=classification.secondary_categories,
                feed_id=item.feed_id,
                classification_confidence=classification.confidence,
                title=item.title,
                summary=item.summary,
                article_text=article.text if article else None,
                feed_name=feed.name,
                feed_url=feed.url,
                feedback_adjustments=feedback_adjustments,
            )
            processed += 1

        db.commit()

    return {
        "status": "ok",
        "days": days,
        "limit": limit,
        "processed": processed,
    }


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


def _mark_feed_failure_and_enqueue_notifications(db: Session, feed: Feed, error: str) -> bool:
    _mark_feed_failure(db, feed, error)
    reservation = reserve_feed_failing_notification_deliveries(db, feed=feed)
    db.commit()
    return enqueue_notification_webhook_delivery_processing(reservation.delivery_ids)
