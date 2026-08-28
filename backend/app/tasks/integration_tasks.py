from __future__ import annotations

import logging
import uuid

from app.core.config import get_settings
from app.models.integration import IntegrationDelivery
from app.services.integration_delivery import (
    IntegrationDeliveryClaimTracker,
    defer_unclaimed_integration_delivery,
    integration_delivery_claim_observer,
    record_integration_delivery_unknown_outcome,
    release_integration_delivery_publications,
    reserve_recoverable_integration_deliveries,
)
from app.services.integration_events import (
    IntegrationEventContextError,
    record_integration_event_failure,
    release_integration_event_publications,
    reserve_recoverable_integration_events,
    route_integration_event as route_pending_integration_event,
)
from app.services.integration_maintenance import run_integration_delivery_maintenance
from app.services.integration_registry import get_integration_connector
from app.tasks.celery_app import celery_app
from app.tasks.feed_task_notifications import enqueue_notification_delivery_batches
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)
settings = get_settings()


def enqueue_integration_event_routing(event_ids: list[uuid.UUID]) -> bool:
    all_enqueued = True
    for event_id in event_ids:
        try:
            route_integration_event.delay(str(event_id))
        except Exception as exc:
            all_enqueued = False
            logger.exception(
                "integration_event_enqueue_failed event_id=%s error=%s", event_id, exc
            )
    return all_enqueued


def enqueue_integration_delivery_processing(
    delivery_ids: list[uuid.UUID],
    countdown: int | None = None,
) -> bool:
    return enqueue_notification_delivery_batches(
        delivery_ids,
        batch_size=settings.notification_delivery_enqueue_batch_size,
        delivery_task=process_integration_deliveries,
        logger=logger,
        countdown=countdown,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.process_integration_deliveries",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_integration_deliveries(delivery_ids: list[str]):
    delivered = 0
    failed = 0
    deferred = 0
    skipped = 0
    with db_session() as db:
        for raw_delivery_id in delivery_ids:
            try:
                delivery_id = uuid.UUID(raw_delivery_id)
            except (AttributeError, TypeError, ValueError):
                skipped += 1
                continue
            claim_tracker = IntegrationDeliveryClaimTracker(delivery_id)

            try:
                delivery = db.get(IntegrationDelivery, delivery_id)
                if delivery is None:
                    skipped += 1
                    continue
                connector = get_integration_connector(delivery.connector_type)
                if connector is None:
                    defer_unclaimed_integration_delivery(
                        db,
                        delivery_id=delivery.id,
                        error_code="unsupported_connector",
                        error_message=(
                            f"Connector {delivery.connector_type!r} is not available on this worker; "
                            "delivery will be retried after the worker is upgraded."
                        ),
                    )
                    db.commit()
                    deferred += 1
                    continue
                with integration_delivery_claim_observer(claim_tracker.observe):
                    result = connector.process_delivery(db, delivery=delivery)
                for followup in result.followup_deliveries:
                    enqueue_integration_delivery_processing(
                        [followup.delivery_id],
                        countdown=followup.countdown_seconds,
                    )
                if result.followup_event_ids:
                    enqueue_integration_event_routing(list(result.followup_event_ids))
                if result.status == "succeeded":
                    delivered += 1
                elif result.status in {"pending", "sending", "retry_wait", "deferred"}:
                    deferred += 1
                elif result.status in {"terminal", "missing"}:
                    skipped += 1
                else:
                    failed += 1
            except Exception as exc:
                db.rollback()
                error_message = f"{type(exc).__name__}: {exc}"[:4000]
                if claim_tracker.attempt_number is not None:
                    record_integration_delivery_unknown_outcome(
                        db,
                        delivery_id=delivery_id,
                        expected_attempt_number=claim_tracker.attempt_number,
                        error_code="worker_error",
                        error_message=error_message,
                    )
                else:
                    defer_unclaimed_integration_delivery(
                        db,
                        delivery_id=delivery_id,
                        error_code="worker_error",
                        error_message=error_message,
                    )
                db.commit()
                failed += 1
                logger.exception(
                    "integration_delivery_processing_failed delivery_id=%s error_type=%s",
                    delivery_id,
                    type(exc).__name__,
                )
    return {
        "status": "ok",
        "scanned": len(delivery_ids),
        "delivered": delivered,
        "failed": failed,
        "deferred": deferred,
        "skipped": skipped,
    }


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_pending_integration_deliveries",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_pending_integration_deliveries():
    with db_session() as db:
        reservation = reserve_recoverable_integration_deliveries(db)
        db.commit()
    queued_ids, failed_ids = _enqueue_recovery_delivery_processing(
        list(reservation.delivery_ids)
    )
    if failed_ids:
        with db_session() as db:
            release_integration_delivery_publications(
                db,
                delivery_ids=failed_ids,
                reserved_at=reservation.reserved_at,
            )
            db.commit()
    return {
        "status": "ok",
        "scanned": len(reservation.delivery_ids),
        "queued": len(queued_ids),
        "enqueue_failed": bool(failed_ids),
    }


def _enqueue_recovery_delivery_processing(
    delivery_ids: list[uuid.UUID],
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    batch_size = max(1, int(settings.notification_delivery_enqueue_batch_size))
    queued: list[uuid.UUID] = []
    failed: list[uuid.UUID] = []
    for offset in range(0, len(delivery_ids), batch_size):
        chunk = delivery_ids[offset : offset + batch_size]
        try:
            process_integration_deliveries.delay(
                [str(delivery_id) for delivery_id in chunk]
            )
        except Exception as exc:
            failed.extend(chunk)
            logger.exception(
                "integration_delivery_recovery_enqueue_failed delivery_count=%s error=%s",
                len(chunk),
                exc,
            )
        else:
            queued.extend(chunk)
    return queued, failed


@celery_app.task(
    name="app.tasks.feed_tasks.maintain_integration_delivery_history",
    acks_late=True,
    reject_on_worker_lost=True,
)
def maintain_integration_delivery_history():
    with db_session() as db:
        result = run_integration_delivery_maintenance(db)
    return {
        "status": "ok",
        "rolled_up": result.rolled_up,
        "webhook_deliveries_deleted": result.webhook_deliveries_deleted,
        "deliveries_deleted": result.deliveries_deleted,
        "events_deleted": result.events_deleted,
        "metrics_deleted": result.metrics_deleted,
    }


@celery_app.task(
    name="app.tasks.feed_tasks.route_integration_event",
    acks_late=True,
    reject_on_worker_lost=True,
)
def route_integration_event(event_id: str):
    try:
        parsed_event_id = uuid.UUID(event_id)
    except (AttributeError, TypeError, ValueError):
        return {"status": "skipped", "reason": "invalid_event_id", "event_id": event_id}

    with db_session() as db:
        try:
            result = route_pending_integration_event(db, event_id=parsed_event_id)
            db.commit()
        except IntegrationEventContextError as exc:
            db.rollback()
            record_integration_event_failure(
                db,
                event_id=parsed_event_id,
                error=str(exc),
                terminal=True,
            )
            db.commit()
            logger.warning(
                "integration_event_dead_lettered event_id=%s error=%s",
                parsed_event_id,
                exc,
            )
            return {"status": "dead_letter", "event_id": event_id, "error": str(exc)}
        except Exception as exc:
            db.rollback()
            failed_event = record_integration_event_failure(
                db,
                event_id=parsed_event_id,
                error=f"{type(exc).__name__}: {exc}",
                terminal=False,
            )
            db.commit()
            logger.exception(
                "integration_event_routing_failed event_id=%s error=%s",
                parsed_event_id,
                exc,
            )
            return {
                "status": failed_event.routing_state
                if failed_event is not None
                else "missing",
                "event_id": event_id,
            }

    enqueue_ok = enqueue_integration_delivery_processing(
        result.integration_delivery_ids
    )
    return {
        "status": result.status,
        "event_id": event_id,
        "integration_deliveries": len(result.integration_delivery_ids),
        "webhook_deliveries": len(result.webhook_delivery_ids),
        "enqueue_failed": bool(result.integration_delivery_ids) and not enqueue_ok,
    }


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_pending_integration_events",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_pending_integration_events():
    with db_session() as db:
        reservation = reserve_recoverable_integration_events(db)
        db.commit()

    queued = 0
    failed_ids: list[uuid.UUID] = []
    for event_id in reservation.event_ids:
        try:
            route_integration_event.delay(str(event_id))
        except Exception as exc:
            failed_ids.append(event_id)
            logger.exception(
                "integration_event_enqueue_failed event_id=%s error=%s", event_id, exc
            )
            continue
        queued += 1
    if failed_ids:
        with db_session() as db:
            release_integration_event_publications(
                db,
                event_ids=failed_ids,
                reserved_at=reservation.reserved_at,
            )
            db.commit()
    return {
        "status": "ok",
        "scanned": len(reservation.event_ids),
        "queued": queued,
        "enqueue_failed": len(failed_ids),
    }
