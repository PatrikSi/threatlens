from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
    IntegrationSubscriptionFeed,
)
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_delivery import (
    CLAIMED,
    claim_integration_delivery,
    finalize_integration_delivery,
)
from app.services.notification_webhooks import (
    FailedWebhookContext,
    build_alert_match_context_for_item,
    build_daily_digest_context,
)
from app.services.smtp_integration import attempt_smtp_integration_delivery

RETRYABLE_SMTP_ERROR_CODES = frozenset(
    {
        "connect_error",
        "connection_closed",
        "connection_error",
        "smtp_error",
        "timeout",
        "tls_error",
        "transient_smtp_error",
    }
)


class IntegrationDeliveryContextError(ValueError):
    pass


@dataclass(frozen=True)
class IntegrationDeliveryProcessingResult:
    delivery_id: uuid.UUID
    status: str
    reason: str | None = None
    retry_at: str | None = None


def process_smtp_integration_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
) -> IntegrationDeliveryProcessingResult:
    claim = claim_integration_delivery(db, delivery_id=delivery_id)
    if claim.status != CLAIMED or claim.attempt_number is None:
        return IntegrationDeliveryProcessingResult(
            delivery_id=delivery_id,
            status=claim.status,
            reason=claim.reason,
            retry_at=claim.scheduled_for.isoformat() if claim.scheduled_for else None,
        )

    try:
        delivery = db.get(IntegrationDelivery, delivery_id)
        if delivery is None:
            raise IntegrationDeliveryContextError("Integration delivery no longer exists")
        if delivery.connector_type != "smtp":
            raise IntegrationDeliveryContextError(
                f"Expected smtp delivery, found {delivery.connector_type or 'unknown'}"
            )
        instance = db.get(IntegrationInstance, delivery.integration_id)
        if instance is None:
            raise IntegrationDeliveryContextError("SMTP integration no longer exists")
        context = _load_smtp_delivery_context(db, delivery=delivery)
        if context["skip_reason"] is not None:
            outcome = finalize_integration_delivery(
                db,
                delivery_id=delivery.id,
                expected_attempt_number=claim.attempt_number,
                success=True,
                duration_ms=0,
                error_code=None,
                error_message=None,
                retryable=False,
                response_json={"skipped": True, "reason": context["skip_reason"]},
            )
            db.commit()
            return IntegrationDeliveryProcessingResult(delivery.id, outcome.state or "succeeded", context["skip_reason"])

        dispatch = attempt_smtp_integration_delivery(
            db,
            instance=instance,
            delivery_id=delivery.id,
            dedupe_key=delivery.idempotency_key,
            event_type=delivery.event_type,
            feed=context["feed"],
            item=context["item"],
            alert_context=context["alert_context"],
            failed_webhook_context=context["failed_webhook_context"],
            digest_context=context["digest_context"],
            delivery_kind=delivery.delivery_kind,
            source_delivery_id=context["source_delivery_id"],
            scope_key=context["scope_key"],
        )
        result = dispatch.delivery
        if result is None:
            outcome = finalize_integration_delivery(
                db,
                delivery_id=delivery.id,
                expected_attempt_number=claim.attempt_number,
                success=True,
                duration_ms=0,
                error_code=None,
                error_message=None,
                retryable=False,
                response_json={"skipped": True, "reason": dispatch.reason},
            )
        else:
            retryable = not result.success and result.error_code in RETRYABLE_SMTP_ERROR_CODES
            outcome = finalize_integration_delivery(
                db,
                delivery_id=delivery.id,
                expected_attempt_number=claim.attempt_number,
                success=result.success,
                duration_ms=result.duration_ms,
                error_code=result.error_code,
                error_message=result.error,
                retryable=retryable,
                response_json={
                    "recipient_count": result.recipient_count,
                    "accepted_count": result.accepted_count,
                    "has_server_message": bool(result.server_message),
                },
            )
        db.commit()
        return IntegrationDeliveryProcessingResult(
            delivery_id=delivery.id,
            status=outcome.state or dispatch.status,
            reason=dispatch.reason,
            retry_at=outcome.retry_at.isoformat() if outcome.retry_at else None,
        )
    except IntegrationDeliveryContextError as exc:
        db.rollback()
        outcome = finalize_integration_delivery(
            db,
            delivery_id=delivery_id,
            expected_attempt_number=claim.attempt_number,
            success=False,
            duration_ms=0,
            error_code="context_error",
            error_message=str(exc),
            retryable=False,
        )
        db.commit()
        return IntegrationDeliveryProcessingResult(delivery_id, outcome.state or "dead_letter", str(exc))
    except Exception as exc:
        db.rollback()
        outcome = finalize_integration_delivery(
            db,
            delivery_id=delivery_id,
            expected_attempt_number=claim.attempt_number,
            success=False,
            duration_ms=None,
            error_code="worker_error",
            error_message=f"{type(exc).__name__}: {exc}"[:4000],
            retryable=True,
        )
        db.commit()
        return IntegrationDeliveryProcessingResult(
            delivery_id,
            outcome.state or "failed",
            "worker_error",
            outcome.retry_at.isoformat() if outcome.retry_at else None,
        )


def _load_smtp_delivery_context(db: Session, *, delivery: IntegrationDelivery) -> dict:
    payload = delivery.payload_json if isinstance(delivery.payload_json, dict) else {}
    item = _load_optional_model(db, Item, payload.get("item_id"), label="item")
    feed_id = payload.get("feed_id") or getattr(item, "feed_id", None)
    feed = _load_optional_model(db, Feed, feed_id, label="feed")
    alert_context = None
    failed_webhook_context = None
    digest_context = None
    source_delivery_id = _optional_uuid(payload.get("source_delivery_id"), label="source_delivery_id")
    scope_key = str(payload.get("scope_key")) if payload.get("scope_key") is not None else None
    skip_reason = None

    if delivery.event_type in {"rss_item_new", "alert_match"}:
        if item is None:
            raise IntegrationDeliveryContextError(f"{delivery.event_type} delivery is missing its item")
        if feed is None:
            raise IntegrationDeliveryContextError(f"{delivery.event_type} delivery is missing its feed")
        if delivery.event_type == "alert_match":
            alert_context = build_alert_match_context_for_item(db, item=item)
            if alert_context is None:
                skip_reason = "no_alert_match"
    elif delivery.event_type == "feed_failing":
        if feed is None:
            raise IntegrationDeliveryContextError("feed_failing delivery is missing its feed")
    elif delivery.event_type == "webhook_failed":
        if source_delivery_id is None:
            raise IntegrationDeliveryContextError("webhook_failed delivery is missing source_delivery_id")
        source_delivery = db.get(NotificationWebhookDelivery, source_delivery_id)
        if source_delivery is None:
            raise IntegrationDeliveryContextError("Source webhook delivery no longer exists")
        source_webhook = db.get(NotificationWebhook, source_delivery.webhook_id)
        if source_webhook is None:
            raise IntegrationDeliveryContextError("Source webhook no longer exists")
        failed_webhook_context = FailedWebhookContext(
            id=source_webhook.id,
            name=source_webhook.name,
            event_type=source_delivery.event_type_snapshot,
            status_code=source_delivery.status_code,
            error=source_delivery.error,
            attempted_at=source_delivery.attempted_at,
        )
    elif delivery.event_type == "daily_digest":
        feed_ids = _subscription_feed_ids(db, subscription_id=delivery.subscription_id)
        digest_context = build_daily_digest_context(db, user_id=None, feed_ids=feed_ids)
        if digest_context is None or digest_context.total_items <= 0:
            skip_reason = "no_digest_items"
    else:
        raise IntegrationDeliveryContextError(f"Unsupported SMTP event type: {delivery.event_type}")

    return {
        "item": item,
        "feed": feed,
        "alert_context": alert_context,
        "failed_webhook_context": failed_webhook_context,
        "digest_context": digest_context,
        "source_delivery_id": source_delivery_id,
        "scope_key": scope_key,
        "skip_reason": skip_reason,
    }


def _subscription_feed_ids(db: Session, *, subscription_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    if subscription_id is None:
        return None
    subscription = db.get(IntegrationSubscription, subscription_id)
    if subscription is None or subscription.feed_scope != "selected":
        return None
    return list(
        db.scalars(
            select(IntegrationSubscriptionFeed.feed_id).where(
                IntegrationSubscriptionFeed.subscription_id == subscription_id
            )
        ).all()
    )


def _load_optional_model(db: Session, model, value, *, label: str):
    parsed = _optional_uuid(value, label=f"{label}_id")
    if parsed is None:
        return None
    loaded = db.get(model, parsed)
    if loaded is None:
        raise IntegrationDeliveryContextError(f"Referenced {label} {parsed} no longer exists")
    return loaded


def _optional_uuid(value, *, label: str) -> uuid.UUID | None:
    if value in {None, ""}:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise IntegrationDeliveryContextError(f"Invalid {label}") from exc
