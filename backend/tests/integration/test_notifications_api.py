import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.feed import Feed
from app.models.notification_webhook import NotificationWebhook
from app.models.user import User
from app.schemas.notification import NotificationWebhookTestResponse


def test_user_can_crud_notification_webhooks(client: TestClient, auth_headers, db_session, seed_users):
    viewer = seed_users["viewer"]
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
            "url_template": "https://hooks.example.com/notify",
            "method": "POST",
            "feed_scope": "selected",
            "feed_ids": [str(feed.id)],
            "query_params": [{"key": "title", "value": "{{ item.title }}"}],
            "headers": [{"key": "X-Feed", "value": "{{ feed.name }}"}],
            "body_mode": "json",
            "body_fields": [{"key": "item.title", "value": "{{ item.title }}"}],
            "timeout_seconds": 10,
        },
        headers=auth_headers["viewer"],
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["user_id"] == str(viewer.id)
    assert created["feed_ids"] == [str(feed.id)]

    list_response = client.get("/notifications/webhooks", headers=auth_headers["viewer"])
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
        headers=auth_headers["viewer"],
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "All feeds webhook"
    assert updated["enabled"] is False
    assert updated["body_mode"] == "raw"

    webhook = db_session.scalar(select(NotificationWebhook).where(NotificationWebhook.id == uuid.UUID(webhook_id)))
    assert webhook is not None
    assert webhook.user_id == viewer.id
    assert webhook.feed_scope == "all"

    delete_response = client.delete(f"/notifications/webhooks/{webhook_id}", headers=auth_headers["viewer"])
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
        headers=auth_headers["viewer"],
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["url_template"] == "https://hooks.example.com/notify"
    assert payload["query_params"] == [
        {"key": "token", "value": "abc123"},
        {"key": "priority", "value": "5"},
    ]


def test_notification_webhook_test_endpoint_returns_render_result(client: TestClient, auth_headers, db_session, monkeypatch, seed_users):
    viewer = seed_users["viewer"]
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
        headers=auth_headers["viewer"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status_code"] == 204
    assert captured["user_id"] == viewer.id
    assert captured["sample_feed_id"] == feed.id


def test_user_cannot_access_another_users_notification_webhook(client: TestClient, auth_headers, db_session, seed_users):
    analyst = seed_users["analyst"]
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=analyst.id,
        name="Analyst webhook",
        url_template="https://hooks.example.com/analyst",
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
        headers=auth_headers["viewer"],
    )
    assert response.status_code == 404

    still_owned = db_session.scalar(select(NotificationWebhook).where(NotificationWebhook.id == webhook.id))
    assert still_owned is not None
    assert still_owned.user_id == analyst.id
    another_user = db_session.scalar(select(User).where(User.id == viewer.id))
    assert another_user is not None
