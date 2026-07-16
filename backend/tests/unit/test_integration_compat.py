import uuid

from app.models.integration import IntegrationInstance, IntegrationSubscription
from app.models.notification_webhook import NotificationWebhook
from app.models.user import User
from app.services.integration_compat import ensure_webhook_integration


def test_legacy_webhook_is_repaired_into_generic_control_plane(db_session):
    user = User(
        id=uuid.uuid4(),
        email="legacy-webhook@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Legacy webhook",
        enabled=True,
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[str(uuid.uuid4())],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(webhook)
    db_session.flush()

    instance, subscription = ensure_webhook_integration(db_session, webhook)

    assert webhook.integration_id == instance.id
    assert webhook.subscription_id == subscription.id
    assert instance.id == webhook.id
    assert instance.owner_user_id == user.id
    assert instance.integration_type == "webhook"
    assert subscription.event_type == "rss_item_new"
    assert subscription.filter_json == {
        "feed_scope": "selected",
        "feed_ids": webhook.feed_ids_json,
    }

    webhook.name = "Updated webhook"
    webhook.enabled = False
    webhook.event_type = "feed_failing"
    instance_again, subscription_again = ensure_webhook_integration(db_session, webhook)

    assert instance_again.id == instance.id
    assert subscription_again.id == subscription.id
    assert instance_again.name == "Updated webhook"
    assert instance_again.enabled is False
    assert subscription_again.event_type == "feed_failing"
    assert subscription_again.enabled is False
    assert db_session.query(IntegrationInstance).filter_by(integration_type="webhook").count() == 1
    assert db_session.query(IntegrationSubscription).filter_by(integration_id=instance.id).count() == 1
