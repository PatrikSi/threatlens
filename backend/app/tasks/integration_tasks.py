from __future__ import annotations

import logging
import uuid

from app.core.config import get_settings
from app.models.integration import IntegrationDelivery
from app.services.integration_delivery import defer_integration_delivery, list_recoverable_integration_delivery_ids
from app.services.integration_events import (
    IntegrationEventContextError,
    list_recoverable_integration_event_ids,
    record_integration_event_failure,
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
            logger.exception("integration_event_enqueue_failed event_id=%s error=%s", event_id, exc)
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
            delivery = db.get(IntegrationDelivery, delivery_id)
            if delivery is None:
                skipped += 1
                continue
            connector = get_integration_connector(delivery.connector_type)
            if connector is None:
                defer_integration_delivery(
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
        delivery_ids = list_recoverable_integration_delivery_ids(db)
    enqueue_ok = enqueue_integration_delivery_processing(delivery_ids)
    return {
        "status": "ok",
        "scanned": len(delivery_ids),
        "queued": len(delivery_ids) if enqueue_ok else 0,
        "enqueue_failed": bool(delivery_ids) and not enqueue_ok,
    }


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
            logger.warning("integration_event_dead_lettered event_id=%s error=%s", parsed_event_id, exc)
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
            logger.exception("integration_event_routing_failed event_id=%s error=%s", parsed_event_id, exc)
            return {
                "status": failed_event.routing_state if failed_event is not None else "missing",
                "event_id": event_id,
            }

    enqueue_ok = enqueue_integration_delivery_processing(result.integration_delivery_ids)
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
        event_ids = list_recoverable_integration_event_ids(db)

    queued = 0
    for event_id in event_ids:
        try:
            route_integration_event.delay(str(event_id))
        except Exception as exc:
            logger.exception("integration_event_enqueue_failed event_id=%s error=%s", event_id, exc)
            continue
        queued += 1
    return {"status": "ok", "scanned": len(event_ids), "queued": queued}
