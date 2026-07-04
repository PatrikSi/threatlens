import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.feed import Feed
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.notification_webhooks import NotificationWebhookRetryInProgressError
from app.schemas.notification import NotificationWebhookTestResponse


def test_admin_can_create_notification_webhooks_by_default(client: TestClient, auth_headers):
    response = client.post(
        "/notifications/webhooks",
        json={
            "name": "Admin webhook",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com/notify",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["admin"],
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Admin webhook"


def test_admin_webhook_update_revalidates_url_safety(client: TestClient, auth_headers, db_session, seed_users):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Existing webhook",
        enabled=True,
        event_type="rss_item_new",
        url_template="https://hooks.example.com/notify",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(webhook)
    db_session.commit()

    response = client.patch(
        f"/notifications/webhooks/{webhook.id}",
        json={
            "name": "Unsafe update",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "http://hooks.example.com/changed",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["admin"],
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "url_template must use https unless ALLOW_PRIVATE_NETWORK_WEBHOOKS is enabled"


def test_user_can_crud_notification_webhooks(client: TestClient, auth_headers, db_session, seed_users):
    admin = seed_users["admin"]
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()

    create_response = client.post(
        "/notifications/webhooks",
        json={
            "name": "Unit42 webhook",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com:443/notify",
            "method": "POST",
            "feed_scope": "selected",
            "feed_ids": [str(feed.id)],
            "query_params": [{"key": "title", "value": "{{ item.title }}"}],
            "headers": [{"key": "X-Feed", "value": "{{ feed.name }}"}],
            "body_mode": "json",
            "body_fields": [{"key": "item.title", "value": "{{ item.title }}"}],
            "timeout_seconds": 10,
        },
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["user_id"] == str(admin.id)
    assert created["feed_ids"] == [str(feed.id)]

    list_response = client.get("/notifications/webhooks", headers=auth_headers["admin"])
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    webhook_id = created["id"]
    update_response = client.patch(
        f"/notifications/webhooks/{webhook_id}",
        json={
            "name": "All feeds webhook",
            "enabled": False,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com/all",
            "method": "PUT",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "raw",
            "body_fields": [],
            "body_template": "title={{ item.title }}",
            "timeout_seconds": 12,
        },
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "All feeds webhook"
    assert updated["enabled"] is False
    assert updated["body_mode"] == "raw"

    webhook = db_session.scalar(select(NotificationWebhook).where(NotificationWebhook.id == uuid.UUID(webhook_id)))
    assert webhook is not None
    assert webhook.user_id == admin.id
    assert webhook.feed_scope == "all"

    delete_response = client.delete(f"/notifications/webhooks/{webhook_id}", headers=auth_headers["admin"])
    assert delete_response.status_code == 204
    assert db_session.scalar(select(NotificationWebhook).where(NotificationWebhook.id == uuid.UUID(webhook_id))) is None


def test_notification_webhook_create_extracts_query_string_into_params(client: TestClient, auth_headers):
    response = client.post(
        "/notifications/webhooks",
        json={
            "name": "Query parser",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com/notify?token=abc123&priority=5",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["admin"],
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["url_template"] == "https://hooks.example.com/notify"
    assert payload["query_params"] == [
        {"key": "token", "value": "abc123"},
        {"key": "priority", "value": "5"},
    ]


def test_notification_webhook_test_endpoint_returns_render_result(client: TestClient, auth_headers, db_session, monkeypatch, seed_users):
    admin = seed_users["admin"]
    feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()

    captured: dict[str, object] = {}

    def _fake_test(db, *, user, payload, sample_item_id, sample_feed_id):
        captured["user_id"] = user.id
        captured["name"] = payload.name
        captured["sample_feed_id"] = sample_feed_id
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=32,
            rendered_url="https://hooks.example.com/test",
            rendered_method="POST",
            rendered_headers=[],
            rendered_query_params=[],
            rendered_body='{"title":"Example"}',
            response_body_preview="",
            error=None,
        )

    monkeypatch.setattr("app.api.routes.notifications.test_notification_webhook", _fake_test)

    response = client.post(
        "/notifications/webhooks/test",
        json={
            "sample_feed_id": str(feed.id),
            "webhook": {
                "name": "CISA test",
                "enabled": True,
                "event_type": "rss_item_new",
                "url_template": "https://hooks.example.com/test",
                "method": "POST",
                "feed_scope": "selected",
                "feed_ids": [str(feed.id)],
                "query_params": [],
                "headers": [],
                "body_mode": "raw",
                "body_fields": [],
                "body_template": "title={{ item.title }}",
                "timeout_seconds": 10,
            },
        },
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status_code"] == 204
    assert captured["user_id"] == admin.id
    assert captured["sample_feed_id"] == feed.id


def test_notification_webhook_test_endpoint_redacts_sensitive_previews(client: TestClient, auth_headers, monkeypatch):
    request_body = '{"signature":"top-secret"}'
    response_body = '{"ok":true,"token":"secret"}'

    def _fake_send(rendered):
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=19,
            rendered_url=f"{rendered.url}?token=abc123",
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=response_body,
            error=None,
        )

    monkeypatch.setattr("app.services.notification_webhook_http.send_rendered_notification_request", _fake_send)

    response = client.post(
        "/notifications/webhooks/test",
        json={
            "webhook": {
                "name": "Redacted test",
                "enabled": True,
                "event_type": "rss_item_new",
                "url_template": "https://hooks.example.com/test?token=abc123",
                "method": "POST",
                "feed_scope": "all",
                "feed_ids": [],
                "query_params": [],
                "headers": [{"key": "Authorization", "value": "Bearer secret-token"}],
                "body_mode": "raw",
                "body_fields": [],
                "body_template": request_body,
                "timeout_seconds": 10,
            },
        },
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rendered_url"] == "https://hooks.example.com/test?token=REDACTED"
    assert any(header["key"] == "Authorization" and header["value"] == "REDACTED" for header in payload["rendered_headers"])
    assert all("secret-token" not in header["value"] for header in payload["rendered_headers"])
    assert payload["rendered_query_params"] == [{"key": "token", "value": "REDACTED"}]
    assert payload["rendered_body"] == f"Stored body withheld ({len(request_body)} chars)"
    assert payload["response_body_preview"] == f"Stored body withheld ({len(response_body)} chars)"


def test_analyst_can_create_notification_webhooks_by_default(client: TestClient, auth_headers):
    response = client.post(
        "/notifications/webhooks",
        json={
            "name": "Analyst webhook",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com:443/notify",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Analyst webhook"


def test_analyst_cannot_create_notification_webhooks_for_public_http_url(client: TestClient, auth_headers):
    response = client.post(
        "/notifications/webhooks",
        json={
            "name": "Analyst webhook",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "http://hooks.example.com/notify",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "url_template must use https unless ALLOW_PRIVATE_NETWORK_WEBHOOKS is enabled"


def test_viewer_cannot_create_notification_webhooks(client: TestClient, auth_headers):
    response = client.post(
        "/notifications/webhooks",
        json={
            "name": "Viewer webhook",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com/notify",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["viewer"],
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


def test_viewer_cannot_test_notification_webhooks(client: TestClient, auth_headers):
    response = client.post(
        "/notifications/webhooks/test",
        json={
            "webhook": {
                "name": "Viewer test",
                "enabled": True,
                "event_type": "rss_item_new",
                "url_template": "https://hooks.example.com/test",
                "method": "POST",
                "feed_scope": "all",
                "feed_ids": [],
                "query_params": [],
                "headers": [],
                "body_mode": "none",
                "body_fields": [],
                "timeout_seconds": 10,
            },
        },
        headers=auth_headers["viewer"],
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


def test_user_can_list_notification_webhook_delivery_history(client: TestClient, auth_headers, db_session, seed_users):
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=viewer.id,
        name="History webhook",
        url_template="https://hooks.example.com/history",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=viewer.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=42,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/history",
        rendered_method="POST",
        rendered_headers_json=[{"key": "Content-Type", "value": "application/json"}],
        rendered_query_params_json=[],
        rendered_body='{"title":"Example"}',
        response_body_preview="server error",
        error="HTTP 500",
        item_title_snapshot="Example item",
        feed_name_snapshot="Example feed",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    response = client.get(f"/notifications/webhooks/{webhook.id}/deliveries?page=1&page_size=10", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["deliveries"][0]["id"] == str(delivery.id)
    assert payload["deliveries"][0]["event_type"] == "rss_item_new"
    assert payload["deliveries"][0]["delivery_kind"] == "live"
    assert payload["deliveries"][0]["delivery_state"] == "failed"
    assert payload["deliveries"][0]["attempt_count"] == 1
    assert payload["deliveries"][0]["item_title"] == "Example item"
    assert payload["deliveries"][0]["response_body_preview"] == "Stored body withheld (12 chars)"


def test_user_can_list_notification_webhook_delivery_history_with_stable_tiebreaker(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=viewer.id,
        name="History webhook",
        url_template="https://hooks.example.com/history",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    attempted_at = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    earlier_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    later_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    db_session.add(webhook)
    db_session.flush()
    db_session.add_all(
        [
            NotificationWebhookDelivery(
                id=earlier_id,
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="failed",
                attempt_count=1,
                success=False,
                status_code=500,
                duration_ms=42,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/history/1",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body='{"title":"Example"}',
                response_body_preview="server error",
                error="HTTP 500",
                item_title_snapshot="Earlier item",
                feed_name_snapshot="Example feed",
                attempted_at=attempted_at,
            ),
            NotificationWebhookDelivery(
                id=later_id,
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="failed",
                attempt_count=1,
                success=False,
                status_code=500,
                duration_ms=42,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/history/2",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body='{"title":"Example"}',
                response_body_preview="server error",
                error="HTTP 500",
                item_title_snapshot="Later item",
                feed_name_snapshot="Example feed",
                attempted_at=attempted_at,
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/notifications/webhooks/{webhook.id}/deliveries?page=1&page_size=10", headers=auth_headers["viewer"])

    assert response.status_code == 200
    payload = response.json()
    assert [entry["id"] for entry in payload["deliveries"]] == [str(later_id), str(earlier_id)]


def test_user_cannot_list_notification_webhook_delivery_history_outside_pagination_bounds(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=viewer.id,
        name="History webhook",
        url_template="https://hooks.example.com/history",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(webhook)
    db_session.commit()

    response = client.get(
        f"/notifications/webhooks/{webhook.id}/deliveries?page=0&page_size=101",
        headers=auth_headers["viewer"],
    )

    assert response.status_code == 422


def test_user_can_retry_notification_webhook_delivery(client: TestClient, auth_headers, db_session, monkeypatch, seed_users):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Retry webhook",
        url_template="https://hooks.example.com/retry",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=admin.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=51,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"Retry"}',
        response_body_preview="HTTP 500",
        error="HTTP 500",
        item_title_snapshot="Retry item",
        feed_name_snapshot="Retry feed",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    def _fake_retry(db, *, webhook, delivery):
        retried = NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=webhook.user_id,
            event_type_snapshot=delivery.event_type_snapshot,
            delivery_kind="retry",
            delivery_state="succeeded",
            attempt_count=1,
            success=True,
            status_code=204,
            duration_ms=12,
            timeout_seconds=delivery.timeout_seconds,
            rendered_url=delivery.rendered_url,
            rendered_method=delivery.rendered_method,
            rendered_headers_json=delivery.rendered_headers_json,
            rendered_query_params_json=delivery.rendered_query_params_json,
            rendered_body=delivery.rendered_body,
            response_body_preview="ok",
            error=None,
            item_title_snapshot=delivery.item_title_snapshot,
            feed_name_snapshot=delivery.feed_name_snapshot,
        )
        db.add(retried)
        db.flush()
        return retried

    monkeypatch.setattr("app.api.routes.notifications.retry_notification_webhook_delivery", _fake_retry)

    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["event_type"] == "rss_item_new"
    assert payload["delivery_kind"] == "retry"
    assert payload["success"] is True
    assert payload["status_code"] == 204
    assert payload["item_title"] == "Retry item"


def test_retry_failed_notification_webhook_delivery_warns_when_followup_enqueue_is_delayed(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
    seed_users,
):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Retry webhook",
        url_template="https://hooks.example.com/retry",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=admin.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=51,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"Retry"}',
        response_body_preview="HTTP 500",
        error="HTTP 500",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    def _fake_retry(db, *, webhook, delivery):
        retried = NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=webhook.user_id,
            event_type_snapshot=delivery.event_type_snapshot,
            delivery_kind="retry",
            delivery_state="failed",
            attempt_count=1,
            success=False,
            status_code=503,
            duration_ms=12,
            timeout_seconds=delivery.timeout_seconds,
            rendered_url=delivery.rendered_url,
            rendered_method=delivery.rendered_method,
            rendered_headers_json=delivery.rendered_headers_json,
            rendered_query_params_json=delivery.rendered_query_params_json,
            rendered_body=delivery.rendered_body,
            response_body_preview="unavailable",
            error="HTTP 503",
        )
        db.add(retried)
        db.flush()
        return retried

    monkeypatch.setattr("app.api.routes.notifications.retry_notification_webhook_delivery", _fake_retry)
    monkeypatch.setattr(
        "app.api.routes.notifications.reserve_webhook_failed_notification_deliveries",
        lambda *_args, **_kwargs: SimpleNamespace(delivery_ids=[uuid.uuid4()]),
    )
    monkeypatch.setattr("app.api.routes.notifications.enqueue_notification_webhook_delivery_processing", lambda _ids: False)

    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["status_code"] == 503
    assert payload["warnings"] == [
        "Webhook-failed notification delivery is reserved but enqueue was delayed; the recovery sweep will retry it."
    ]


def test_user_cannot_retry_notification_webhook_delivery_while_in_progress(client: TestClient, auth_headers, db_session, seed_users):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Retry webhook",
        url_template="https://hooks.example.com/retry",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=admin.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="sending",
        attempt_count=1,
        claimed_at=datetime.now(timezone.utc),
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"Retry"}',
        response_body_preview=None,
        error=None,
        item_title_snapshot="Retry item",
        feed_name_snapshot="Retry feed",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["admin"],
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Webhook delivery is already queued or in progress"


def test_user_retry_notification_webhook_delivery_returns_conflict_when_another_retry_is_in_progress(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
    seed_users,
):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Retry webhook",
        url_template="https://hooks.example.com/retry",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=admin.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=51,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"Retry"}',
        response_body_preview="HTTP 500",
        error="HTTP 500",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    monkeypatch.setattr(
        "app.api.routes.notifications.retry_notification_webhook_delivery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            NotificationWebhookRetryInProgressError("Webhook retry is already queued or in progress")
        ),
    )

    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["admin"],
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Webhook retry is already queued or in progress"


def test_user_retry_notification_webhook_delivery_returns_conflict_for_reusable_in_progress_retry(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
    seed_users,
):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Retry webhook",
        url_template="https://hooks.example.com/retry",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=admin.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=51,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"Retry"}',
        response_body_preview="HTTP 500",
        error="HTTP 500",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    def _fake_retry(db, *, webhook, delivery):
        retried = NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=webhook.user_id,
            event_type_snapshot=delivery.event_type_snapshot,
            source_delivery_id=delivery.id,
            delivery_kind="retry",
            delivery_state="pending",
            attempt_count=0,
            success=False,
            status_code=None,
            duration_ms=None,
            timeout_seconds=delivery.timeout_seconds,
            rendered_url=delivery.rendered_url,
            rendered_method=delivery.rendered_method,
            rendered_headers_json=delivery.rendered_headers_json,
            rendered_query_params_json=delivery.rendered_query_params_json,
            rendered_body=delivery.rendered_body,
            response_body_preview=None,
            error=None,
        )
        db.add(retried)
        db.flush()
        return retried

    monkeypatch.setattr("app.api.routes.notifications.retry_notification_webhook_delivery", _fake_retry)
    monkeypatch.setattr(
        "app.api.routes.notifications.reserve_webhook_failed_notification_deliveries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pending retry should not emit failure follow-up")),
    )

    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Webhook retry is already queued or in progress"


def test_user_cannot_retry_successful_notification_webhook_delivery(client: TestClient, auth_headers, db_session, seed_users):
    admin = seed_users["admin"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Retry webhook",
        url_template="https://hooks.example.com/retry",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=admin.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="succeeded",
        attempt_count=1,
        success=True,
        status_code=204,
        duration_ms=11,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"Retry"}',
        response_body_preview="ok",
        error=None,
        item_title_snapshot="Retry item",
        feed_name_snapshot="Retry feed",
    )
    db_session.add_all([webhook, delivery])
    db_session.commit()

    response = client.post(
        f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Only failed webhook deliveries can be retried"


def test_user_can_fetch_notification_analytics(client: TestClient, auth_headers, db_session, seed_users):
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=viewer.id,
        name="Analytics webhook",
        url_template="https://hooks.example.com/analytics",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all(
        [
            webhook,
            NotificationWebhookDelivery(
                id=uuid.uuid4(),
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="succeeded",
                attempt_count=1,
                success=True,
                status_code=204,
                duration_ms=12,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/analytics",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                response_body_preview="ok",
                error=None,
            ),
            NotificationWebhookDelivery(
                id=uuid.uuid4(),
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="feed_failing",
                delivery_kind="live",
                delivery_state="failed",
                attempt_count=1,
                success=False,
                status_code=500,
                duration_ms=18,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/analytics",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                response_body_preview="HTTP 500",
                error="HTTP 500",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/notifications/analytics", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_deliveries"] == 2
    assert payload["successful_deliveries"] == 1
    assert payload["failed_deliveries"] == 1
    assert payload["failures_last_24h"] == 1
    assert payload["success_rate_pct"] == 50.0
    assert payload["most_failing_webhook"]["webhook_id"] == str(webhook.id)
    assert payload["events"] == [
        {"event_type": "feed_failing", "total_deliveries": 1, "failed_deliveries": 1},
        {"event_type": "rss_item_new", "total_deliveries": 1, "failed_deliveries": 0},
    ]
    assert payload["queue"] == {
        "status": "healthy",
        "ok": True,
        "pending_deliveries": 0,
        "sending_deliveries": 0,
        "stale_sending_deliveries": 0,
        "oldest_pending_age_seconds": None,
        "oldest_sending_age_seconds": None,
        "degraded_after_seconds": 300,
        "stale_after_seconds": 120,
    }


def test_user_cannot_access_another_users_notification_webhook(client: TestClient, auth_headers, db_session, seed_users):
    admin = seed_users["admin"]
    analyst = seed_users["analyst"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="Admin webhook",
        url_template="https://hooks.example.com/admin",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(webhook)
    db_session.commit()

    response = client.patch(
        f"/notifications/webhooks/{webhook.id}",
        json={
            "name": "Hijack attempt",
            "enabled": True,
            "event_type": "rss_item_new",
            "url_template": "https://hooks.example.com/hijack",
            "method": "POST",
            "feed_scope": "all",
            "feed_ids": [],
            "query_params": [],
            "headers": [],
            "body_mode": "none",
            "body_fields": [],
            "timeout_seconds": 10,
        },
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 404

    still_owned = db_session.scalar(select(NotificationWebhook).where(NotificationWebhook.id == webhook.id))
    assert still_owned is not None
    assert still_owned.user_id == admin.id
    another_user = db_session.scalar(select(User).where(User.id == analyst.id))
    assert another_user is not None
