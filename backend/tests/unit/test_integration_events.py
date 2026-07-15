import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

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
from app.schemas.integration import SMTPSettingsUpdate
from app.schemas.notification import NotificationWebhookTestResponse
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_events import (
    IntegrationEventContextError,
    emit_integration_event,
    list_recoverable_integration_event_ids,
    route_integration_event,
)
from app.services.integration_storage import apply_smtp_settings_update, get_or_create_smtp_integration
from app.services.integration_registry import get_integration_connector


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


def test_webhook_connector_processes_generic_delivery_and_updates_legacy_history(db_session, monkeypatch):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "Connector feed")
    item = _persist_item(db_session, feed)
    _persist_webhook(db_session, user, name="Connector webhook", feed_scope="all")
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"connector-process:{item.id}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id), "owner_user_id": str(user.id)},
    )
    routed = route_integration_event(db_session, event_id=event.id)
    delivery = db_session.get(IntegrationDelivery, routed.integration_delivery_ids[0])
    connector = get_integration_connector("webhook")
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda rendered: NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=12,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=None,
            error=None,
        ),
    )

    result = connector.process_delivery(
        db_session,
        delivery=delivery,
    )

    db_session.refresh(delivery)
    legacy = db_session.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.integration_delivery_id == delivery.id
        )
    )
    assert result.status == "succeeded"
    assert delivery.state == "succeeded"
    assert legacy is not None and legacy.delivery_state == "succeeded"
    assert result.followup_deliveries == ()
    assert result.followup_event_ids == ()


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


def test_route_event_reconciles_webhook_changes_written_by_older_node(db_session):
    user = _persist_user(db_session)
    selected_feed = _persist_feed(db_session, "Selected after legacy update")
    previously_selected_feed = _persist_feed(db_session, "Selected before legacy update")
    item = _persist_item(db_session, selected_feed)
    webhook = _persist_webhook(
        db_session,
        user,
        name="Legacy-updated webhook",
        feed_scope="selected",
        feed_ids=[previously_selected_feed.id],
    )
    subscription = db_session.get(IntegrationSubscription, webhook.subscription_id)
    assert subscription is not None
    assert subscription.filter_json["feed_ids"] == [str(previously_selected_feed.id)]

    # Simulate a pre-integration node that only updates the compatibility table.
    webhook.feed_ids_json = [str(selected_feed.id)]
    db_session.add(webhook)
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"legacy-update:{item.id}",
        payload={
            "item_id": str(item.id),
            "feed_id": str(selected_feed.id),
            "owner_user_id": str(user.id),
        },
    )

    result = route_integration_event(db_session, event_id=event.id)

    assert len(result.webhook_delivery_ids) == 1
    db_session.refresh(subscription)
    assert subscription.filter_json == {
        "feed_scope": "selected",
        "feed_ids": [str(selected_feed.id)],
    }
    assert set(
        db_session.scalars(
            select(IntegrationSubscriptionFeed.feed_id).where(
                IntegrationSubscriptionFeed.subscription_id == subscription.id
            )
        ).all()
    ) == {selected_feed.id}


def test_route_event_fans_out_to_smtp_generic_delivery(db_session):
    feed = _persist_feed(db_session, "SMTP routed feed")
    item = _persist_item(db_session, feed)
    smtp = get_or_create_smtp_integration(db_session)
    apply_smtp_settings_update(
        smtp,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["soc@example.com"],
            event_types=["rss_item_new"],
            feed_scope="selected",
            feed_ids=[feed.id],
        ),
    )
    db_session.add(smtp)
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"smtp-route:{item.id}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )

    result = route_integration_event(db_session, event_id=event.id)

    deliveries = db_session.scalars(
        select(IntegrationDelivery).where(IntegrationDelivery.event_id == event.id)
    ).all()
    assert len(result.integration_delivery_ids) == 1
    assert len(deliveries) == 1
    assert deliveries[0].connector_type == "smtp"
    assert deliveries[0].state == "pending"


def test_route_event_creates_smtp_delivery_for_selected_feed_subscription(db_session):
    feed = _persist_feed(db_session, "SMTP selected feed")
    item = _persist_item(db_session, feed)
    smtp = IntegrationInstance(
        id=uuid.uuid4(),
        system_key=f"smtp.test.{uuid.uuid4()}",
        name="SMTP",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={
            "host": "smtp.example.com",
            "from_email": "threatlens@example.com",
            "to_emails": ["soc@example.com"],
            "event_types": ["rss_item_new"],
            "feed_scope": "selected",
            "feed_ids": [str(feed.id)],
        },
    )
    db_session.add(smtp)
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"item:{item.id}:smtp-route",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )

    result = route_integration_event(db_session, event_id=event.id)

    delivery = db_session.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.connector_type == "smtp",
        )
    )
    assert delivery is not None
    assert result.integration_delivery_ids == [delivery.id]
    assert result.webhook_delivery_ids == []
    assert db_session.scalar(
        select(IntegrationSubscriptionFeed.feed_id).where(
            IntegrationSubscriptionFeed.subscription_id == delivery.subscription_id
        )
    ) == feed.id


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


def test_route_event_rejects_non_scalar_uuid_payload_with_context_error(db_session):
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="test",
        source_id=None,
        idempotency_key=f"invalid-payload:{uuid.uuid4()}",
        payload={"item_id": ["not", "a", "uuid"]},
    )

    with pytest.raises(IntegrationEventContextError, match="invalid item_id"):
        route_integration_event(db_session, event_id=event.id)


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
