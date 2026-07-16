import uuid
from datetime import datetime, timedelta, timezone

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationDeliveryMetric,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_maintenance import (
    prune_integration_delivery_history,
    rollup_terminal_integration_deliveries,
)


def test_terminal_delivery_metrics_are_rolled_up_exactly_once(db_session, monkeypatch):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    _event, delivery, _legacy = _persist_terminal_webhook_delivery(
        db_session,
        completed_at=now - timedelta(hours=2),
    )
    db_session.add_all(
        [
            IntegrationAttempt(
                delivery_id=delivery.id,
                integration_id=delivery.integration_id,
                attempt_number=1,
                status="failed",
                started_at=now - timedelta(hours=2, seconds=2),
                finished_at=now - timedelta(hours=2, seconds=1),
                duration_ms=100,
            ),
            IntegrationAttempt(
                delivery_id=delivery.id,
                integration_id=delivery.integration_id,
                attempt_number=2,
                status="failed",
                started_at=now - timedelta(hours=2, seconds=1),
                finished_at=now - timedelta(hours=2),
                duration_ms=250,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_metrics_delay_seconds",
        0,
    )

    first = rollup_terminal_integration_deliveries(db_session, now=now)
    second = rollup_terminal_integration_deliveries(db_session, now=now)

    metric = db_session.query(IntegrationDeliveryMetric).one()
    assert first == 1
    assert second == 0
    assert metric.dead_letter_count == 1
    assert metric.failed_count == 0
    assert metric.attempt_count == 2
    assert metric.duration_total_ms == 350
    assert metric.duration_max_ms == 250


def test_retention_deletes_legacy_projection_only_after_generic_rollup(db_session, monkeypatch):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    event, delivery, legacy = _persist_terminal_webhook_delivery(
        db_session,
        completed_at=now - timedelta(days=2),
    )
    event_id = event.id
    delivery_id = delivery.id
    legacy_id = legacy.id
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_metrics_delay_seconds",
        0,
    )
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_retention_days",
        1,
    )
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_event_retention_days",
        1,
    )

    before_rollup = prune_integration_delivery_history(db_session, now=now)
    rolled_up = rollup_terminal_integration_deliveries(db_session, now=now)
    after_rollup = prune_integration_delivery_history(db_session, now=now)

    assert before_rollup["deliveries_deleted"] == 0
    assert before_rollup["webhook_deliveries_deleted"] == 0
    assert rolled_up == 1
    assert after_rollup["deliveries_deleted"] == 1
    assert after_rollup["webhook_deliveries_deleted"] == 1
    assert after_rollup["events_deleted"] == 1
    assert db_session.get(IntegrationDelivery, delivery_id) is None
    assert db_session.get(NotificationWebhookDelivery, legacy_id) is None
    assert db_session.get(IntegrationEvent, event_id) is None
    assert db_session.query(IntegrationDeliveryMetric).count() == 1


def _persist_terminal_webhook_delivery(
    db_session,
    *,
    completed_at: datetime,
) -> tuple[IntegrationEvent, IntegrationDelivery, NotificationWebhookDelivery]:
    user = User(
        id=uuid.uuid4(),
        email=f"metric-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
        is_approved=True,
    )
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        name="Metric webhook",
        integration_type="webhook",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(instance)
    db_session.flush()
    subscription = IntegrationSubscription(
        integration_id=instance.id,
        subscription_key="legacy-webhook",
        event_type="rss_item_new",
    )
    db_session.add(subscription)
    db_session.flush()
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        integration_id=instance.id,
        subscription_id=subscription.id,
        user_id=user.id,
        name="Metric webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    event = IntegrationEvent(
        id=uuid.uuid4(),
        event_type="rss_item_new",
        source_type="test",
        idempotency_key=f"metric-event:{uuid.uuid4()}",
        payload_json={},
        routing_state="routed",
        available_at=completed_at,
        routed_at=completed_at,
        created_at=completed_at,
    )
    delivery = IntegrationDelivery(
        id=uuid.uuid4(),
        integration_id=instance.id,
        subscription_id=subscription.id,
        event_id=event.id,
        owner_user_id=user.id,
        connector_type="webhook",
        event_type="rss_item_new",
        delivery_kind="live",
        state="dead_letter",
        idempotency_key=f"metric-delivery:{uuid.uuid4()}",
        payload_json={},
        attempt_count=2,
        completed_at=completed_at,
        dead_lettered_at=completed_at,
        created_at=completed_at,
        updated_at=completed_at,
    )
    db_session.add_all([webhook, event])
    db_session.flush()
    db_session.add(delivery)
    db_session.flush()
    legacy = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        integration_delivery_id=delivery.id,
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=2,
        success=False,
        timeout_seconds=10,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        error="HTTP 503",
        attempted_at=completed_at,
    )
    db_session.add(legacy)
    db_session.commit()
    return event, delivery, legacy
