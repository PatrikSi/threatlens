from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.notification_webhooks import NotificationDeliveryReservationBatch


@dataclass(frozen=True)
class ScheduledNotificationDelivery:
    delivery_id: uuid.UUID
    countdown_seconds: int | None


@dataclass(frozen=True)
class NotificationDeliveryProcessingResult:
    delivered: int
    failed: int
    followup_deliveries: tuple[ScheduledNotificationDelivery, ...]
    followup_event_ids: tuple[uuid.UUID, ...]
    terminal_failed_delivery_ids: tuple[uuid.UUID, ...]


def process_reserved_notification_deliveries(
    db: Session,
    delivery_ids: list[uuid.UUID],
    *,
    process_delivery: Callable[..., Any],
    reserve_retryable_delivery: Callable[..., Any],
    reserve_failed_delivery_notifications: Callable[..., NotificationDeliveryReservationBatch] | None,
    logger: logging.Logger,
    emit_failed_delivery_event: Callable[[Session, NotificationWebhookDelivery], uuid.UUID] | None = None,
    mark_dead_letter: Callable[[Session, NotificationWebhookDelivery], None] | None = None,
) -> NotificationDeliveryProcessingResult:
    delivered = 0
    failed = 0
    followup_deliveries: list[ScheduledNotificationDelivery] = []
    followup_event_ids: list[uuid.UUID] = []
    terminal_failed_delivery_ids: list[uuid.UUID] = []

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
            db.commit()
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
            db.commit()
            if retry_reservation.created:
                followup_deliveries.append(
                    ScheduledNotificationDelivery(
                        delivery_id=retry_reservation.delivery.id,
                        countdown_seconds=retry_reservation.countdown_seconds,
                    )
                )
                logger.warning(
                    "notification_webhook_delivery_retry_scheduled webhook_id=%s delivery_id=%s retry_delivery_id=%s countdown_seconds=%s",
                    attempt.delivery.webhook_id,
                    attempt.delivery.id,
                    retry_reservation.delivery.id,
                    retry_reservation.countdown_seconds,
                )
            continue

        if mark_dead_letter is not None:
            mark_dead_letter(db, attempt.delivery)

        if attempt.delivery.event_type_snapshot != "webhook_failed":
            terminal_failed_delivery_ids.append(attempt.delivery.id)
            if emit_failed_delivery_event is not None:
                followup_event_ids.append(emit_failed_delivery_event(db, attempt.delivery))
            elif reserve_failed_delivery_notifications is not None:
                failed_reservations = reserve_failed_delivery_notifications(
                    db,
                    failed_delivery=attempt.delivery,
                )
                followup_deliveries.extend(
                    ScheduledNotificationDelivery(delivery_id=followup_id, countdown_seconds=None)
                    for followup_id in failed_reservations.delivery_ids
                )

        db.commit()
        logger.warning(
            "notification_webhook_delivery_failed webhook_id=%s delivery_id=%s event_type=%s status_code=%s error=%s",
            attempt.delivery.webhook_id,
            attempt.delivery.id,
            attempt.delivery.event_type_snapshot,
            attempt.result.status_code,
            attempt.result.error,
        )

    return NotificationDeliveryProcessingResult(
        delivered=delivered,
        failed=failed,
        followup_deliveries=tuple(followup_deliveries),
        followup_event_ids=tuple(followup_event_ids),
        terminal_failed_delivery_ids=tuple(terminal_failed_delivery_ids),
    )
