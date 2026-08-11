import uuid
from types import SimpleNamespace

from app.schemas.notification import NotificationWebhookField, NotificationWebhookWrite
from app.services import notification_webhooks
from app.services.notification_webhook_contexts import build_sample_feed_for_event
from app.services.notification_webhook_history import (
    NotificationDeliveryReservationBatch,
    is_retryable_notification_outcome,
)
from app.services.notification_webhook_requests import (
    THREATLENS_SOURCE_DELIVERY_ID_HEADER,
    render_notification_request,
    restore_saved_request_target,
)


def test_notification_webhooks_preserves_extracted_import_surface():
    assert (
        notification_webhooks.NotificationDeliveryReservationBatch
        is NotificationDeliveryReservationBatch
    )
    assert (
        notification_webhooks.render_notification_request is render_notification_request
    )
    assert (
        notification_webhooks._restore_saved_request_target
        is restore_saved_request_target
    )
    assert (
        notification_webhooks.THREATLENS_SOURCE_DELIVERY_ID_HEADER
        == THREATLENS_SOURCE_DELIVERY_ID_HEADER
    )


def test_render_notification_request_keeps_body_and_delivery_headers():
    delivery_id = uuid.uuid4()
    source_delivery_id = uuid.uuid4()
    payload = NotificationWebhookWrite(
        name="Contract",
        url_template="https://hooks.example.com/events",
        method="POST",
        headers=[NotificationWebhookField(key="X-Feed", value="{{ feed.name }}")],
        body_mode="json",
        body_fields=[
            NotificationWebhookField(key="item.title", value="{{ item.title }}")
        ],
    )

    rendered = render_notification_request(
        payload,
        user=SimpleNamespace(id=uuid.uuid4(), email="owner@example.com"),
        feed=SimpleNamespace(id=uuid.uuid4(), name="Unit42"),
        item=SimpleNamespace(id=uuid.uuid4(), title="Threat report"),
        delivery_id=delivery_id,
        source_delivery_id=source_delivery_id,
    )

    assert rendered.json_body == {"item": {"title": "Threat report"}}
    assert rendered.headers_dict["X-Feed"] == "Unit42"
    assert rendered.headers_dict["X-ThreatLens-Delivery-ID"] == str(delivery_id)
    assert rendered.headers_dict[THREATLENS_SOURCE_DELIVERY_ID_HEADER] == str(
        source_delivery_id
    )


def test_restore_saved_request_target_avoids_duplicate_query_parameters():
    fields = [
        NotificationWebhookField(key="token", value="secret"),
        NotificationWebhookField(key="empty", value=""),
    ]

    url, query_pairs = restore_saved_request_target(
        "https://hooks.example.com/events?token=secret&empty=",
        fields,
    )

    assert url == "https://hooks.example.com/events"
    assert query_pairs == [("token", "secret"), ("empty", "")]


def test_extracted_context_and_retry_rules_match_webhook_contract():
    sample = build_sample_feed_for_event(
        SimpleNamespace(id=uuid.uuid4(), name="Example", error_count=0),
        "feed_failing",
    )

    assert (
        sample.error_count == notification_webhooks.FEED_FAILING_NOTIFICATION_THRESHOLD
    )
    assert is_retryable_notification_outcome(status_code=503, error="HTTP 503") is True
    assert is_retryable_notification_outcome(status_code=400, error="HTTP 400") is False
    assert (
        is_retryable_notification_outcome(
            status_code=None, error="render_error:unknown variable"
        )
        is False
    )
