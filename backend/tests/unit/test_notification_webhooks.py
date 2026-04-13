import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import NotificationWebhookField, NotificationWebhookTestResponse, NotificationWebhookWrite
from app.services.notification_webhooks import (
    _read_response_preview,
    _send_rendered_notification_request,
    _send_request_with_redirects,
    RedirectError,
    build_alert_match_context_for_item,
    get_notification_analytics,
    render_notification_request,
    retry_notification_webhook_delivery,
    send_notification_webhook_for_item,
    validate_notification_webhook_payload,
)
from app.tasks.feed_tasks import (
    dispatch_alert_match_notification_webhooks,
    dispatch_feed_failing_notification_webhooks,
    dispatch_new_item_notification_webhooks,
)


def _persist_rows(db_session, *rows):
    db_session.add_all(rows)
    db_session.flush()


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


def test_render_notification_request_redacts_feed_url_template_values():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify",
        method="POST",
        body_mode="json",
        body_fields=[NotificationWebhookField(key="feed.url", value="{{ feed.url }}")],
    )

    rendered = render_notification_request(
        payload,
        user=User(id=uuid.uuid4(), email="viewer@example.com", password_hash="x", role="viewer", is_active=True, is_approved=True),
        feed=Feed(
            id=uuid.uuid4(),
            name="Secure Feed",
            url="https://alice:secret@example.com/feed.xml?token=abc123&source=partner",
            enabled=True,
            fetch_interval_seconds=1800,
        ),
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

    assert rendered.json_body == {
        "feed": {
            "url": "https://example.com/feed.xml?token=REDACTED&source=partner",
        }
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
    assert [(field.key, field.value) for field in rendered.headers] == [("Content-Type", "application/json")]


def test_render_notification_request_rejects_duplicate_headers_case_insensitively():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify",
        method="POST",
        headers=[
            NotificationWebhookField(key="Content-Type", value="application/json"),
            NotificationWebhookField(key="content-type", value="text/plain"),
        ],
        body_mode="none",
    )

    with pytest.raises(ValueError, match="Duplicate header"):
        render_notification_request(
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


def test_render_notification_request_rejects_host_header_override():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify",
        method="POST",
        headers=[NotificationWebhookField(key="Host", value="internal.example.com")],
        body_mode="none",
    )

    with pytest.raises(ValueError, match="Header is not allowed"):
        render_notification_request(
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


def test_send_request_with_redirects_does_not_replay_original_query_params_after_redirect(monkeypatch):
    seen_urls: list[str] = []
    monkeypatch.setattr("app.services.notification_webhooks.ensure_runtime_fetchable_url", lambda *args, **kwargs: None)

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://hooks.example.com/final?server=1"})
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        response = _send_request_with_redirects(
            client,
            method="POST",
            url="https://hooks.example.com/start?orig=1",
            headers={"Content-Type": "application/json"},
            params=[("token", "abc123")],
            json_body={"title": "ThreatLens"},
            form_body=None,
            raw_body=None,
        )

    assert response.status_code == 204
    assert seen_urls == [
        "https://hooks.example.com/start?orig=1&token=abc123",
        "https://hooks.example.com/final?server=1",
    ]


def test_send_request_with_redirects_blocks_cross_origin_redirects(monkeypatch):
    monkeypatch.setattr("app.services.notification_webhooks.ensure_runtime_fetchable_url", lambda *args, **kwargs: None)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://other.example.com/final"})

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(RedirectError, match="Cross-origin redirects are not allowed"):
            _send_request_with_redirects(
                client,
                method="POST",
                url="https://hooks.example.com/start",
                headers={"Content-Type": "application/json"},
                params=[],
                json_body={"title": "ThreatLens"},
                form_body=None,
                raw_body=None,
            )


def test_send_request_with_redirects_allows_same_origin_redirect_with_explicit_default_port(monkeypatch):
    monkeypatch.setattr("app.services.notification_webhooks.ensure_runtime_fetchable_url", lambda *args, **kwargs: None)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://hooks.example.com:443/final"})
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        response = _send_request_with_redirects(
            client,
            method="POST",
            url="https://hooks.example.com/start",
            headers={"Content-Type": "application/json"},
            params=[],
            json_body={"title": "ThreatLens"},
            form_body=None,
            raw_body=None,
        )

    assert response.status_code == 204


def test_notification_webhooks_use_dedicated_private_network_setting(monkeypatch):
    captured: dict[str, object] = {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    monkeypatch.setattr("app.services.notification_webhooks.settings.allow_private_network_fetch", True)
    monkeypatch.setattr("app.services.notification_webhooks.settings.allow_private_network_webhooks", False)
    monkeypatch.setattr(
        "app.services.notification_webhooks.build_safe_http_client",
        lambda *args, **kwargs: captured.setdefault("allow_private_network", kwargs["allow_private_network"]) or _Client(),
    )
    monkeypatch.setattr(
        "app.services.notification_webhooks._send_request_with_redirects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("stop after client setup")),
    )

    result = _send_rendered_notification_request(
        type(
            "Rendered",
            (),
            {
                "timeout_seconds": 10,
                "url": "https://hooks.example.com/notify",
                "method": "POST",
                "headers": [],
                "query_params": [],
                "body": None,
                "headers_dict": {},
                "query_param_pairs": [],
                "json_body": None,
                "form_body": None,
                "raw_body": None,
            },
        )()
    )

    assert result.success is False
    assert captured["allow_private_network"] is False


def test_webhook_redirect_validation_uses_dedicated_private_network_setting(monkeypatch):
    observed: list[bool] = []

    monkeypatch.setattr("app.services.notification_webhooks.settings.allow_private_network_fetch", True)
    monkeypatch.setattr("app.services.notification_webhooks.settings.allow_private_network_webhooks", False)
    monkeypatch.setattr(
        "app.services.notification_webhooks.ensure_runtime_fetchable_url",
        lambda _url, *, allow_private_network=False: observed.append(allow_private_network),
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        response = _send_request_with_redirects(
            client,
            method="POST",
            url="https://hooks.example.com/start",
            headers={},
            params=[],
            json_body=None,
            form_body=None,
            raw_body=None,
        )

    assert response.status_code == 204
    assert observed == [False]


def test_read_response_preview_caps_body_size():
    response = httpx.Response(200, content=b"a" * 5000)

    assert _read_response_preview(response, max_bytes=4000) == "a" * 4000


def test_send_notification_webhook_for_item_records_delivery_history(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
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
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/1",
        title="Threat report",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:history",
        content_hash="b" * 64,
        status="new",
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="History webhook",
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
    _persist_rows(db_session, feed, user)
    _persist_rows(db_session, item, webhook)
    db_session.commit()

    def _fake_send(rendered):
        return NotificationWebhookTestResponse(
            success=True,
            status_code=202,
            duration_ms=18,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview="accepted",
            error=None,
        )

    monkeypatch.setattr("app.services.notification_webhooks._send_rendered_notification_request", _fake_send)

    result = send_notification_webhook_for_item(db_session, webhook=webhook, item=item, feed=feed, user=user)

    assert result.success is True
    delivery = db_session.scalar(select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.webhook_id == webhook.id))
    assert delivery is not None
    assert delivery.event_type_snapshot == "rss_item_new"
    assert delivery.delivery_kind == "live"
    assert delivery.item_id == item.id
    assert delivery.feed_id == feed.id
    assert delivery.item_title_snapshot == item.title
    assert delivery.feed_name_snapshot == feed.name
    assert delivery.status_code == 202
    assert delivery.response_body_preview == "accepted"


def test_retry_notification_webhook_delivery_reuses_saved_rendered_request(db_session, monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Retry Feed",
        url="https://example.com/retry.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="retry-item",
        url="https://example.com/articles/retry-item",
        canonical_url="https://example.com/articles/retry-item",
        title="Threat report",
        dedupe_key="retry-item",
        content_hash="a" * 64,
        status="content_fetched",
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Retry webhook",
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
    original_delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot="rss_item_new",
        item_id=item.id,
        feed_id=feed.id,
        delivery_kind="live",
        success=False,
        status_code=500,
        duration_ms=41,
        timeout_seconds=12,
        rendered_url="https://example.com/hook?token=abc",
        rendered_method="POST",
        rendered_headers_json=[{"key": "Content-Type", "value": "application/json"}],
        rendered_query_params_json=[{"key": "token", "value": "abc"}],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview="server error",
        error="HTTP 500",
        item_title_snapshot="Threat report",
        feed_name_snapshot="Unit42",
    )
    _persist_rows(db_session, user, feed)
    _persist_rows(db_session, item, webhook)
    _persist_rows(db_session, original_delivery)
    db_session.commit()

    captured: dict[str, object] = {}

    def _fake_send(rendered):
        captured["url"] = rendered.url
        captured["query_param_pairs"] = list(rendered.query_param_pairs)
        captured["raw_body"] = rendered.raw_body
        captured["timeout_seconds"] = rendered.timeout_seconds
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=11,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview="ok",
            error=None,
        )

    monkeypatch.setattr("app.services.notification_webhooks._send_rendered_notification_request", _fake_send)

    retried = retry_notification_webhook_delivery(db_session, webhook=webhook, delivery=original_delivery)

    assert captured["url"] == "https://example.com/hook?token=abc"
    assert captured["query_param_pairs"] == []
    assert captured["raw_body"] == b'{"title":"ThreatLens"}'
    assert captured["timeout_seconds"] == 12
    assert retried.delivery_kind == "retry"
    assert retried.item_id == original_delivery.item_id
    assert retried.feed_id == original_delivery.feed_id
    assert retried.item_title_snapshot == "Threat report"
    assert retried.feed_name_snapshot == "Unit42"
    assert retried.success is True


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

    _persist_rows(db_session, feed, other_feed, user, inactive_user)
    _persist_rows(db_session, item, deliver_all, deliver_selected, skip_other_feed, skip_inactive)
    db_session.commit()

    delivered_ids: list[uuid.UUID] = []

    def _send(_db, *, webhook, user, event_type, item, feed, **_kwargs):
        delivered_ids.append(webhook.id)
        assert event_type == "rss_item_new"

        class _Result:
            success = True
            status_code = 204
            error = None

        class _Delivery:
            id = uuid.uuid4()

        class _Attempt:
            result = _Result()
            delivery = _Delivery()

        return _Attempt()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.send_notification_webhook", _send)
    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_new_item_notification_webhooks(str(item.id))

    assert set(delivered_ids) == {deliver_all.id, deliver_selected.id}
    assert result["matched_webhooks"] == 3
    assert result["delivered"] == 2
    assert result["skipped"] == 1


def test_build_alert_match_context_for_item_collects_matching_alerts(db_session):
    user = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Alert Context Feed",
        url="https://example.com/alert-context.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/items/1",
        title="LockBit phishing wave hits finance sector",
        summary="Credential theft activity observed against finance teams.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:alert-context",
        content_hash="c" * 64,
        status="new",
    )
    _persist_rows(db_session, user, feed)
    _persist_rows(
        db_session,
        item,
        AlertInterest(
            id=uuid.uuid4(),
            user_id=user.id,
            name="Ransomware Watch",
            category="malware",
            keywords=["lockbit", "ransomware"],
            enabled=True,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        ),
    )
    _persist_rows(
        db_session,
        AlertInterest(
            id=uuid.uuid4(),
            user_id=user.id,
            name="Credential Theft",
            category="identity",
            keywords=["credential theft", "mfa fatigue"],
            enabled=True,
            created_at=datetime.now(timezone.utc),
        ),
    )
    db_session.commit()

    context = build_alert_match_context_for_item(db_session, user_id=user.id, item=item)

    assert context is not None
    assert context.count == 2
    assert context.primary_name == "Ransomware Watch"
    assert context.names == ["Ransomware Watch", "Credential Theft"]
    assert context.categories == ["malware", "identity"]
    assert context.matched_keywords == ["lockbit", "credential theft"]


def test_dispatch_alert_match_notification_webhooks_only_delivers_for_matching_users(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    matching_user = User(
        id=uuid.uuid4(),
        email="matching@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    non_matching_user = User(
        id=uuid.uuid4(),
        email="other@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/alert-match",
        title="LockBit operators expand phishing campaign",
        summary="Credential theft and LockBit activity observed.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:alert-match-dispatch",
        content_hash="d" * 64,
        status="content_fetched",
    )
    matching_webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=matching_user.id,
        name="Alert webhook",
        event_type="alert_match",
        url_template="https://example.com/match",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    ignored_webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=non_matching_user.id,
        name="Other alert webhook",
        event_type="alert_match",
        url_template="https://example.com/other",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    _persist_rows(db_session, feed, matching_user, non_matching_user)
    _persist_rows(
        db_session,
        item,
        matching_webhook,
        ignored_webhook,
        AlertInterest(
            id=uuid.uuid4(),
            user_id=matching_user.id,
            name="Ransomware Watch",
            category="malware",
            keywords=["lockbit"],
            enabled=True,
        ),
        AlertInterest(
            id=uuid.uuid4(),
            user_id=non_matching_user.id,
            name="Cloud Watch",
            category="cloud",
            keywords=["aws"],
            enabled=True,
        ),
    )
    db_session.commit()

    delivered_ids: list[uuid.UUID] = []

    def _send(_db, *, webhook, user, event_type, feed, item, alert_context=None, **_kwargs):
        delivered_ids.append(webhook.id)
        assert event_type == "alert_match"
        assert alert_context is not None

        class _Result:
            success = True
            status_code = 204
            error = None

        class _Delivery:
            id = uuid.uuid4()

        class _Attempt:
            result = _Result()
            delivery = _Delivery()

        return _Attempt()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.send_notification_webhook", _send)
    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_alert_match_notification_webhooks(str(item.id))

    assert delivered_ids == [matching_webhook.id]
    assert result["matched_webhooks"] == 2
    assert result["delivered"] == 1
    assert result["skipped"] == 1


def test_dispatch_feed_failing_notification_webhooks_respects_recent_cooldown(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Failing feed",
        url="https://example.com/failing.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        error_count=3,
        last_error="http_status:500",
    )
    user = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Feed failure webhook",
        event_type="feed_failing",
        url_template="https://example.com/failing-hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    recent_delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="feed_failing",
        feed_id=feed.id,
        delivery_kind="live",
        success=True,
        status_code=204,
        duration_ms=12,
        timeout_seconds=10,
        rendered_url="https://example.com/failing-hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview="ok",
        error=None,
        feed_name_snapshot=feed.name,
        attempted_at=datetime.now(timezone.utc),
    )
    _persist_rows(db_session, feed, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, recent_delivery)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_feed_failing_notification_webhooks(str(feed.id))

    assert result["matched_webhooks"] == 1
    assert result["delivered"] == 0
    assert result["skipped"] == 1


def test_get_notification_analytics_summarizes_delivery_history(db_session):
    user = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Analytics webhook",
        url_template="https://example.com/analytics",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(
        db_session,
        NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=user.id,
            event_type_snapshot="rss_item_new",
            delivery_kind="live",
            success=True,
            status_code=204,
            duration_ms=10,
            timeout_seconds=10,
            rendered_url="https://example.com/analytics",
            rendered_method="POST",
            rendered_headers_json=[],
            rendered_query_params_json=[],
            rendered_body=None,
            response_body_preview="ok",
            error=None,
            attempted_at=datetime.now(timezone.utc),
        ),
        NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=user.id,
            event_type_snapshot="alert_match",
            delivery_kind="live",
            success=False,
            status_code=500,
            duration_ms=18,
            timeout_seconds=10,
            rendered_url="https://example.com/analytics",
            rendered_method="POST",
            rendered_headers_json=[],
            rendered_query_params_json=[],
            rendered_body=None,
            response_body_preview="HTTP 500",
            error="HTTP 500",
            attempted_at=datetime.now(timezone.utc),
        ),
    )
    db_session.commit()

    analytics = get_notification_analytics(db_session, user_id=user.id)

    assert analytics.total_deliveries == 2
    assert analytics.successful_deliveries == 1
    assert analytics.failed_deliveries == 1
    assert analytics.failures_last_24h == 1
    assert analytics.success_rate_pct == 50.0
    assert analytics.most_failing_webhook is not None
    assert analytics.most_failing_webhook.webhook_id == webhook.id
    assert [(entry.event_type, entry.total_deliveries, entry.failed_deliveries) for entry in analytics.events] == [
        ("alert_match", 1, 1),
        ("rss_item_new", 1, 0),
    ]
