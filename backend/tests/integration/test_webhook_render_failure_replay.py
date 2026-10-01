"""Requests which failed rendering never become sendable through retry/replay."""

import uuid

import pytest

from app.models.article import Article
from app.models.feed import Feed
from app.models.integration import IntegrationDelivery
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.notification import (
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.integration_events import (
    emit_integration_event,
    route_integration_event,
)
from app.services.notification_webhook_storage import build_notification_webhook
from app.services.notification_webhooks import process_notification_webhook_delivery


@pytest.fixture
def failed_render(db_session, seed_users, monkeypatch):
    feed = Feed(name="Oversized source", url=f"https://example.com/{uuid.uuid4()}")
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        title="Oversized evidence",
        url="https://example.com/evidence",
        dedupe_key=str(uuid.uuid4()),
        content_hash="a" * 64,
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Evidence " * 15000,
        )
    )
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="Render failure",
            url_template="https://receiver.example/events",
            body_mode="raw",
            body_template="{{item.full_text}}" * 10,
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"failed-render:{uuid.uuid4()}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    routed = route_integration_event(db_session, event_id=event.id)
    delivery = db_session.get(
        NotificationWebhookDelivery, routed.webhook_delivery_ids[0]
    )
    db_session.commit()
    sent = []

    def send(rendered):
        sent.append(rendered.body)
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=1,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=None,
            error=None,
        )

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        send,
    )
    outcome = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)
    assert outcome.delivery.delivery_state == "failed"
    assert outcome.delivery.error.startswith("render_error:")
    assert not sent
    generic = db_session.get(IntegrationDelivery, delivery.integration_delivery_id)
    from app.services.integration_delivery import mark_integration_delivery_dead_letter

    mark_integration_delivery_dead_letter(db_session, delivery_id=generic.id)
    db_session.commit()
    assert generic.state == "dead_letter"
    monkeypatch.setattr(
        "app.api.routes.integrations.enqueue_integration_delivery_processing",
        lambda *_: True,
    )
    return webhook, delivery, generic, sent


@pytest.mark.parametrize("surface", ["personal_retry", "generic_replay"])
@pytest.mark.parametrize(
    "history", ["original", "overwritten_diagnostic", "legacy_markerless"]
)
def test_render_budget_failure_cannot_be_retried_or_replayed(
    client, auth_headers, db_session, failed_render, surface, history
):
    webhook, delivery, generic, sent = failed_render
    from app.services.webhook_request_state import REQUEST_RENDERED_KEY

    assert generic.payload_json[REQUEST_RENDERED_KEY] is False
    if history == "overwritten_diagnostic":
        delivery.error = "policy_error:Owner temporarily unavailable"
        generic.last_error_code = "webhook_owner_inactive"
    elif history == "legacy_markerless":
        generic.payload_json = {
            key: value
            for key, value in generic.payload_json.items()
            if key != REQUEST_RENDERED_KEY
        }
    db_session.commit()
    if surface == "personal_retry":
        response = client.post(
            f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
            headers=auth_headers["analyst"],
        )
    else:
        response = client.post(
            f"/integrations/deliveries/{generic.id}/replay",
            headers=auth_headers["admin"],
        )
        if response.status_code == 200:
            process_notification_webhook_delivery(
                db_session, delivery_id=uuid.UUID(response.json()["delivery_id"])
            )
    assert response.status_code == 409, (
        f"{response.text}; incorrectly sent bodies: {sent}"
    )
    assert "render" in response.json()["detail"].lower()
    assert not sent


def test_worker_rejects_unrendered_request_when_its_diagnostic_was_cleared(
    db_session, failed_render
):
    _webhook, delivery, generic, sent = failed_render
    delivery.error = None
    delivery.delivery_state = "pending"
    generic.state = "pending"
    generic.last_error_code = None
    db_session.commit()
    outcome = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)
    assert outcome.delivery.delivery_state == "failed"
    assert "no successfully rendered request" in outcome.delivery.error
    assert not sent


def test_typed_payload_ignores_unused_oversized_custom_body(db_session, failed_render):
    webhook, source, _generic, sent = failed_render
    from app.services.notification_webhooks import reserve_notification_webhook_delivery
    from app.models.user import User

    webhook.payload_mode = "automation_v1"
    db_session.flush()
    fresh = reserve_notification_webhook_delivery(
        db_session,
        webhook=webhook,
        user=db_session.get(User, webhook.user_id),
        event_type="rss_item_new",
        item=db_session.get(Item, source.item_id),
        feed=db_session.get(Feed, source.feed_id),
    )
    assert fresh.error is None
    assert not sent


def test_corrected_ordinary_template_can_be_rendered_again_safely(
    client, auth_headers, db_session, failed_render
):
    webhook, source, _generic, sent = failed_render
    from app.models.user import User
    from app.schemas.notification import NotificationWebhookField
    from app.services.notification_webhook_storage import (
        encrypt_notification_text,
        notification_fields_to_storage,
    )
    from app.services.notification_webhooks import reserve_notification_webhook_delivery
    from app.services.webhook_request_state import REQUEST_RENDERED_KEY

    webhook.body_template = encrypt_notification_text("{{item.title}}")
    webhook.headers_json = notification_fields_to_storage(
        [
            NotificationWebhookField(key="Content-Type", value="text/plain"),
            NotificationWebhookField(key="content-type", value="application/json"),
        ]
    )
    db_session.flush()
    failed = reserve_notification_webhook_delivery(
        db_session,
        webhook=webhook,
        user=db_session.get(User, webhook.user_id),
        event_type="rss_item_new",
        item=db_session.get(Item, source.item_id),
        feed=db_session.get(Feed, source.feed_id),
    )
    db_session.commit()
    process_notification_webhook_delivery(db_session, delivery_id=failed.id)
    assert failed.error.startswith("render_error:")
    webhook.headers_json = notification_fields_to_storage([])
    db_session.commit()
    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{failed.id}/retry",
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert sent == ["Oversized evidence"]
    retry = db_session.get(
        NotificationWebhookDelivery, uuid.UUID(response.json()["id"])
    )
    generic = db_session.get(IntegrationDelivery, retry.integration_delivery_id)
    assert generic.payload_json[REQUEST_RENDERED_KEY] is True


def test_editing_frozen_template_does_not_make_its_failed_snapshot_replayable(
    client, auth_headers, db_session, failed_render
):
    webhook, delivery, _generic, sent = failed_render
    from app.services.notification_webhook_storage import encrypt_notification_text

    webhook.body_template = encrypt_notification_text("{{item.title}}")
    db_session.commit()
    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 409, response.text
    assert not sent
