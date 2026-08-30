from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.ai_daily_brief import AIDailyBrief
from app.models.feed import Feed
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_delivery import mark_integration_delivery_dead_letter
from app.services.integration_events import (
    EVENT_DEAD_LETTER,
    EVENT_FAILED,
    EVENT_PENDING,
    EVENT_ROUTED,
    EVENT_ROUTING,
    emit_integration_event,
)
from app.services.ai_config import load_active_ai_settings
from app.services.alert_evaluation import persist_alert_evaluation_intent
from app.services.daily_brief_notifications import emit_daily_brief_ready_event
from app.services.feed_pipeline import mark_feed_failure
from app.services.notification_webhooks import (
    FEED_FAILING_NOTIFICATION_THRESHOLD,
    build_alert_match_context_for_item,
    list_recoverable_notification_delivery_ids,
    process_notification_webhook_delivery,
    reserve_notification_webhook_delivery,
    reserve_retryable_notification_webhook_delivery,
)
from app.tasks.alert_tasks import enqueue_alert_evaluation_requests
from app.tasks.celery_app import celery_app
from app.tasks.feed_task_notifications import (
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
        error_message=failed_delivery.error
        or "Webhook delivery attempts were exhausted.",
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
            "feed_id": str(failed_delivery.feed_id)
            if failed_delivery.feed_id
            else None,
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
        process_delivery=lambda session, *, delivery_id: (
            process_notification_webhook_delivery(
                session,
                delivery_id=delivery_id,
                commit_outcome=False,
            )
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
    return _enqueue_smtp_notification_items(
        item_ids, task=dispatch_smtp_new_item_notification
    )


def _enqueue_smtp_alert_match_notification(item_id: uuid.UUID) -> bool:
    return _enqueue_smtp_notification_items(
        [item_id], task=dispatch_smtp_alert_match_notification
    )


def _enqueue_smtp_feed_failing_notification(feed_id: uuid.UUID) -> bool:
    try:
        dispatch_smtp_feed_failing_notification.delay(str(feed_id))
    except Exception as exc:
        logger.exception(
            "smtp_feed_failing_notification_enqueue_failed feed_id=%s error=%s",
            feed_id,
            exc,
        )
        return False
    return True


def _smtp_skipped_task_response(
    reason: str | None, **identifiers: str
) -> dict[str, Any]:
    return {
        "status": "skipped",
        **identifiers,
        "reason": reason,
        "sent": 0,
        "failed": 0,
        "skipped": 1,
    }


def _legacy_webhook_task_response(
    staging: dict[str, Any],
    *,
    include_smtp_enqueue_status: bool,
) -> dict[str, Any]:
    """Preserve historical Celery result fields while exposing durable staging."""

    staging_status = str(staging.get("status") or "pending")
    response = {
        **staging,
        "status": "skipped" if staging_status == "skipped" else "ok",
        "delivery_status": staging_status,
        "delivery_failed": int(staging.get("failed") or 0),
        "matched_webhooks": 0,
        "delivered": 0,
        "failed": 0,
        "skipped": int(staging.get("skipped") or 0),
    }
    if include_smtp_enqueue_status:
        response["smtp_enqueue_failed"] = bool(staging.get("enqueue_failed", False))
    return response


def _enqueue_legacy_integration_event(
    event_id: uuid.UUID,
    *,
    routing_state: str,
    available_at: datetime | None = None,
    **identifiers: str,
) -> dict[str, Any]:
    if routing_state == EVENT_DEAD_LETTER:
        status, reason, enqueue_failed, publication_state = (
            "dead_letter",
            "event_dead_letter",
            False,
            "not_required",
        )
    elif routing_state == EVENT_ROUTED:
        status, reason, enqueue_failed, publication_state = (
            "already_routed",
            None,
            False,
            "complete",
        )
    elif routing_state == EVENT_ROUTING:
        status, reason, enqueue_failed, publication_state = (
            "in_progress",
            None,
            False,
            "in_progress",
        )
    elif routing_state == EVENT_PENDING:
        enqueue_ok = enqueue_integration_event_routing([event_id])
        status, reason, enqueue_failed, publication_state = (
            ("queued", None, False, "published")
            if enqueue_ok
            else ("pending", "event_enqueue_failed", True, "failed")
        )
    elif routing_state == EVENT_FAILED:
        status, reason, enqueue_failed, publication_state = (
            "retry_scheduled",
            "event_backoff",
            False,
            "deferred",
        )
    else:
        logger.error(
            "legacy_smtp_event_has_unknown_state event_id=%s routing_state=%s",
            event_id,
            routing_state,
        )
        status, reason, enqueue_failed, publication_state = (
            "pending",
            "unknown_event_state",
            True,
            "unknown",
        )

    return {
        "status": status,
        "event_status": status,
        "routing_state": routing_state,
        "durable_state": routing_state,
        "retry_at": (
            _coerce_utc(available_at).isoformat()
            if routing_state == EVENT_FAILED and available_at is not None
            else None
        ),
        "publication_state": publication_state,
        **identifiers,
        "reason": reason,
        "sent": 0,
        "failed": 1 if status == "dead_letter" else 0,
        "skipped": 1 if status in {"already_routed", "in_progress"} else 0,
        "integration_event_id": str(event_id),
        "enqueue_failed": enqueue_failed,
    }


def _load_item_and_feed_for_notification(
    db: Session, item_id: str
) -> tuple[Item | None, Feed | None, str | None]:
    try:
        parsed_item_id = uuid.UUID(item_id)
    except (AttributeError, TypeError, ValueError):
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


def stage_feed_failure_notifications(
    db: Session,
    feed: Feed,
    error: str,
) -> list[uuid.UUID]:
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
    return integration_event_ids


def enqueue_feed_failure_notifications(
    integration_event_ids: list[uuid.UUID],
) -> bool:
    return enqueue_integration_event_routing(integration_event_ids)


def mark_feed_failure_and_enqueue_notifications(
    db: Session,
    feed: Feed,
    error: str,
) -> bool:
    integration_event_ids = stage_feed_failure_notifications(db, feed, error)
    db.commit()
    return enqueue_feed_failure_notifications(integration_event_ids)


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
        return {
            "status": "skipped",
            "reason": "no_valid_delivery_ids",
            "skipped": skipped,
        }

    with db_session() as db:
        delivered, failed = _process_reserved_notification_deliveries(
            db, parsed_delivery_ids
        )
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
        event = emit_integration_event(
            db,
            event_type="rss_item_new",
            source_type="item",
            source_id=item.id,
            idempotency_key=f"item:{item.id}:rss_item_new:v1",
            payload={"item_id": str(item.id), "feed_id": str(feed.id)},
        )
        event_id = event.id
        routing_state = event.routing_state
        event_available_at = event.available_at
        db.commit()
    return _enqueue_legacy_integration_event(
        event_id,
        routing_state=routing_state,
        available_at=event_available_at,
        item_id=item_id,
    )


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

        classification = db.scalar(
            select(ItemClassification).where(ItemClassification.item_id == item.id)
        )
        intent = persist_alert_evaluation_intent(
            db,
            item=item,
            classification=classification,
            source="live",
            notify=True,
        )
        evaluation = db.get(AlertEvaluationRequest, intent.request_id)
        if evaluation is None:
            raise RuntimeError("Alert evaluation intent could not be reloaded")
        request_id = evaluation.id
        request_state = evaluation.state
        request_available_at = evaluation.available_at
        dispatch_published_at = evaluation.dispatch_published_at
        last_dispatch_failed_at = evaluation.last_dispatch_failed_at
        notifications_enabled = evaluation.notify
        should_enqueue = intent.created
        db.commit()
    if should_enqueue:
        enqueue_ok = enqueue_alert_evaluation_requests([request_id])
        return {
            "status": "queued" if enqueue_ok else "pending",
            "item_id": item_id,
            "reason": None if enqueue_ok else "evaluation_enqueue_failed",
            "evaluation_request_id": str(request_id),
            "evaluation_state": request_state,
            "retry_at": _coerce_utc(request_available_at).isoformat(),
            "publication_state": "published" if enqueue_ok else "failed",
            "enqueue_failed": not enqueue_ok,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
        }
    if not notifications_enabled:
        return {
            **_smtp_skipped_task_response(
                "alert_evaluation_notifications_disabled",
                item_id=item_id,
            ),
            "evaluation_request_id": str(request_id),
            "evaluation_state": request_state,
            "retry_at": (
                _coerce_utc(request_available_at).isoformat()
                if request_state in {"pending", "retry_wait"}
                else None
            ),
            "publication_state": "not_required",
            "enqueue_failed": False,
        }
    status, reason = {
        "succeeded": ("already_evaluated", None),
        "dead_letter": ("dead_letter", "alert_evaluation_dead_letter"),
    }.get(request_state, ("in_progress", None))
    publication_state = (
        "failed"
        if last_dispatch_failed_at is not None
        and (
            dispatch_published_at is None
            or last_dispatch_failed_at >= dispatch_published_at
        )
        else "published"
        if dispatch_published_at is not None
        else "pending"
    )
    return {
        "status": status,
        "item_id": item_id,
        "reason": reason,
        "evaluation_request_id": str(request_id),
        "evaluation_state": request_state,
        "retry_at": (
            _coerce_utc(request_available_at).isoformat()
            if request_state in {"pending", "retry_wait"}
            else None
        ),
        "publication_state": publication_state,
        "enqueue_failed": False,
        "sent": 0,
        "failed": 1 if status == "dead_letter" else 0,
        "skipped": 1 if status == "already_evaluated" else 0,
    }


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
            return _smtp_skipped_task_response(
                "below_failure_threshold", feed_id=feed_id
            )

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
        event_id = event.id
        routing_state = event.routing_state
        event_available_at = event.available_at
        db.commit()
    return _enqueue_legacy_integration_event(
        event_id,
        routing_state=routing_state,
        available_at=event_available_at,
        feed_id=feed_id,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_smtp_webhook_failed_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_smtp_webhook_failed_notification(delivery_id: str):
    try:
        parsed_delivery_id = uuid.UUID(delivery_id)
    except (AttributeError, TypeError, ValueError):
        return _smtp_skipped_task_response(
            "invalid_delivery_id", delivery_id=delivery_id
        )

    with db_session() as db:
        failed_delivery = db.scalar(
            select(NotificationWebhookDelivery).where(
                NotificationWebhookDelivery.id == parsed_delivery_id
            )
        )
        if failed_delivery is None:
            return _smtp_skipped_task_response(
                "delivery_not_found", delivery_id=delivery_id
            )
        if (
            failed_delivery.success
            or failed_delivery.event_type_snapshot == "webhook_failed"
        ):
            return _smtp_skipped_task_response("not_eligible", delivery_id=delivery_id)

        source_webhook = db.scalar(
            select(NotificationWebhook).where(
                NotificationWebhook.id == failed_delivery.webhook_id
            )
        )
        if source_webhook is None:
            return _smtp_skipped_task_response(
                "source_webhook_not_found", delivery_id=delivery_id
            )

        event_id = _emit_failed_webhook_integration_event(db, failed_delivery)
        event = db.get(IntegrationEvent, event_id)
        if event is None:
            raise RuntimeError("Emitted integration event could not be reloaded")
        routing_state = event.routing_state
        event_available_at = event.available_at
        db.commit()
    return _enqueue_legacy_integration_event(
        event_id,
        routing_state=routing_state,
        available_at=event_available_at,
        delivery_id=delivery_id,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_new_item_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_new_item_notification_webhooks(item_id: str):
    return _legacy_webhook_task_response(
        dispatch_smtp_new_item_notification(item_id),
        include_smtp_enqueue_status=True,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_alert_match_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_alert_match_notification_webhooks(item_id: str):
    return _legacy_webhook_task_response(
        dispatch_smtp_alert_match_notification(item_id),
        include_smtp_enqueue_status=True,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_feed_failing_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_feed_failing_notification_webhooks(feed_id: str):
    return _legacy_webhook_task_response(
        dispatch_smtp_feed_failing_notification(feed_id),
        include_smtp_enqueue_status=True,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_webhook_failed_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_webhook_failed_notification_webhooks(delivery_id: str):
    return _legacy_webhook_task_response(
        dispatch_smtp_webhook_failed_notification(delivery_id),
        include_smtp_enqueue_status=False,
    )


@celery_app.task(
    name="app.tasks.feed_tasks.dispatch_daily_digest_notification_webhooks",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_daily_digest_notification_webhooks():
    with db_session() as db:
        now = datetime.now(timezone.utc)
        active = load_active_ai_settings(db)
        if not active.ai_enabled:
            return {"status": "skipped", "reason": "ai_disabled"}
        if not active.ai_configured:
            return {"status": "skipped", "reason": "ai_not_configured"}
        if not active.daily_brief_enabled:
            return {"status": "skipped", "reason": "daily_brief_disabled"}
        brief = db.scalar(
            select(AIDailyBrief).where(
                AIDailyBrief.brief_date == now.date(),
                AIDailyBrief.status == "ready",
            )
        )
        if brief is None:
            return {"status": "skipped", "reason": "daily_brief_not_ready"}
        event = emit_daily_brief_ready_event(db, brief=brief)
        event_id = event.id
        routing_state = event.routing_state
        event_available_at = event.available_at
        db.commit()
    routing = _enqueue_legacy_integration_event(
        event_id,
        routing_state=routing_state,
        available_at=event_available_at,
    )
    return {
        "status": "ok",
        "matched_webhooks": 0,
        "delivered": 0,
        "failed": 0,
        "skipped": 0,
        "smtp_status": routing["status"],
        "smtp_reason": routing["reason"],
        "smtp_sent": 0,
        "smtp_failed": 0,
        "smtp_skipped": 0,
        "integration_event_id": str(event_id),
        "enqueue_failed": routing["enqueue_failed"],
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
