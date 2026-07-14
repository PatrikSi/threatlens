from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
    IntegrationSubscriptionFeed,
)
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import NotificationEventType
from app.services.integration_compat import repair_legacy_webhook_integrations
from app.services.integration_delivery import ensure_webhook_delivery
from app.services.integration_storage import sync_smtp_subscriptions
from app.services.notification_webhooks import (
    NotificationDeliveryReservationBatch,
    build_daily_digest_context,
    has_recent_notification_delivery,
    reserve_alert_match_notification_deliveries,
    reserve_feed_failing_notification_deliveries,
    reserve_new_item_notification_deliveries,
    reserve_notification_webhook_delivery,
    reserve_webhook_failed_notification_deliveries,
    try_acquire_notification_delivery_lock,
)

settings = get_settings()

EVENT_PENDING = "pending"
EVENT_ROUTING = "routing"
EVENT_ROUTED = "routed"
EVENT_FAILED = "failed"
EVENT_DEAD_LETTER = "dead_letter"


class IntegrationEventContextError(ValueError):
    pass


@dataclass(frozen=True)
class RoutedIntegrationEvent:
    event_id: uuid.UUID
    status: str
    webhook_delivery_ids: list[uuid.UUID]
    integration_delivery_ids: list[uuid.UUID]


def emit_integration_event(
    db: Session,
    *,
    event_type: NotificationEventType,
    source_type: str,
    source_id: str | uuid.UUID | None,
    idempotency_key: str,
    payload: dict,
    actor_user_id: uuid.UUID | None = None,
    available_at: datetime | None = None,
) -> IntegrationEvent:
    existing = db.scalar(select(IntegrationEvent).where(IntegrationEvent.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    event = IntegrationEvent(
        event_type=event_type,
        source_type=source_type,
        source_id=str(source_id) if source_id is not None else None,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        payload_json=dict(payload),
        routing_state=EVENT_PENDING,
        available_at=available_at or datetime.now(timezone.utc),
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(IntegrationEvent).where(IntegrationEvent.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    return event


def route_integration_event(db: Session, *, event_id: uuid.UUID) -> RoutedIntegrationEvent:
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise IntegrationEventContextError("Integration event not found")
    if event.routing_state == EVENT_ROUTED:
        return _routed_event_result(db, event)
    if event.routing_state == EVENT_DEAD_LETTER:
        return RoutedIntegrationEvent(event.id, EVENT_DEAD_LETTER, [], [])

    event.routing_state = EVENT_ROUTING
    event.claimed_at = datetime.now(timezone.utc)
    event.routing_attempt_count = max(0, int(event.routing_attempt_count or 0)) + 1
    event.last_error = None
    db.add(event)

    repair_legacy_webhook_integrations(db)
    reservation = _reserve_webhook_event_deliveries(db, event=event)
    integration_delivery_ids = _attach_event_to_webhook_deliveries(
        db,
        event=event,
        delivery_ids=reservation.delivery_ids,
    )
    integration_delivery_ids.extend(_reserve_smtp_event_deliveries(db, event=event))
    event.routing_state = EVENT_ROUTED
    event.routed_at = datetime.now(timezone.utc)
    event.claimed_at = None
    db.add(event)
    db.flush()
    return RoutedIntegrationEvent(
        event_id=event.id,
        status=EVENT_ROUTED,
        webhook_delivery_ids=reservation.delivery_ids,
        integration_delivery_ids=integration_delivery_ids,
    )


def record_integration_event_failure(
    db: Session,
    *,
    event_id: uuid.UUID,
    error: str,
    terminal: bool,
    now: datetime | None = None,
) -> IntegrationEvent | None:
    current_time = now or datetime.now(timezone.utc)
    event = db.scalar(select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update())
    if event is None or event.routing_state == EVENT_ROUTED:
        return event
    attempts = max(0, int(event.routing_attempt_count or 0)) + 1
    max_attempts = max(1, int(settings.integration_event_routing_max_attempts))
    dead_letter = terminal or attempts >= max_attempts
    event.routing_state = EVENT_DEAD_LETTER if dead_letter else EVENT_FAILED
    event.routing_attempt_count = attempts
    event.claimed_at = None
    event.last_error = error[:4000]
    event.available_at = current_time + timedelta(seconds=_routing_backoff_seconds(attempts))
    db.add(event)
    return event


def list_recoverable_integration_event_ids(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    stale_cutoff = current_time - timedelta(seconds=settings.integration_event_routing_stale_after_seconds)
    batch_size = max(1, int(limit or settings.integration_event_routing_batch_size))
    return list(
        db.scalars(
            select(IntegrationEvent.id)
            .where(
                or_(
                    and_(
                        IntegrationEvent.routing_state.in_([EVENT_PENDING, EVENT_FAILED]),
                        IntegrationEvent.available_at <= current_time,
                    ),
                    and_(
                        IntegrationEvent.routing_state == EVENT_ROUTING,
                        or_(IntegrationEvent.claimed_at.is_(None), IntegrationEvent.claimed_at < stale_cutoff),
                    ),
                )
            )
            .order_by(IntegrationEvent.available_at.asc(), IntegrationEvent.created_at.asc())
            .limit(batch_size)
        ).all()
    )


def _reserve_webhook_event_deliveries(
    db: Session,
    *,
    event: IntegrationEvent,
) -> NotificationDeliveryReservationBatch:
    feed_id = _payload_uuid(event, "feed_id", required=False)
    owner_user_id = _payload_uuid(event, "owner_user_id", required=False)
    webhooks = _matching_webhooks(
        db,
        event_type=event.event_type,
        feed_id=feed_id,
        owner_user_id=owner_user_id,
    )

    if event.event_type in {"rss_item_new", "alert_match"}:
        item_id = _payload_uuid(event, "item_id")
        item = db.get(Item, item_id)
        if item is None:
            raise IntegrationEventContextError(f"Item {item_id} no longer exists")
        feed = db.get(Feed, feed_id or item.feed_id)
        if feed is None:
            raise IntegrationEventContextError(f"Feed {feed_id or item.feed_id} no longer exists")
        if event.event_type == "rss_item_new":
            return reserve_new_item_notification_deliveries(db, item=item, feed=feed, webhooks=webhooks)
        return reserve_alert_match_notification_deliveries(db, item=item, feed=feed, webhooks=webhooks)

    if event.event_type == "feed_failing":
        if feed_id is None:
            raise IntegrationEventContextError("feed_failing event is missing feed_id")
        feed = db.get(Feed, feed_id)
        if feed is None:
            raise IntegrationEventContextError(f"Feed {feed_id} no longer exists")
        return reserve_feed_failing_notification_deliveries(db, feed=feed, webhooks=webhooks)

    if event.event_type == "webhook_failed":
        source_delivery_id = _payload_uuid(event, "source_delivery_id")
        source_delivery = db.get(NotificationWebhookDelivery, source_delivery_id)
        if source_delivery is None:
            raise IntegrationEventContextError(f"Webhook delivery {source_delivery_id} no longer exists")
        return reserve_webhook_failed_notification_deliveries(db, failed_delivery=source_delivery)

    if event.event_type == "daily_digest":
        return _reserve_daily_digest_webhooks(db, event=event, webhooks=webhooks)

    raise IntegrationEventContextError(f"Unsupported integration event type: {event.event_type}")


def _matching_webhooks(
    db: Session,
    *,
    event_type: str,
    feed_id: uuid.UUID | None,
    owner_user_id: uuid.UUID | None,
) -> list[NotificationWebhook]:
    query = (
        select(NotificationWebhook)
        .join(IntegrationSubscription, IntegrationSubscription.id == NotificationWebhook.subscription_id)
        .join(IntegrationInstance, IntegrationInstance.id == NotificationWebhook.integration_id)
        .where(
            IntegrationInstance.integration_type == "webhook",
            IntegrationInstance.enabled.is_(True),
            IntegrationSubscription.enabled.is_(True),
            IntegrationSubscription.event_type == event_type,
            NotificationWebhook.enabled.is_(True),
        )
    )
    if owner_user_id is not None:
        query = query.where(IntegrationInstance.owner_user_id == owner_user_id)
    if feed_id is None and event_type != "daily_digest":
        query = query.where(IntegrationSubscription.feed_scope == "all")
    elif feed_id is not None:
        query = query.outerjoin(
            IntegrationSubscriptionFeed,
            and_(
                IntegrationSubscriptionFeed.subscription_id == IntegrationSubscription.id,
                IntegrationSubscriptionFeed.feed_id == feed_id,
            ),
        ).where(
            or_(
                IntegrationSubscription.feed_scope == "all",
                IntegrationSubscriptionFeed.feed_id == feed_id,
            )
        )
    return list(db.scalars(query.order_by(NotificationWebhook.created_at.asc())).unique().all())


def _reserve_daily_digest_webhooks(
    db: Session,
    *,
    event: IntegrationEvent,
    webhooks: list[NotificationWebhook],
) -> NotificationDeliveryReservationBatch:
    scope_key = str(event.payload_json.get("scope_key") or event.created_at.date().isoformat())
    delivery_ids: list[uuid.UUID] = []
    skipped = 0
    for webhook in webhooks:
        user = db.get(User, webhook.user_id)
        if user is None or not user.is_active or not user.is_approved:
            skipped += 1
            continue
        if not try_acquire_notification_delivery_lock(
            db,
            webhook_id=webhook.id,
            event_type="daily_digest",
            scope_key=scope_key,
        ):
            skipped += 1
            continue
        if has_recent_notification_delivery(
            db,
            webhook_id=webhook.id,
            event_type="daily_digest",
            scope_key=scope_key,
        ):
            skipped += 1
            continue
        feed_ids = (
            list(
                db.scalars(
                    select(IntegrationSubscriptionFeed.feed_id).where(
                        IntegrationSubscriptionFeed.subscription_id == webhook.subscription_id
                    )
                ).all()
            )
            if webhook.feed_scope == "selected"
            else None
        )
        digest_context = build_daily_digest_context(db, user_id=user.id, feed_ids=feed_ids)
        if digest_context is None or digest_context.total_items <= 0:
            skipped += 1
            continue
        delivery = reserve_notification_webhook_delivery(
            db,
            webhook=webhook,
            user=user,
            event_type="daily_digest",
            digest_context=digest_context,
            item_title=f"{digest_context.total_items} items in last 24h",
            feed_name=", ".join(digest_context.feed_names[:3]) or None,
            scope_key=scope_key,
        )
        delivery_ids.append(delivery.id)
    return NotificationDeliveryReservationBatch(
        delivery_ids=delivery_ids,
        matched_webhooks=len(webhooks),
        skipped=skipped,
    )


def _attach_event_to_webhook_deliveries(
    db: Session,
    *,
    event: IntegrationEvent,
    delivery_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    generic_ids: list[uuid.UUID] = []
    for legacy_delivery in db.scalars(
        select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.id.in_(delivery_ids))
    ).all():
        webhook = db.get(NotificationWebhook, legacy_delivery.webhook_id)
        if webhook is None:
            continue
        generic = ensure_webhook_delivery(
            db,
            webhook=webhook,
            legacy_delivery=legacy_delivery,
            event_id=event.id,
        )
        generic.idempotency_key = f"event:{event.id}:subscription:{generic.subscription_id}:live"
        db.add(generic)
        generic_ids.append(generic.id)
    db.flush()
    return generic_ids


def _reserve_smtp_event_deliveries(db: Session, *, event: IntegrationEvent) -> list[uuid.UUID]:
    instances = db.scalars(
        select(IntegrationInstance).where(
            IntegrationInstance.integration_type == "smtp",
            IntegrationInstance.enabled.is_(True),
        )
    ).all()
    for instance in instances:
        sync_smtp_subscriptions(db, instance)

    feed_id = _payload_uuid(event, "feed_id", required=False)
    query = (
        select(IntegrationSubscription, IntegrationInstance)
        .join(IntegrationInstance, IntegrationInstance.id == IntegrationSubscription.integration_id)
        .where(
            IntegrationInstance.integration_type == "smtp",
            IntegrationInstance.enabled.is_(True),
            IntegrationSubscription.enabled.is_(True),
            IntegrationSubscription.event_type == event.event_type,
        )
    )
    if feed_id is None and event.event_type != "daily_digest":
        query = query.where(IntegrationSubscription.feed_scope == "all")
    elif feed_id is not None:
        query = query.outerjoin(
            IntegrationSubscriptionFeed,
            and_(
                IntegrationSubscriptionFeed.subscription_id == IntegrationSubscription.id,
                IntegrationSubscriptionFeed.feed_id == feed_id,
            ),
        ).where(
            or_(
                IntegrationSubscription.feed_scope == "all",
                IntegrationSubscriptionFeed.feed_id == feed_id,
            )
        )

    delivery_ids: list[uuid.UUID] = []
    for subscription, instance in db.execute(query).unique().all():
        existing = db.scalar(
            select(IntegrationDelivery).where(
                IntegrationDelivery.event_id == event.id,
                IntegrationDelivery.subscription_id == subscription.id,
                IntegrationDelivery.delivery_kind == "live",
            )
        )
        if existing is not None:
            delivery_ids.append(existing.id)
            continue
        delivery = IntegrationDelivery(
            integration_id=instance.id,
            subscription_id=subscription.id,
            event_id=event.id,
            owner_user_id=instance.owner_user_id,
            connector_type="smtp",
            event_type=event.event_type,
            delivery_kind="live",
            state="pending",
            idempotency_key=f"event:{event.id}:subscription:{subscription.id}:live",
            payload_json=dict(event.payload_json or {}),
            max_attempts=max(1, int(settings.integration_delivery_retry_max_attempts)),
        )
        db.add(delivery)
        db.flush()
        delivery_ids.append(delivery.id)
    return delivery_ids


def _routed_event_result(db: Session, event: IntegrationEvent) -> RoutedIntegrationEvent:
    generic = db.scalars(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.delivery_kind == "live",
        )
    ).all()
    generic_ids = [delivery.id for delivery in generic]
    webhook_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id).where(
                NotificationWebhookDelivery.integration_delivery_id.in_(generic_ids)
            )
        ).all()
    ) if generic_ids else []
    return RoutedIntegrationEvent(event.id, EVENT_ROUTED, webhook_ids, generic_ids)


def _payload_uuid(event: IntegrationEvent, key: str, *, required: bool = True) -> uuid.UUID | None:
    value = event.payload_json.get(key) if isinstance(event.payload_json, dict) else None
    if value is None or value == "":
        if required:
            raise IntegrationEventContextError(f"{event.event_type} event is missing {key}")
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise IntegrationEventContextError(f"{event.event_type} event has invalid {key}") from exc


def _routing_backoff_seconds(attempts: int) -> int:
    base = max(1, int(settings.integration_event_routing_backoff_seconds))
    return min(base * (2 ** max(0, attempts - 1)), 3600)
