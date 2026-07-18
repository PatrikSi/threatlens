from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_delivery import mark_integration_delivery_dead_letter
from app.services.integration_events import emit_integration_event
from app.services.feed_pipeline import mark_feed_failure
from app.services.notification_webhooks import (
    FEED_FAILING_NOTIFICATION_THRESHOLD,
    FailedWebhookContext,
    build_alert_match_context_for_item,
    list_recoverable_notification_delivery_ids,
    process_notification_webhook_delivery,
    reserve_alert_match_notification_deliveries,
    reserve_feed_failing_notification_deliveries,
    reserve_new_item_notification_deliveries,
    reserve_notification_webhook_delivery,
    reserve_retryable_notification_webhook_delivery,
    reserve_webhook_failed_notification_deliveries,
)
from app.services.smtp_integration import SMTPDispatchResult, dispatch_smtp_notification
from app.tasks.celery_app import celery_app
from app.tasks.feed_task_notifications import (
    dispatch_feed_failing_notification_batch,
    dispatch_item_notification_batch,
    dispatch_webhook_failed_notification_batch,
    enqueue_notification_delivery_batches,
    process_reserved_notification_deliveries as process_reserved_notification_deliveries_impl,
)
from app.tasks.integration_tasks import enqueue_integration_event_routing
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)
settings = get_settings()


def _mark_failed_webhook_delivery_dead_letter(
    db: Session,
    failed_delivery: NotificationWebhookDelivery,
) -> None:
    if failed_delivery.integration_delivery_id is None:
        return
    mark_integration_delivery_dead_letter(
        db,
        delivery_id=failed_delivery.integration_delivery_id,
        error_code="attempts_exhausted",
        error_message=failed_delivery.error or "Webhook delivery attempts were exhausted.",
    )


def _emit_failed_webhook_integration_event(
    db: Session,
    failed_delivery: NotificationWebhookDelivery,
) -> uuid.UUID:
    event = emit_integration_event(
        db,
        event_type="webhook_failed",
        source_type="notification_webhook_delivery",
        source_id=failed_delivery.id,
        idempotency_key=f"webhook_delivery:{failed_delivery.id}:webhook_failed:v1",
        payload={
            "source_delivery_id": str(failed_delivery.id),
            "feed_id": str(failed_delivery.feed_id) if failed_delivery.feed_id else None,
            "owner_user_id": str(failed_delivery.user_id),
        },
    )
    return event.id


def _process_reserved_notification_deliveries(
    db: Session,
    delivery_ids: list[uuid.UUID],
) -> tuple[int, int]:
    return process_reserved_notification_deliveries_impl(
        db,
        delivery_ids,
        process_delivery=lambda session, *, delivery_id: process_notification_webhook_delivery(
            session,
            delivery_id=delivery_id,
            commit_outcome=False,
        ),
        reserve_retryable_delivery=reserve_retryable_notification_webhook_delivery,
        reserve_failed_delivery_notifications=None,
        enqueue_delivery_processing=enqueue_notification_webhook_delivery_processing,
        logger=logger,
        emit_failed_delivery_event=_emit_failed_webhook_integration_event,
        enqueue_event_routing=enqueue_integration_event_routing,
        mark_dead_letter=_mark_failed_webhook_delivery_dead_letter,
    )


def enqueue_notification_webhook_delivery_processing(
    delivery_ids: list[uuid.UUID],
    *,
    countdown: int | None = None,
) -> bool:
    return enqueue_notification_delivery_batches(
        delivery_ids,
        batch_size=settings.notification_delivery_enqueue_batch_size,
        delivery_task=process_notification_webhook_deliveries,
        logger=logger,
        countdown=countdown,
    )


def _enqueue_smtp_notification_items(item_ids: list[uuid.UUID], *, task: Any) -> bool:
    all_enqueued = True
    for item_id in item_ids:
        try:
            task.delay(str(item_id))
        except Exception as exc:
            all_enqueued = False
            logger.exception(
                "smtp_notification_enqueue_failed task=%s item_id=%s error=%s",
                getattr(task, "name", "unknown"),
                item_id,
                exc,
            )
    return all_enqueued


def _enqueue_smtp_new_item_notifications(item_ids: list[uuid.UUID]) -> bool:
    return _enqueue_smtp_notification_items(item_ids, task=dispatch_smtp_new_item_notification)


def _enqueue_smtp_alert_match_notification(item_id: uuid.UUID) -> bool:
    return _enqueue_smtp_notification_items([item_id], task=dispatch_smtp_alert_match_notification)


def _enqueue_smtp_feed_failing_notification(feed_id: uuid.UUID) -> bool:
    try:
        dispatch_smtp_feed_failing_notification.delay(str(feed_id))
    except Exception as exc:
        logger.exception("smtp_feed_failing_notification_enqueue_failed feed_id=%s error=%s", feed_id, exc)
        return False
    return True


def _smtp_task_response(result: SMTPDispatchResult, **identifiers: str) -> dict[str, Any]:
    response = {
        "status": result.status,
        **identifiers,
        "reason": result.reason,
        "sent": 1 if result.sent else 0,
        "failed": 1 if result.failed else 0,
        "skipped": 1 if result.skipped else 0,
    }
    if result.delivery is not None:
        response.update(
            {
                "recipient_count": result.delivery.recipient_count,
                "accepted_count": result.delivery.accepted_count,
                "error_code": result.delivery.error_code,
            }
        )
    return response


def _smtp_skipped_task_response(reason: str | None, **identifiers: str) -> dict[str, Any]:
    return _smtp_task_response(SMTPDispatchResult(status="skipped", reason=reason), **identifiers)


def _safe_enqueue_smtp_task(task: Any, value: str) -> bool:
    try:
        task.delay(value)
    except Exception as exc:
        logger.exception(
            "smtp_notification_enqueue_failed task=%s value=%s error=%s",
            getattr(task, "name", "unknown"),
            value,
            exc,
        )
        return False
    return True


def _load_item_and_feed_for_notification(db: Session, item_id: str) -> tuple[Item | None, Feed | None, str | None]:
    try:
        parsed_item_id = uuid.UUID(item_id)
    except ValueError:
        return None, None, "invalid_item_id"

    item = db.scalar(select(Item).where(Item.id == parsed_item_id))
    if item is None:
        return None, None, "item_not_found"

    feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
    if feed is None:
        return item, None, "feed_not_found"
    return item, feed, None


def _feed_failing_smtp_scope_key(now: datetime) -> str:
    current = _coerce_utc(now)
    return f"{current.date().isoformat()}:{current.hour // 12}"


def mark_feed_failure_and_enqueue_notifications(db: Session, feed: Feed, error: str) -> bool:
    mark_feed_failure(db, feed, error)
    integration_event_ids: list[uuid.UUID] = []
    if int(feed.error_count or 0) >= FEED_FAILING_NOTIFICATION_THRESHOLD:
        scope_key = _feed_failing_smtp_scope_key(datetime.now(timezone.utc))
        event = emit_integration_event(
            db,
            event_type="feed_failing",
            source_type="feed",
            source_id=feed.id,
            idempotency_key=f"feed:{feed.id}:feed_failing:{scope_key}:v1",
            payload={
                "feed_id": str(feed.id),
                "scope_key": scope_key,
                "error_count": int(feed.error_count or 0),
            },
        )
        integration_event_ids.append(event.id)
    db.commit()
    return enqueue_integration_event_routing(integration_event_ids)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
        except (AttributeError, TypeError, ValueError):
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


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_smtp_new_item_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_smtp_new_item_notification(item_id: str):
    with db_session() as db:
        item, feed, reason = _load_item_and_feed_for_notification(db, item_id)
        if item is None or feed is None:
            return _smtp_skipped_task_response(reason, item_id=item_id)
        result = dispatch_smtp_notification(db, event_type="rss_item_new", feed=feed, item=item)
        db.commit()
        return _smtp_task_response(result, item_id=item_id)


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_smtp_alert_match_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_smtp_alert_match_notification(item_id: str):
    with db_session() as db:
        item, feed, reason = _load_item_and_feed_for_notification(db, item_id)
        if item is None or feed is None:
            return _smtp_skipped_task_response(reason, item_id=item_id)

        alert_context = build_alert_match_context_for_item(db, item=item)
        if alert_context is None:
            return _smtp_skipped_task_response("no_alert_match", item_id=item_id)

        result = dispatch_smtp_notification(
            db,
            event_type="alert_match",
            feed=feed,
            item=item,
            alert_context=alert_context,
        )
        db.commit()
        return _smtp_task_response(result, item_id=item_id)


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_smtp_feed_failing_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_smtp_feed_failing_notification(feed_id: str):
    try:
        parsed_feed_id = uuid.UUID(feed_id)
    except (AttributeError, TypeError, ValueError):
        return _smtp_skipped_task_response("invalid_feed_id", feed_id=feed_id)

    with db_session() as db:
        feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
        if feed is None:
            return _smtp_skipped_task_response("feed_not_found", feed_id=feed_id)
        if int(feed.error_count or 0) < FEED_FAILING_NOTIFICATION_THRESHOLD:
            return _smtp_skipped_task_response("below_failure_threshold", feed_id=feed_id)

        result = dispatch_smtp_notification(
            db,
            event_type="feed_failing",
            feed=feed,
            scope_key=_feed_failing_smtp_scope_key(datetime.now(timezone.utc)),
        )
        db.commit()
        return _smtp_task_response(result, feed_id=feed_id)


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_smtp_webhook_failed_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_smtp_webhook_failed_notification(delivery_id: str):
    try:
        parsed_delivery_id = uuid.UUID(delivery_id)
    except (AttributeError, TypeError, ValueError):
        return _smtp_skipped_task_response("invalid_delivery_id", delivery_id=delivery_id)

    with db_session() as db:
        failed_delivery = db.scalar(
            select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.id == parsed_delivery_id)
        )
        if failed_delivery is None:
            return _smtp_skipped_task_response("delivery_not_found", delivery_id=delivery_id)
        if failed_delivery.success or failed_delivery.event_type_snapshot == "webhook_failed":
            return _smtp_skipped_task_response("not_eligible", delivery_id=delivery_id)

        source_webhook = db.scalar(select(NotificationWebhook).where(NotificationWebhook.id == failed_delivery.webhook_id))
        if source_webhook is None:
            return _smtp_skipped_task_response("source_webhook_not_found", delivery_id=delivery_id)

        feed = db.scalar(select(Feed).where(Feed.id == failed_delivery.feed_id)) if failed_delivery.feed_id else None
        failed_context = FailedWebhookContext(
            id=source_webhook.id,
            name=source_webhook.name,
            event_type=failed_delivery.event_type_snapshot,
            status_code=failed_delivery.status_code,
            error=failed_delivery.error,
            attempted_at=failed_delivery.attempted_at,
        )
        result = dispatch_smtp_notification(
            db,
            event_type="webhook_failed",
            feed=feed,
            failed_webhook_context=failed_context,
            source_delivery_id=failed_delivery.id,
        )
        db.commit()
        return _smtp_task_response(result, delivery_id=delivery_id)


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_new_item_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_new_item_notification_webhooks(item_id: str):
    with db_session() as db:
        webhook_result = dispatch_item_notification_batch(
            db,
            item_id,
            reserve_deliveries=reserve_new_item_notification_deliveries,
            process_reserved_deliveries=_process_reserved_notification_deliveries,
        )
    smtp_enqueue_ok = _safe_enqueue_smtp_task(dispatch_smtp_new_item_notification, item_id)
    return {**webhook_result, "smtp_enqueue_failed": not smtp_enqueue_ok}


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_alert_match_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_alert_match_notification_webhooks(item_id: str):
    with db_session() as db:
        webhook_result = dispatch_item_notification_batch(
            db,
            item_id,
            reserve_deliveries=reserve_alert_match_notification_deliveries,
            process_reserved_deliveries=_process_reserved_notification_deliveries,
        )
    smtp_enqueue_ok = _safe_enqueue_smtp_task(dispatch_smtp_alert_match_notification, item_id)
    return {**webhook_result, "smtp_enqueue_failed": not smtp_enqueue_ok}


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_feed_failing_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_feed_failing_notification_webhooks(feed_id: str):
    with db_session() as db:
        webhook_result = dispatch_feed_failing_notification_batch(
            db,
            feed_id,
            failure_threshold=FEED_FAILING_NOTIFICATION_THRESHOLD,
            reserve_deliveries=reserve_feed_failing_notification_deliveries,
            process_reserved_deliveries=_process_reserved_notification_deliveries,
        )
    smtp_enqueue_ok = _safe_enqueue_smtp_task(dispatch_smtp_feed_failing_notification, feed_id)
    return {**webhook_result, "smtp_enqueue_failed": not smtp_enqueue_ok}


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_webhook_failed_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_webhook_failed_notification_webhooks(delivery_id: str):
    with db_session() as db:
        return dispatch_webhook_failed_notification_batch(
            db,
            delivery_id,
            reserve_deliveries=reserve_webhook_failed_notification_deliveries,
            process_reserved_deliveries=_process_reserved_notification_deliveries,
        )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_daily_digest_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_daily_digest_notification_webhooks():
    with db_session() as db:
        digest_day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        digest_scope_key = digest_day_start.date().isoformat()
        event = emit_integration_event(
            db,
            event_type="daily_digest",
            source_type="digest_window",
            source_id=digest_scope_key,
            idempotency_key=f"daily_digest:{digest_scope_key}:v1",
            payload={"scope_key": digest_scope_key},
        )
        db.commit()
    enqueue_ok = enqueue_integration_event_routing([event.id])
    return {
        "status": "ok",
        "matched_webhooks": 0,
        "delivered": 0,
        "failed": 0,
        "skipped": 0,
        "smtp_status": "queued" if enqueue_ok else "pending",
        "smtp_reason": None if enqueue_ok else "event_enqueue_failed",
        "smtp_sent": 0,
        "smtp_failed": 0,
        "smtp_skipped": 0,
        "integration_event_id": str(event.id),
        "enqueue_failed": not enqueue_ok,
    }


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


__all__ = [
    "_emit_failed_webhook_integration_event",
    "_enqueue_smtp_alert_match_notification",
    "_enqueue_smtp_feed_failing_notification",
    "_enqueue_smtp_new_item_notifications",
    "_feed_failing_smtp_scope_key",
    "_mark_failed_webhook_delivery_dead_letter",
    "_process_reserved_notification_deliveries",
    "dispatch_alert_match_notification_webhooks",
    "dispatch_daily_digest_notification_webhooks",
    "dispatch_feed_failing_notification_webhooks",
    "dispatch_new_item_notification_webhooks",
    "dispatch_pending_notification_webhook_deliveries",
    "dispatch_smtp_alert_match_notification",
    "dispatch_smtp_feed_failing_notification",
    "dispatch_smtp_new_item_notification",
    "dispatch_smtp_webhook_failed_notification",
    "dispatch_webhook_failed_notification_webhooks",
    "enqueue_notification_webhook_delivery_processing",
    "mark_feed_failure_and_enqueue_notifications",
    "process_notification_webhook_deliveries",
    "reserve_notification_webhook_delivery",
]
