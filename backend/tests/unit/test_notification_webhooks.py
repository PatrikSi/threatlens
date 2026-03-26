import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.user import User
from app.schemas.notification import NotificationWebhookField, NotificationWebhookWrite
from app.services.notification_webhooks import render_notification_request, validate_notification_webhook_payload
from app.tasks.feed_tasks import dispatch_new_item_notification_webhooks


def test_validate_notification_webhook_payload_rejects_unknown_template_variables():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://example.com/hooks/{{ item.title }}/{{ item.unknown }}",
        method="POST",
        body_mode="none",
    )

    try:
        validate_notification_webhook_payload(payload, set())
    except ValueError as exc:
        assert "item.unknown" in str(exc)
    else:
        raise AssertionError("expected payload validation to fail")


def test_notification_webhook_write_extracts_query_params_from_url_template():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify?token=abc123&source={{ feed.name }}",
        method="POST",
        query_params=[NotificationWebhookField(key="priority", value="5")],
        body_mode="none",
    )

    assert payload.url_template == "https://hooks.example.com/notify"
    assert [(field.key, field.value) for field in payload.query_params] == [
        ("priority", "5"),
        ("token", "abc123"),
        ("source", "{{ feed.name }}"),
    ]


def test_render_notification_request_expands_templates_into_json_body():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify/{{ feed.name }}",
        method="POST",
        query_params=[NotificationWebhookField(key="title", value="{{ item.title }}")],
        headers=[NotificationWebhookField(key="X-Feed", value="{{ feed.name }}")],
        body_mode="json",
        body_fields=[
            NotificationWebhookField(key="event.title", value="{{ item.title }}"),
            NotificationWebhookField(key="event.url", value="{{ item.url }}"),
            NotificationWebhookField(key="feed", value="{{ feed.name }}"),
        ],
    )

    rendered = render_notification_request(
        payload,
        user=User(id=uuid.uuid4(), email="viewer@example.com", password_hash="x", role="viewer", is_active=True, is_approved=True),
        feed=Feed(id=uuid.uuid4(), name="Unit42", url="https://example.com/feed.xml", enabled=True, fetch_interval_seconds=1800),
        item=Item(
            id=uuid.uuid4(),
            feed_id=uuid.uuid4(),
            url="https://example.com/articles/1",
            title="Threat report",
            summary="summary",
            published_at=datetime(2026, 3, 25, 9, 15, tzinfo=timezone.utc),
            dedupe_key="dedupe",
            content_hash="a" * 64,
            status="new",
        ),
    )

    assert rendered.url == "https://hooks.example.com/notify/Unit42"
    assert rendered.query_param_pairs == [("title", "Threat report")]
    assert rendered.headers_dict["X-Feed"] == "Unit42"
    assert rendered.json_body == {
        "event": {
            "title": "Threat report",
            "url": "https://example.com/articles/1",
        },
        "feed": "Unit42",
    }


def test_render_notification_request_defaults_raw_json_to_application_json():
    payload = NotificationWebhookWrite(
        name="Gotify",
        url_template="http://192.168.0.191:8093/message",
        method="POST",
        query_params=[NotificationWebhookField(key="token", value="example")],
        body_mode="raw",
        body_template='{\n  "title": "ThreatLens Alert",\n  "message": "Test notification from ThreatLens",\n  "priority": 5\n}',
    )

    rendered = render_notification_request(
        payload,
        user=User(id=uuid.uuid4(), email="viewer@example.com", password_hash="x", role="viewer", is_active=True, is_approved=True),
        feed=Feed(id=uuid.uuid4(), name="Unit42", url="https://example.com/feed.xml", enabled=True, fetch_interval_seconds=1800),
        item=Item(
            id=uuid.uuid4(),
            feed_id=uuid.uuid4(),
            url="https://example.com/articles/1",
            title="Threat report",
            summary="summary",
            published_at=datetime(2026, 3, 25, 9, 15, tzinfo=timezone.utc),
            dedupe_key="dedupe",
            content_hash="a" * 64,
            status="new",
        ),
    )

    assert rendered.headers_dict["Content-Type"] == "application/json"


def test_dispatch_new_item_notification_webhooks_matches_feed_scope_and_active_user(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    other_feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    user = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    inactive_user = User(
        id=uuid.uuid4(),
        email="inactive@example.com",
        password_hash="x",
        role="viewer",
        is_active=False,
        is_approved=True,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/1",
        title="Threat report",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:1",
        content_hash="a" * 64,
        status="new",
    )
    deliver_all = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="All feeds",
        url_template="https://example.com/a",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    deliver_selected = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Selected feed",
        url_template="https://example.com/b",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[str(feed.id)],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    skip_other_feed = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Wrong feed",
        url_template="https://example.com/c",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[str(other_feed.id)],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    skip_inactive = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=inactive_user.id,
        name="Inactive user",
        url_template="https://example.com/d",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )

    db_session.add_all([feed, other_feed, user, inactive_user, item, deliver_all, deliver_selected, skip_other_feed, skip_inactive])
    db_session.commit()

    delivered_ids: list[uuid.UUID] = []

    def _send(_db, *, webhook, item, feed, user):
        delivered_ids.append(webhook.id)

        class _Result:
            success = True
            status_code = 204
            error = None

        return _Result()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.send_notification_webhook_for_item", _send)
    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_new_item_notification_webhooks(str(item.id))

    assert set(delivered_ids) == {deliver_all.id, deliver_selected.id}
    assert result["matched_webhooks"] == 3
    assert result["delivered"] == 2
    assert result["skipped"] == 1
