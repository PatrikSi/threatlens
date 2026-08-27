from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import redact_log_text
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_delivery import (
    DELIVERY_DEAD_LETTER,
    DELIVERY_FAILED,
    IntegrationDeliveryClaimTracker,
    defer_unclaimed_integration_delivery,
    integration_delivery_claim_observer,
    lock_webhook_delivery_external_io_eligibility,
    record_integration_delivery_unknown_outcome,
    renew_integration_delivery_lease,
)
from app.services.notification_webhook_http import notification_delivery_lease_heartbeat
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
    reserve_failed_delivery_notifications: Callable[
        ..., NotificationDeliveryReservationBatch
    ]
    | None,
    logger: logging.Logger,
    emit_failed_delivery_event: Callable[
        [Session, NotificationWebhookDelivery], uuid.UUID
    ]
    | None = None,
    mark_dead_letter: Callable[[Session, NotificationWebhookDelivery], None]
    | None = None,
) -> NotificationDeliveryProcessingResult:
    delivered = 0
    failed = 0
    followup_deliveries: list[ScheduledNotificationDelivery] = []
    followup_event_ids: list[uuid.UUID] = []
    terminal_failed_delivery_ids: list[uuid.UUID] = []

    for delivery_id in delivery_ids:
        claim_tracker = IntegrationDeliveryClaimTracker(delivery_id)

        def _renew_lease(
            lease_seconds: int,
            *,
            tracked_delivery_id: uuid.UUID = delivery_id,
            tracker: IntegrationDeliveryClaimTracker = claim_tracker,
        ) -> None:
            _renew_webhook_delivery_lease(
                db,
                delivery_id=tracked_delivery_id,
                expected_attempt_number=tracker.attempt_number,
                lease_seconds=lease_seconds,
            )

        try:
            with integration_delivery_claim_observer(claim_tracker.observe):
                with notification_delivery_lease_heartbeat(_renew_lease):
                    attempt = process_delivery(db, delivery_id=delivery_id)
            result = _complete_webhook_delivery_attempt(
                db,
                attempt=attempt,
                reserve_retryable_delivery=reserve_retryable_delivery,
                reserve_failed_delivery_notifications=reserve_failed_delivery_notifications,
                logger=logger,
                emit_failed_delivery_event=emit_failed_delivery_event,
                mark_dead_letter=mark_dead_letter,
                followup_deliveries=followup_deliveries,
                followup_event_ids=followup_event_ids,
                terminal_failed_delivery_ids=terminal_failed_delivery_ids,
            )
        except Exception as exc:
            db.rollback()
            current = db.scalar(
                select(NotificationWebhookDelivery)
                .where(NotificationWebhookDelivery.id == delivery_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None:
                logger.info(
                    "notification_webhook_delivery_missing delivery_id=%s", delivery_id
                )
                continue
            recorded = _record_webhook_processing_failure(
                db,
                delivery=current,
                expected_attempt_number=claim_tracker.attempt_number,
                exc=exc,
            )
            if recorded:
                db.commit()
            else:
                db.rollback()
            failed += 1
            logger.exception(
                "notification_webhook_delivery_worker_failed delivery_id=%s error_type=%s",
                delivery_id,
                type(exc).__name__,
            )
            continue
        if result == "delivered":
            delivered += 1
        elif result == "failed":
            failed += 1

    return NotificationDeliveryProcessingResult(
        delivered=delivered,
        failed=failed,
        followup_deliveries=tuple(followup_deliveries),
        followup_event_ids=tuple(followup_event_ids),
        terminal_failed_delivery_ids=tuple(terminal_failed_delivery_ids),
    )


def _complete_webhook_delivery_attempt(
    db: Session,
    *,
    attempt: Any,
    reserve_retryable_delivery: Callable[..., Any],
    reserve_failed_delivery_notifications: Callable[
        ..., NotificationDeliveryReservationBatch
    ]
    | None,
    logger: logging.Logger,
    emit_failed_delivery_event: Callable[
        [Session, NotificationWebhookDelivery], uuid.UUID
    ]
    | None,
    mark_dead_letter: Callable[[Session, NotificationWebhookDelivery], None] | None,
    followup_deliveries: list[ScheduledNotificationDelivery],
    followup_event_ids: list[uuid.UUID],
    terminal_failed_delivery_ids: list[uuid.UUID],
) -> str:
    if not getattr(attempt, "claimed", True):
        logger.info(
            "notification_webhook_delivery_already_claimed delivery_id=%s state=%s",
            attempt.delivery.id,
            getattr(attempt.delivery, "delivery_state", "unknown"),
        )
        return "skipped"

    if attempt.result.success:
        db.commit()
        return "delivered"

    source_webhook = db.scalar(
        select(NotificationWebhook).where(
            NotificationWebhook.id == attempt.delivery.webhook_id
        )
    )
    retry_reservation = (
        reserve_retryable_delivery(
            db,
            webhook=source_webhook,
            delivery=attempt.delivery,
        )
        if source_webhook is not None
        and attempt.delivery.event_type_snapshot != "webhook_failed"
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
        return "failed"

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
                ScheduledNotificationDelivery(
                    delivery_id=followup_id, countdown_seconds=None
                )
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
    return "failed"


def _renew_webhook_delivery_lease(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_number: int | None,
    lease_seconds: int,
) -> None:
    if expected_attempt_number is None:
        raise RuntimeError("Webhook delivery claim identity is unavailable")
    current_time = datetime.now(timezone.utc)
    delivery = db.scalar(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        delivery is None
        or delivery.delivery_state != "sending"
        or int(delivery.attempt_count or 0) != int(expected_attempt_number)
    ):
        db.rollback()
        raise RuntimeError("Webhook delivery lease is no longer owned by this worker")
    if (
        delivery.integration_delivery_id is not None
        and not renew_integration_delivery_lease(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            lease_seconds=lease_seconds,
            now=current_time,
        )
    ):
        db.rollback()
        raise RuntimeError(
            "Webhook integration delivery lease is no longer owned by this worker"
        )
    delivery.claimed_at = current_time
    delivery.not_before = current_time + timedelta(seconds=max(1, int(lease_seconds)))
    db.add(delivery)
    webhook_id = delivery.webhook_id
    integration_delivery_id = delivery.integration_delivery_id
    db.commit()
    if integration_delivery_id is None:
        raise RuntimeError("Webhook integration delivery configuration is incomplete")
    lock_webhook_delivery_external_io_eligibility(
        db,
        webhook_id=webhook_id,
        legacy_delivery_id=delivery_id,
        integration_delivery_id=integration_delivery_id,
        expected_attempt_number=expected_attempt_number,
    )


def _record_webhook_processing_failure(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    expected_attempt_number: int | None,
    exc: Exception,
) -> bool:
    error_message = redact_log_text(f"{type(exc).__name__}: {exc}", max_chars=4000)
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    generic_state: str | None = None
    if expected_attempt_number is None:
        if delivery.delivery_state != "pending":
            return False
        if delivery.integration_delivery_id is not None:
            defer_unclaimed_integration_delivery(
                db,
                delivery_id=delivery.integration_delivery_id,
                error_code="worker_error",
                error_message=error_message,
                delay_seconds=60,
            )
    elif delivery.delivery_state != "sending" or int(
        delivery.attempt_count or 0
    ) != int(expected_attempt_number):
        return False
    elif delivery.integration_delivery_id is not None:
        outcome = record_integration_delivery_unknown_outcome(
            db,
            delivery_id=delivery.integration_delivery_id,
            expected_attempt_number=expected_attempt_number,
            error_code="worker_error",
            error_message=error_message,
        )
        generic_state = outcome.state
        if not outcome.recorded:
            return False
        if outcome.retry_at is not None:
            retry_at = outcome.retry_at

    delivery.delivery_state = (
        "failed"
        if generic_state in {DELIVERY_DEAD_LETTER, DELIVERY_FAILED}
        else "pending"
    )
    delivery.success = False
    delivery.claimed_at = None
    delivery.not_before = None if delivery.delivery_state == "failed" else retry_at
    delivery.error = error_message
    delivery.attempted_at = datetime.now(timezone.utc)
    db.add(delivery)
    return True
