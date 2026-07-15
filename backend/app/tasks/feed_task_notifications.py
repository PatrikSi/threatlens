from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.notification_webhooks import NotificationDeliveryReservationBatch
from app.services.notification_delivery_processing import (
    process_reserved_notification_deliveries as process_reserved_notification_deliveries_transactionally,
)


def enqueue_notification_delivery_batches(
    delivery_ids: list[uuid.UUID],
    *,
    batch_size: int,
    delivery_task: Any,
    logger: logging.Logger,
    countdown: int | None = None,
) -> bool:
    if not delivery_ids:
        return True

    effective_batch_size = max(1, int(batch_size))
    all_enqueued = True

    for delivery_id_chunk in _chunk_uuid_list(delivery_ids, effective_batch_size):
        serialized_ids = [str(delivery_id) for delivery_id in delivery_id_chunk]
        try:
            if countdown is not None and int(countdown) > 0:
                delivery_task.apply_async(args=[serialized_ids], countdown=int(countdown))
            else:
                delivery_task.delay(serialized_ids)
        except Exception as exc:
            all_enqueued = False
            logger.exception(
                "notification_webhook_delivery_enqueue_failed delivery_count=%s error=%s",
                len(delivery_id_chunk),
                exc,
            )

    return all_enqueued


def process_reserved_notification_deliveries(
    db: Session,
    delivery_ids: list[uuid.UUID],
    *,
    process_delivery: Callable[..., Any],
    reserve_retryable_delivery: Callable[..., Any],
    reserve_failed_delivery_notifications: Callable[..., NotificationDeliveryReservationBatch] | None,
    enqueue_delivery_processing: Callable[..., bool],
    logger: logging.Logger,
    notify_failed_delivery: Callable[[NotificationWebhookDelivery], None] | None = None,
    emit_failed_delivery_event: Callable[[Session, NotificationWebhookDelivery], uuid.UUID] | None = None,
    enqueue_event_routing: Callable[[list[uuid.UUID]], bool] | None = None,
    mark_dead_letter: Callable[[Session, NotificationWebhookDelivery], None] | None = None,
) -> tuple[int, int]:
    result = process_reserved_notification_deliveries_transactionally(
        db,
        delivery_ids,
        process_delivery=process_delivery,
        reserve_retryable_delivery=reserve_retryable_delivery,
        reserve_failed_delivery_notifications=reserve_failed_delivery_notifications,
        logger=logger,
        emit_failed_delivery_event=emit_failed_delivery_event,
        mark_dead_letter=mark_dead_letter,
    )
    for followup in result.followup_deliveries:
        enqueue_delivery_processing(
            [followup.delivery_id],
            countdown=followup.countdown_seconds,
        )
    if result.followup_event_ids and enqueue_event_routing is not None:
        enqueue_event_routing(list(result.followup_event_ids))
    if notify_failed_delivery is not None:
        for failed_delivery_id in result.terminal_failed_delivery_ids:
            failed_delivery = db.get(NotificationWebhookDelivery, failed_delivery_id)
            if failed_delivery is not None:
                notify_failed_delivery(failed_delivery)
    return result.delivered, result.failed


def dispatch_item_notification_batch(
    db: Session,
    item_id: str,
    *,
    reserve_deliveries: Callable[..., NotificationDeliveryReservationBatch],
    process_reserved_deliveries: Callable[[Session, list[uuid.UUID]], tuple[int, int]],
) -> dict[str, Any]:
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

    reservation = reserve_deliveries(db, item=item, feed=feed)
    return _complete_reserved_notification_dispatch(
        db,
        reservation=reservation,
        process_reserved_deliveries=process_reserved_deliveries,
        identifier_key="item_id",
        identifier_value=item_id,
    )


def dispatch_feed_failing_notification_batch(
    db: Session,
    feed_id: str,
    *,
    failure_threshold: int,
    reserve_deliveries: Callable[..., NotificationDeliveryReservationBatch],
    process_reserved_deliveries: Callable[[Session, list[uuid.UUID]], tuple[int, int]],
) -> dict[str, Any]:
    try:
        parsed_feed_id = uuid.UUID(feed_id)
    except ValueError:
        return {"status": "skipped", "reason": "invalid_feed_id", "feed_id": feed_id}

    feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
    if feed is None:
        return {"status": "skipped", "reason": "feed_not_found", "feed_id": feed_id}
    if int(feed.error_count or 0) < int(failure_threshold):
        return {"status": "skipped", "reason": "below_failure_threshold", "feed_id": feed_id}

    reservation = reserve_deliveries(db, feed=feed)
    return _complete_reserved_notification_dispatch(
        db,
        reservation=reservation,
        process_reserved_deliveries=process_reserved_deliveries,
        identifier_key="feed_id",
        identifier_value=feed_id,
    )


def dispatch_webhook_failed_notification_batch(
    db: Session,
    delivery_id: str,
    *,
    reserve_deliveries: Callable[..., NotificationDeliveryReservationBatch],
    process_reserved_deliveries: Callable[[Session, list[uuid.UUID]], tuple[int, int]],
) -> dict[str, Any]:
    try:
        parsed_delivery_id = uuid.UUID(delivery_id)
    except ValueError:
        return {"status": "skipped", "reason": "invalid_delivery_id", "delivery_id": delivery_id}

    failed_delivery = db.scalar(
        select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.id == parsed_delivery_id)
    )
    if failed_delivery is None:
        return {"status": "skipped", "reason": "delivery_not_found", "delivery_id": delivery_id}
    if failed_delivery.success or failed_delivery.event_type_snapshot == "webhook_failed":
        return {"status": "skipped", "reason": "not_eligible", "delivery_id": delivery_id}

    reservation = reserve_deliveries(db, failed_delivery=failed_delivery)
    return _complete_reserved_notification_dispatch(
        db,
        reservation=reservation,
        process_reserved_deliveries=process_reserved_deliveries,
        identifier_key="delivery_id",
        identifier_value=delivery_id,
    )


def _complete_reserved_notification_dispatch(
    db: Session,
    *,
    reservation: NotificationDeliveryReservationBatch,
    process_reserved_deliveries: Callable[[Session, list[uuid.UUID]], tuple[int, int]],
    identifier_key: str,
    identifier_value: str,
) -> dict[str, Any]:
    db.commit()
    delivered, failed = process_reserved_deliveries(db, reservation.delivery_ids)
    return {
        "status": "ok",
        identifier_key: identifier_value,
        "matched_webhooks": reservation.matched_webhooks,
        "delivered": delivered,
        "failed": failed,
        "skipped": reservation.skipped,
    }


def _chunk_uuid_list(values: list[uuid.UUID], chunk_size: int) -> list[list[uuid.UUID]]:
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]
