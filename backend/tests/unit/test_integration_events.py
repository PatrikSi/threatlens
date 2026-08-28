import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.models.feed import Feed
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence
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
from app.schemas.integration import SMTPHookWrite, SMTPSettingsUpdate
from app.schemas.notification import NotificationWebhookTestResponse
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_events import (
    emit_integration_event,
    list_recoverable_integration_event_ids,
    reserve_recoverable_integration_events,
    route_integration_event,
)
from app.services.integration_processors import process_smtp_integration_delivery
from app.services.integration_storage import (
    apply_smtp_settings_update,
    get_or_create_smtp_integration,
)
from app.services.integration_smtp_hooks import create_smtp_hook
from app.services.integration_registry import get_integration_connector
from app.services.notification_webhook_storage import decrypt_notification_text
from app.services.smtp_delivery_eligibility import SMTP_SOURCE_OWNER_IDS_KEY
from app.services.smtp_integration import SMTPNotificationResult


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


def test_emit_integration_event_binds_explicit_schema_to_payload(db_session):
    event = emit_integration_event(
        db_session,
        event_type="daily_digest",
        source_type="daily_brief",
        source_id=uuid.uuid4(),
        idempotency_key=f"explicit-schema:{uuid.uuid4()}",
        payload={"schema_version": 2, "brief_id": str(uuid.uuid4())},
        schema_version=1,
    )

    assert event.schema_version == 1
    assert event.payload_json["schema_version"] == 1


def test_route_event_matches_normalized_feed_subscriptions_and_preserves_legacy_history(
    db_session,
):
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
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.id.in_(result.webhook_delivery_ids)
        )
    ).all()
    assert {delivery.webhook_id for delivery in legacy} == {
        all_webhook.id,
        selected_webhook.id,
    }
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


def test_webhook_connector_processes_generic_delivery_and_updates_legacy_history(
    db_session, monkeypatch
):
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
        payload={
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            "owner_user_id": str(user.id),
        },
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
    previously_selected_feed = _persist_feed(
        db_session, "Selected before legacy update"
    )
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


def test_alert_event_routes_to_ownerless_default_and_created_smtp_hooks(
    db_session,
    monkeypatch,
):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "SMTP alert routing feed")
    item = _persist_item(db_session, feed)
    db_session.add(
        AlertInterest(
            user_id=user.id,
            name="Durable event watch",
            category="threat",
            keywords=["durable"],
            enabled=True,
        )
    )
    settings = SMTPSettingsUpdate(
        enabled=True,
        host="smtp.example.com",
        from_email="threatlens@example.com",
        to_emails=["soc@example.com"],
        event_types=["alert_match"],
    )
    default_smtp = get_or_create_smtp_integration(db_session)
    apply_smtp_settings_update(default_smtp, settings)
    created_smtp = create_smtp_hook(
        db_session,
        SMTPHookWrite(name="Secondary alert relay", settings=settings),
    )
    legacy_event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"smtp-alert-route-legacy:{item.id}",
        payload={
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            "evaluation_request_id": str(uuid.uuid4()),
        },
    )
    legacy_result = route_integration_event(db_session, event_id=legacy_event.id)
    current_payload = dict(legacy_event.payload_json)
    current_payload.update(
        {
            "schema_version": 3,
            "owner_user_id": str(user.id),
            "occurrence_ids": [],
            "occurrence_ids_by_owner": [
                {"owner_user_id": str(user.id), "occurrence_ids": []}
            ],
        }
    )
    event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"smtp-alert-route-current:{item.id}",
        payload=current_payload,
        schema_version=3,
    )

    result = route_integration_event(db_session, event_id=event.id)

    legacy_deliveries = db_session.scalars(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == legacy_event.id,
            IntegrationDelivery.connector_type == "smtp",
        )
    ).all()
    deliveries = db_session.scalars(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.connector_type == "smtp",
        )
    ).all()
    assert legacy_result.status == "routed"
    assert result.status == "routed"
    assert {delivery.integration_id for delivery in legacy_deliveries} == {
        default_smtp.id,
        created_smtp.id,
    }
    assert {delivery.integration_id for delivery in deliveries} == {
        default_smtp.id,
        created_smtp.id,
    }
    assert default_smtp.owner_user_id is None
    assert created_smtp.owner_user_id is None
    assert {delivery.owner_user_id for delivery in deliveries} == {None}
    assert all(
        delivery.payload_json["owner_user_id"] == str(user.id)
        for delivery in deliveries
    )
    assert all(
        delivery.payload_json[SMTP_SOURCE_OWNER_IDS_KEY] == [str(user.id)]
        for delivery in deliveries
    )
    assert all("alert_matches" not in delivery.payload_json for delivery in deliveries)

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        return SMTPNotificationResult(
            success=True,
            duration_ms=10,
            recipient_count=len(active.to_emails),
            accepted_count=len(active.to_emails),
            error_code=None,
            error=None,
            server_message="250 accepted",
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
            delivery_outcome="accepted",
            accepted_recipients=tuple(active.to_emails),
        )

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)
    db_session.commit()
    processing_results = [
        process_smtp_integration_delivery(db_session, delivery_id=delivery.id)
        for delivery in deliveries
    ]
    assert [result.status for result in processing_results] == [
        "succeeded",
        "succeeded",
    ]


def test_ownerless_smtp_route_upgrades_legacy_v1_alert_context(db_session):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "Legacy v1 SMTP feed")
    item = _persist_item(db_session, feed)
    smtp = get_or_create_smtp_integration(db_session)
    apply_smtp_settings_update(
        smtp,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["soc@example.com"],
            event_types=["alert_match"],
        ),
    )
    event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"legacy-v1-smtp:{item.id}",
        schema_version=1,
        payload={
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            "evaluation_request_id": str(uuid.uuid4()),
        },
    )
    _persist_alert_occurrence(
        db_session,
        owner=user,
        item=item,
        event=event,
        name="Legacy v1 watch",
    )
    item.title = "Mutable item title"
    feed.name = "Mutable feed name"
    db_session.add_all([item, feed])
    unrelated_user = _persist_user(db_session)
    db_session.add(
        AlertInterest(
            user_id=unrelated_user.id,
            name="Current mutable rule",
            category="threat",
            keywords=["durable"],
            enabled=True,
        )
    )
    db_session.flush()

    result = route_integration_event(db_session, event_id=event.id)

    delivery = db_session.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.connector_type == "smtp",
        )
    )
    assert result.status == "routed"
    assert delivery is not None
    assert delivery.owner_user_id is None
    assert delivery.payload_json["schema_version"] == 3
    assert delivery.payload_json["owner_user_id"] == str(user.id)
    assert delivery.payload_json[SMTP_SOURCE_OWNER_IDS_KEY] == [str(user.id)]
    assert delivery.payload_json["alert"]["primary_name"] == "Legacy v1 watch"
    assert delivery.payload_json["item"]["title"] == "Integration event item"
    assert delivery.payload_json["feed"]["name"] == "Legacy v1 SMTP feed"


def test_future_smtp_alert_schema_remains_recoverable_past_attempt_limit(
    db_session,
    monkeypatch,
):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "Future SMTP alert feed")
    item = _persist_item(db_session, feed)
    _persist_webhook(
        db_session,
        user,
        name="Future alert webhook",
        feed_scope="all",
        event_type="alert_match",
    )
    smtp = get_or_create_smtp_integration(db_session)
    apply_smtp_settings_update(
        smtp,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["soc@example.com"],
            event_types=["alert_match"],
        ),
    )
    event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"future-smtp-alert:{item.id}",
        schema_version=4,
        payload={
            "schema_version": 4,
            "item_id": str(item.id),
            "feed_id": {"future_reference": str(feed.id)},
            "evaluation_request_id": str(uuid.uuid4()),
        },
    )
    monkeypatch.setattr(
        "app.services.integration_events.settings.integration_event_routing_max_attempts",
        1,
    )

    first = route_integration_event(db_session, event_id=event.id)
    second = route_integration_event(db_session, event_id=event.id)

    db_session.refresh(event)
    assert first.status == "failed"
    assert second.status == "failed"
    assert event.routing_attempt_count == 0
    assert "newer schema version 4" in (event.last_error or "")
    assert all(error.compatibility_wait for error in first.routing_errors)
    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == event.id
            )
        )
        is None
    )
    assert db_session.scalar(select(NotificationWebhookDelivery.id)) is None
    assert event.id in list_recoverable_integration_event_ids(
        db_session,
        now=event.available_at + timedelta(seconds=1),
    )


def test_future_resource_event_with_non_object_payload_waits_before_connectors(
    db_session,
):
    user = _persist_user(db_session)
    _persist_webhook(
        db_session,
        user,
        name="Future resource webhook",
        feed_scope="all",
    )
    smtp = get_or_create_smtp_integration(db_session)
    apply_smtp_settings_update(
        smtp,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["soc@example.com"],
            event_types=["rss_item_new"],
        ),
    )
    event = IntegrationEvent(
        event_type="rss_item_new",
        schema_version=3,
        source_type="item",
        source_id=str(uuid.uuid4()),
        idempotency_key=f"future-resource:{uuid.uuid4()}",
        payload_json=["future", {"resource_reference": "opaque"}],
    )
    db_session.add(event)
    db_session.flush()

    result = route_integration_event(db_session, event_id=event.id)

    db_session.refresh(event)
    assert result.status == "failed"
    assert event.routing_attempt_count == 0
    assert "newer schema version 3" in (event.last_error or "")
    assert all(error.compatibility_wait for error in result.routing_errors)
    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == event.id
            )
        )
        is None
    )
    assert db_session.scalar(select(NotificationWebhookDelivery.id)) is None


def test_smtp_route_rejects_oversized_legacy_v2_owner_context(db_session):
    feed = _persist_feed(db_session, "Oversized SMTP alert feed")
    smtp = get_or_create_smtp_integration(db_session)
    apply_smtp_settings_update(
        smtp,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["soc@example.com"],
            event_types=["alert_match"],
        ),
    )
    alert_context = {
        "count": 1,
        "primary_name": "Oversized alert context",
        "names": ["Oversized alert context"],
        "categories": ["threat"],
        "matched_keywords": ["oversized"],
    }
    event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=uuid.uuid4(),
        idempotency_key=f"oversized-v2-smtp:{uuid.uuid4()}",
        schema_version=2,
        payload={
            "schema_version": 2,
            "evaluation_request_id": str(uuid.uuid4()),
            "feed_id": str(feed.id),
            "alert": alert_context,
            "alert_matches": [
                {"owner_user_id": str(uuid.uuid4()), **alert_context}
                for _ in range(101)
            ],
        },
    )

    result = route_integration_event(db_session, event_id=event.id)

    assert result.status == "dead_letter"
    assert "smtp_source_owner_context_too_large" in (event.last_error or "")
    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == event.id,
                IntegrationDelivery.connector_type == "smtp",
            )
        )
        is None
    )


def test_route_event_keeps_valid_delivery_recoverable_when_connector_is_unknown(
    db_session, monkeypatch
):
    feed = _persist_feed(db_session, "Rolling upgrade feed")
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
        ),
    )
    future = IntegrationInstance(
        name="Future connector",
        integration_type="future_destination",
        direction="destination",
        enabled=True,
    )
    db_session.add(future)
    db_session.flush()
    db_session.add(
        IntegrationSubscription(
            integration_id=future.id,
            subscription_key="event:rss_item_new",
            event_type="rss_item_new",
            enabled=True,
        )
    )
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"rolling-upgrade:{item.id}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )

    result = route_integration_event(db_session, event_id=event.id)
    db_session.refresh(event)

    assert result.status == "failed"
    assert len(result.integration_delivery_ids) == 1
    assert (
        db_session.get(
            IntegrationDelivery, result.integration_delivery_ids[0]
        ).connector_type
        == "smtp"
    )
    assert "future_destination" in (event.last_error or "")
    assert "worker is upgraded" in (event.last_error or "")
    assert event.id in list_recoverable_integration_event_ids(
        db_session,
        now=event.available_at + timedelta(seconds=1),
    )

    monkeypatch.setattr(
        "app.services.integration_events.settings.integration_event_routing_max_attempts",
        1,
    )
    second = route_integration_event(db_session, event_id=event.id)

    assert second.status == "failed"
    assert event.routing_attempt_count == 0
    assert all(error.compatibility_wait for error in second.routing_errors)
    assert event.id in list_recoverable_integration_event_ids(
        db_session,
        now=event.available_at + timedelta(hours=1),
    )


def test_unsupported_connector_subscription_does_not_roll_back_valid_route(
    db_session, monkeypatch
):
    feed = _persist_feed(db_session, "Unsupported connector feed")
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
        ),
    )
    unsupported = IntegrationInstance(
        name="Known but unsupported",
        integration_type="known_destination",
        direction="destination",
        enabled=True,
    )
    db_session.add(unsupported)
    db_session.flush()
    db_session.add(
        IntegrationSubscription(
            integration_id=unsupported.id,
            subscription_key="event:rss_item_new",
            event_type="rss_item_new",
            enabled=True,
        )
    )
    db_session.flush()
    registered_lookup = get_integration_connector
    connector = SimpleNamespace(
        definition=SimpleNamespace(integration_type="known_destination"),
        supports_event_type=lambda _event_type: False,
    )
    monkeypatch.setattr(
        "app.services.integration_events.get_integration_connector",
        lambda integration_type: (
            connector
            if integration_type == "known_destination"
            else registered_lookup(integration_type)
        ),
    )
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"unsupported-connector:{item.id}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )

    monkeypatch.setattr(
        "app.services.integration_events.settings.integration_event_routing_max_attempts",
        1,
    )
    result = route_integration_event(db_session, event_id=event.id)
    retried = route_integration_event(db_session, event_id=event.id)

    assert result.status == "failed"
    assert retried.status == "failed"
    assert event.routing_attempt_count == 0
    assert all(error.compatibility_wait for error in retried.routing_errors)
    assert len(result.integration_delivery_ids) == 1
    assert (
        db_session.get(
            IntegrationDelivery, result.integration_delivery_ids[0]
        ).connector_type
        == "smtp"
    )
    assert "does not support persisted event type" in (event.last_error or "")


def test_v2_item_event_routes_from_immutable_snapshots(db_session):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "Original feed")
    item = _persist_item(db_session, feed)
    webhook = _persist_webhook(
        db_session, user, name="Snapshot webhook", feed_scope="all"
    )
    webhook.body_mode = "raw"
    webhook.body_template = "{{item.title}}|{{feed.name}}"
    db_session.add(webhook)
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"immutable-item:{item.id}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    assert event.schema_version == 2
    assert event.payload_json["item"]["title"] == "Integration event item"
    assert event.payload_json["feed"]["name"] == "Original feed"

    item.title = "Mutated item"
    feed.name = "Mutated feed"
    db_session.flush()
    result = route_integration_event(db_session, event_id=event.id)

    delivery = db_session.get(
        NotificationWebhookDelivery, result.webhook_delivery_ids[0]
    )
    generic = db_session.get(IntegrationDelivery, result.integration_delivery_ids[0])
    assert (
        decrypt_notification_text(delivery.rendered_body)
        == "Integration event item|Original feed"
    )
    assert delivery.item_title_snapshot == "Integration event item"
    assert delivery.feed_name_snapshot == "Original feed"
    assert generic.payload_json["item"]["title"] == "Integration event item"
    assert generic.payload_json["feed"]["name"] == "Original feed"


def test_v1_item_event_retains_legacy_id_hydration(db_session):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "Legacy original feed")
    item = _persist_item(db_session, feed)
    webhook = _persist_webhook(
        db_session, user, name="Legacy webhook", feed_scope="all"
    )
    webhook.body_mode = "raw"
    webhook.body_template = "{{item.title}}|{{feed.name}}"
    db_session.add(webhook)
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"legacy-item:{item.id}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
        schema_version=1,
    )
    item.title = "Legacy hydrated item"
    feed.name = "Legacy hydrated feed"
    db_session.flush()

    result = route_integration_event(db_session, event_id=event.id)

    delivery = db_session.get(
        NotificationWebhookDelivery, result.webhook_delivery_ids[0]
    )
    assert event.schema_version == 1
    assert (
        decrypt_notification_text(delivery.rendered_body)
        == "Legacy hydrated item|Legacy hydrated feed"
    )


def test_v2_alert_event_preserves_owner_specific_match_snapshot(db_session):
    user = _persist_user(db_session)
    feed = _persist_feed(db_session, "Alert snapshot feed")
    item = _persist_item(db_session, feed)
    alert = AlertInterest(
        user_id=user.id,
        name="Original durable alert",
        category="threat",
        keywords=["durable"],
        enabled=True,
    )
    db_session.add(alert)
    webhook = _persist_webhook(
        db_session, user, name="Alert snapshot webhook", feed_scope="all"
    )
    webhook.event_type = "alert_match"
    webhook.body_mode = "raw"
    webhook.body_template = "{{alert.primary_name}}|{{alert.matched_keywords}}"
    db_session.add(webhook)
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"immutable-alert:{item.id}",
        payload={
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            "evaluation_request_id": str(uuid.uuid4()),
        },
    )
    assert event.schema_version == 2
    assert event.payload_json["alert_matches"][0]["owner_user_id"] == str(user.id)

    alert.name = "Mutated alert"
    alert.keywords = ["no-longer-matches"]
    item.summary = "No matching text remains"
    db_session.flush()
    result = route_integration_event(db_session, event_id=event.id)

    delivery = db_session.get(
        NotificationWebhookDelivery, result.webhook_delivery_ids[0]
    )
    generic = db_session.get(IntegrationDelivery, result.integration_delivery_ids[0])
    assert (
        decrypt_notification_text(delivery.rendered_body)
        == "Original durable alert|durable"
    )
    assert generic.payload_json["alert"]["primary_name"] == "Original durable alert"
    assert "alert_matches" not in generic.payload_json


def test_route_daily_digest_event_renders_immutable_ai_brief_context_for_webhook(
    db_session,
):
    user = _persist_user(db_session)
    selected_feed = _persist_feed(db_session, "Selected but ignored for AI brief")
    webhook = _persist_webhook(
        db_session,
        user,
        name="AI brief webhook",
        feed_scope="selected",
        feed_ids=[selected_feed.id],
    )
    webhook.event_type = "daily_digest"
    db_session.add(webhook)
    db_session.flush()
    generated_at = datetime(2026, 7, 18, 9, 0, 8, tzinfo=timezone.utc)
    brief_id = uuid.uuid4()
    event = emit_integration_event(
        db_session,
        event_type="daily_digest",
        source_type="ai_daily_brief",
        source_id=brief_id,
        idempotency_key=f"ai-daily-brief:{brief_id}",
        payload={
            "daily_brief_id": str(brief_id),
            "brief_date": "2026-07-18",
            "scope_key": "ai_daily_brief:2026-07-18",
            "daily_brief": {
                "schema_version": 1,
                "id": str(brief_id),
                "date": "2026-07-18",
                "generated_at": generated_at.isoformat(),
                "window_start": (generated_at - timedelta(hours=24)).isoformat(),
                "window_end": generated_at.isoformat(),
                "title": "AI brief title",
                "text": "Persisted AI brief body",
                "key_points": ["First point"],
                "recommended_actions": ["First action"],
                "item_count": 6,
                "feed_names": ["CISA"],
                "top_titles": ["Source title"],
            },
        },
    )

    result = route_integration_event(db_session, event_id=event.id)

    assert len(result.webhook_delivery_ids) == 1
    delivery = db_session.get(
        NotificationWebhookDelivery, result.webhook_delivery_ids[0]
    )
    assert delivery is not None
    assert delivery.item_title_snapshot == "AI brief title"
    assert delivery.feed_name_snapshot == "AI Daily Brief"
    assert delivery.scope_key == "ai_daily_brief:2026-07-18"


def test_route_legacy_rolling_digest_event_fails_with_clear_context_error(db_session):
    user = _persist_user(db_session)
    webhook = _persist_webhook(
        db_session, user, name="Legacy digest webhook", feed_scope="all"
    )
    webhook.event_type = "daily_digest"
    db_session.add(webhook)
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="daily_digest",
        source_type="digest_window",
        source_id="2026-07-18",
        idempotency_key=f"legacy-digest:{uuid.uuid4()}",
        payload={"scope_key": "2026-07-18"},
    )

    result = route_integration_event(db_session, event_id=event.id)

    assert result.status == "dead_letter"
    assert "Legacy rolling daily digest events" in (event.last_error or "")


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
    assert (
        db_session.scalar(
            select(IntegrationSubscriptionFeed.feed_id).where(
                IntegrationSubscriptionFeed.subscription_id == delivery.subscription_id
            )
        )
        == feed.id
    )


def test_recoverable_event_scan_excludes_future_routed_and_dead_letter_events(
    db_session,
):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    due = _persist_event(
        db_session, state="pending", available_at=now - timedelta(seconds=1)
    )
    failed = _persist_event(db_session, state="failed", available_at=now)
    stale_routing = _persist_event(
        db_session,
        state="routing",
        available_at=now - timedelta(minutes=5),
        claimed_at=now - timedelta(minutes=5),
    )
    _persist_event(db_session, state="pending", available_at=now + timedelta(minutes=1))
    _persist_event(db_session, state="routed", available_at=now - timedelta(minutes=1))
    _persist_event(
        db_session, state="dead_letter", available_at=now - timedelta(minutes=1)
    )

    event_ids = list_recoverable_integration_event_ids(db_session, now=now)

    assert set(event_ids) == {due.id, failed.id, stale_routing.id}


def test_event_recovery_reservation_suppresses_duplicate_publication_sweeps(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.integration_events.settings.integration_event_routing_stale_after_seconds",
        30,
    )
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    due = _persist_event(
        db_session,
        state="pending",
        available_at=now - timedelta(seconds=1),
    )

    first = reserve_recoverable_integration_events(db_session, now=now)
    db_session.commit()
    second = reserve_recoverable_integration_events(
        db_session,
        now=now + timedelta(seconds=10),
    )

    assert first.event_ids == (due.id,)
    assert second.event_ids == ()
    assert due.id in list_recoverable_integration_event_ids(
        db_session,
        now=now + timedelta(seconds=31),
    )


def test_route_event_rejects_non_scalar_uuid_payload_with_context_error(db_session):
    user = _persist_user(db_session)
    _persist_webhook(db_session, user, name="Invalid payload webhook", feed_scope="all")
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="test",
        source_id=None,
        idempotency_key=f"invalid-payload:{uuid.uuid4()}",
        payload={"item_id": ["not", "a", "uuid"]},
    )

    result = route_integration_event(db_session, event_id=event.id)

    assert result.status == "dead_letter"
    assert "invalid item_id" in (event.last_error or "")


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
    feed = Feed(
        id=uuid.uuid4(), name=name, url=f"https://example.com/{uuid.uuid4()}.xml"
    )
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


def _persist_alert_occurrence(
    db_session,
    *,
    owner: User,
    item: Item,
    event: IntegrationEvent,
    name: str,
) -> AlertOccurrence:
    feed = db_session.get(Feed, item.feed_id)
    assert feed is not None
    occurrence = AlertOccurrence(
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=owner.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        integration_event_id=event.id,
        rule_revision=1,
        item_content_hash=uuid.uuid4().hex * 2,
        alert_name_snapshot=name,
        alert_category_snapshot="threat",
        alert_keywords_snapshot=["durable"],
        matched_keywords=["durable"],
        source_snapshot_json={
            "item": {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
                "canonical_url": item.canonical_url,
                "published_at": item.published_at.isoformat()
                if item.published_at is not None
                else None,
                "first_seen_at": item.first_seen_at.isoformat()
                if item.first_seen_at is not None
                else None,
                "status": item.status,
            },
            "feed": {
                "id": str(feed.id),
                "name": feed.name,
                "url": feed.url,
            },
        },
        severity_snapshot="medium",
    )
    db_session.add(occurrence)
    db_session.flush()
    return occurrence


def _persist_webhook(
    db_session,
    user: User,
    *,
    name: str,
    feed_scope: str,
    feed_ids: list[uuid.UUID] | None = None,
    event_type: str = "rss_item_new",
) -> NotificationWebhook:
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        enabled=True,
        event_type=event_type,
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
