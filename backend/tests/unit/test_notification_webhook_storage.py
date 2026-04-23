import uuid

import pytest

from app.core.config import get_settings
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.notification_webhook_storage import (
    upgrade_notification_webhook_delivery_secret_storage,
    upgrade_notification_webhook_secret_storage,
)
from app.services.secret_storage import decrypt_json, decrypt_text, is_encrypted_json, is_encrypted_text


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_upgrade_notification_webhook_secret_storage_rewraps_legacy_plaintext():
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Legacy webhook",
        url_template="https://hooks.example.com/notify",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[{"key": "token", "value": "alpha"}],
        headers_json=[{"key": "Authorization", "value": "Bearer secret"}],
        body_mode="raw",
        body_fields_json=[{"key": "title", "value": "ThreatLens"}],
        body_template='{"title":"ThreatLens"}',
        timeout_seconds=10,
    )

    changed = upgrade_notification_webhook_secret_storage(webhook)

    assert changed is True
    assert is_encrypted_text(webhook.url_template)
    assert is_encrypted_json(webhook.query_params_json)
    assert is_encrypted_json(webhook.headers_json)
    assert is_encrypted_json(webhook.body_fields_json)
    assert is_encrypted_text(webhook.body_template)
    assert decrypt_text(webhook.url_template) == "https://hooks.example.com/notify"
    assert decrypt_json(webhook.query_params_json) == [{"key": "token", "value": "alpha"}]
    assert decrypt_json(webhook.headers_json) == [{"key": "Authorization", "value": "Bearer secret"}]
    assert decrypt_json(webhook.body_fields_json) == [{"key": "title", "value": "ThreatLens"}]
    assert decrypt_text(webhook.body_template) == '{"title":"ThreatLens"}'


def test_upgrade_notification_webhook_delivery_secret_storage_rewraps_legacy_plaintext():
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="pending",
        attempt_count=0,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/notify?token=alpha",
        rendered_method="POST",
        rendered_headers_json=[{"key": "Authorization", "value": "Bearer secret"}],
        rendered_query_params_json=[{"key": "token", "value": "alpha"}],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview="accepted",
        error=None,
    )

    changed = upgrade_notification_webhook_delivery_secret_storage(delivery)

    assert changed is True
    assert is_encrypted_text(delivery.rendered_url)
    assert is_encrypted_json(delivery.rendered_headers_json)
    assert is_encrypted_json(delivery.rendered_query_params_json)
    assert is_encrypted_text(delivery.rendered_body)
    assert is_encrypted_text(delivery.response_body_preview)
    assert decrypt_text(delivery.rendered_url) == "https://hooks.example.com/notify?token=alpha"
    assert decrypt_json(delivery.rendered_headers_json) == [{"key": "Authorization", "value": "Bearer secret"}]
    assert decrypt_json(delivery.rendered_query_params_json) == [{"key": "token", "value": "alpha"}]
    assert decrypt_text(delivery.rendered_body) == '{"title":"ThreatLens"}'
    assert decrypt_text(delivery.response_body_preview) == "accepted"
