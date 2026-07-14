import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.feed import Feed
from app.models.integration import IntegrationDelivery, IntegrationEvent, IntegrationSubscriptionFeed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_events import (
    emit_integration_event,
    list_recoverable_integration_event_ids,
    route_integration_event,
)


def test_emit_integration_event_is_idempotent(db_session):
    first = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=uuid.uuid4(),
        idempotency_key="item:test:rss_item_new",
        payload={"item_id": str(uuid.uuid4())},
    )
    second = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=uuid.uuid4(),
        idempotency_key="item:test:rss_item_new",
        payload={"item_id": str(uuid.uuid4())},
    )

    assert second.id == first.id
    assert db_session.query(IntegrationEvent).count() == 1


def test_route_event_matches_normalized_feed_subscriptions_and_preserves_legacy_history(db_session):
    user = _persist_user(db_session)
    selected_feed = _persist_feed(db_session, "Selected feed")
    other_feed = _persist_feed(db_session, "Other feed")
    item = _persist_item(db_session, selected_feed)
    all_webhook = _persist_webhook(db_session, user, name="All feeds", feed_scope="all")
    selected_webhook = _persist_webhook(
        db_session,
        user,
        name="Selected feed",
        feed_scope="selected",
        feed_ids=[selected_feed.id],
    )
    _persist_webhook(
        db_session,
        user,
        name="Other feed",
        feed_scope="selected",
        feed_ids=[other_feed.id],
    )
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"item:{item.id}:rss_item_new",
        payload={
            "item_id": str(item.id),
            "feed_id": str(selected_feed.id),
            "owner_user_id": str(user.id),
        },
    )

    result = route_integration_event(db_session, event_id=event.id)
    db_session.flush()

    assert result.status == "routed"
    assert len(result.webhook_delivery_ids) == 2
    legacy = db_session.scalars(
        select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.id.in_(result.webhook_delivery_ids))
    ).all()
    assert {delivery.webhook_id for delivery in legacy} == {all_webhook.id, selected_webhook.id}
    generic = db_session.scalars(
        select(IntegrationDelivery).where(IntegrationDelivery.event_id == event.id)
    ).all()
    assert {delivery.id for delivery in generic} == set(result.integration_delivery_ids)
    assert {delivery.id for delivery in generic} == {
        delivery.integration_delivery_id for delivery in legacy
    }

    repeated = route_integration_event(db_session, event_id=event.id)

    assert set(repeated.webhook_delivery_ids) == set(result.webhook_delivery_ids)
    assert db_session.query(NotificationWebhookDelivery).count() == 2
    assert db_session.query(IntegrationDelivery).count() == 2


def test_webhook_repair_ignores_deleted_and_invalid_selected_feed_ids(db_session):
    user = _persist_user(db_session)
    live_feed = _persist_feed(db_session, "Live feed")
    missing_feed_id = uuid.uuid4()
    webhook = _persist_webhook(
        db_session,
        user,
        name="Stale selection",
        feed_scope="selected",
        feed_ids=[live_feed.id, missing_feed_id],
    )

    normalized_feed_ids = set(
        db_session.scalars(
            select(IntegrationSubscriptionFeed.feed_id).where(
                IntegrationSubscriptionFeed.subscription_id == webhook.subscription_id
            )
        ).all()
    )

    assert normalized_feed_ids == {live_feed.id}


def test_recoverable_event_scan_excludes_future_routed_and_dead_letter_events(db_session):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    due = _persist_event(db_session, state="pending", available_at=now - timedelta(seconds=1))
    failed = _persist_event(db_session, state="failed", available_at=now)
    stale_routing = _persist_event(
        db_session,
        state="routing",
        available_at=now - timedelta(minutes=5),
        claimed_at=now - timedelta(minutes=5),
    )
    _persist_event(db_session, state="pending", available_at=now + timedelta(minutes=1))
    _persist_event(db_session, state="routed", available_at=now - timedelta(minutes=1))
    _persist_event(db_session, state="dead_letter", available_at=now - timedelta(minutes=1))

    event_ids = list_recoverable_integration_event_ids(db_session, now=now)

    assert set(event_ids) == {due.id, failed.id, stale_routing.id}


def _persist_user(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"integration-event-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _persist_feed(db_session, name: str) -> Feed:
    feed = Feed(id=uuid.uuid4(), name=name, url=f"https://example.com/{uuid.uuid4()}.xml")
    db_session.add(feed)
    db_session.flush()
    return feed


def _persist_item(db_session, feed: Feed) -> Item:
    item_id = uuid.uuid4()
    item = Item(
        id=item_id,
        feed_id=feed.id,
        source_guid=str(item_id),
        url=f"https://example.com/items/{item_id}",
        canonical_url=f"https://example.com/items/{item_id}",
        title="Integration event item",
        summary="A durable outbox event",
        dedupe_key=f"item:{item_id}",
        content_hash=uuid.uuid4().hex,
    )
    db_session.add(item)
    db_session.flush()
    return item


def _persist_webhook(
    db_session,
    user: User,
    *,
    name: str,
    feed_scope: str,
    feed_ids: list[uuid.UUID] | None = None,
) -> NotificationWebhook:
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        enabled=True,
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope=feed_scope,
        feed_ids_json=[str(feed_id) for feed_id in feed_ids or []],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    return webhook


def _persist_event(
    db_session,
    *,
    state: str,
    available_at: datetime,
    claimed_at: datetime | None = None,
) -> IntegrationEvent:
    event = IntegrationEvent(
        id=uuid.uuid4(),
        event_type="rss_item_new",
        source_type="test",
        idempotency_key=f"test:{uuid.uuid4()}",
        payload_json={},
        routing_state=state,
        available_at=available_at,
        claimed_at=claimed_at,
    )
    db_session.add(event)
    db_session.flush()
    return event
