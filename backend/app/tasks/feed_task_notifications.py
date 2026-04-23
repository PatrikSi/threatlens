from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.notification_webhooks import NotificationDeliveryReservationBatch


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
    reserve_failed_delivery_notifications: Callable[..., NotificationDeliveryReservationBatch],
    enqueue_delivery_processing: Callable[..., bool],
    logger: logging.Logger,
) -> tuple[int, int]:
    delivered = 0
    failed = 0

    for delivery_id in delivery_ids:
        try:
            attempt = process_delivery(db, delivery_id=delivery_id)
        except ValueError as exc:
            if str(exc) != "Webhook delivery not found":
                raise
            logger.info("notification_webhook_delivery_missing delivery_id=%s", delivery_id)
            continue

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
            reserve_retryable_delivery(
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
                enqueue_delivery_processing(
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
            failed_delivery_reservations = reserve_failed_delivery_notifications(
                db,
                failed_delivery=attempt.delivery,
            )
            db.commit()
            enqueue_delivery_processing(failed_delivery_reservations.delivery_ids)

        logger.warning(
            "notification_webhook_delivery_failed webhook_id=%s delivery_id=%s event_type=%s status_code=%s error=%s",
            attempt.delivery.webhook_id,
            attempt.delivery.id,
            attempt.delivery.event_type_snapshot,
            attempt.result.status_code,
            attempt.result.error,
        )

    return delivered, failed


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
