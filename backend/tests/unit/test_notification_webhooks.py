import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationWebhookField,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.integration_compat import (
    WebhookConfigurationCompatibilityError,
    ensure_webhook_integration,
)
from app.services.integration_events import (
    build_alert_match_snapshot_payload,
    emit_integration_event,
    route_integration_event,
)
from app.services.notification_webhook_http import (
    RedirectError,
    WebhookAmbiguousResponseError,
    notification_delivery_external_io_marker,
    notification_delivery_lease_heartbeat,
    read_response_preview,
    send_rendered_notification_request,
    send_request_with_redirects,
)
from app.services.notification_webhooks import (
    NotificationWebhookRetryInProgressError,
    build_alert_match_context_for_item,
    get_notification_analytics,
    list_recoverable_notification_delivery_ids,
    process_notification_webhook_delivery,
    render_notification_request,
    reserve_notification_webhook_delivery,
    reserve_retryable_notification_webhook_delivery,
    retry_notification_webhook_delivery,
    send_notification_webhook_for_item,
    test_notification_webhook as run_test_notification_webhook,
    validate_notification_webhook_payload_for_actor,
    validate_notification_webhook_payload,
)
from app.services.secret_storage import decrypt_json, decrypt_text
from app.tasks.feed_tasks import (
    dispatch_alert_match_notification_webhooks,
    dispatch_feed_failing_notification_webhooks,
    dispatch_new_item_notification_webhooks,
    dispatch_pending_notification_webhook_deliveries,
    dispatch_webhook_failed_notification_webhooks,
)


def _persist_rows(db_session, *rows):
    for row in rows:
        db_session.add(row)
        db_session.flush()


@pytest.fixture
def stub_smtp_enqueue(monkeypatch):
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_integration_event_routing",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_alert_evaluation_requests",
        lambda *_args, **_kwargs: True,
    )


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


def test_legacy_webhook_repair_refuses_future_integration_schema(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"future-webhook-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Future webhook projection",
        enabled=True,
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
    _persist_rows(db_session, user, webhook)
    instance, _subscription = ensure_webhook_integration(db_session, webhook)
    instance.schema_version = 2
    instance.config_json = {**instance.config_json, "future_option": True}
    db_session.add(instance)
    db_session.commit()

    with pytest.raises(WebhookConfigurationCompatibilityError):
        ensure_webhook_integration(db_session, webhook)

    stored = db_session.get(IntegrationInstance, instance.id)
    assert stored is not None
    assert stored.schema_version == 2
    assert stored.config_json["future_option"] is True


def test_validate_notification_webhook_payload_rejects_public_http_targets(monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_webhooks.settings.allow_private_network_webhooks",
        False,
    )

    payload = NotificationWebhookWrite(
        name="Example",
        url_template="http://hooks.example.com/notify",
        method="POST",
        body_mode="none",
    )

    with pytest.raises(ValueError, match="must use https"):
        validate_notification_webhook_payload(payload, set())


def test_validate_notification_webhook_payload_for_actor_allows_active_operators_by_default():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify",
        method="POST",
        body_mode="none",
    )
    analyst = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    admin = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
        is_approved=True,
    )

    validate_notification_webhook_payload_for_actor(payload, set(), actor_user=analyst)
    validate_notification_webhook_payload_for_actor(payload, set(), actor_user=admin)


def test_validate_notification_webhook_payload_for_actor_rejects_viewers_and_inactive_users():
    payload = NotificationWebhookWrite(
        name="Blocked",
        url_template="https://hooks.example.com/notify",
        method="POST",
        body_mode="none",
    )
    viewer = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    inactive_analyst = User(
        id=uuid.uuid4(),
        email="inactive@example.com",
        password_hash="x",
        role="analyst",
        is_active=False,
        is_approved=True,
    )

    with pytest.raises(ValueError, match="no longer authorized"):
        validate_notification_webhook_payload_for_actor(
            payload, set(), actor_user=viewer
        )
    with pytest.raises(ValueError, match="no longer active and approved"):
        validate_notification_webhook_payload_for_actor(
            payload, set(), actor_user=inactive_analyst
        )


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


def test_list_recoverable_notification_delivery_ids_only_returns_stale_pending_or_sending(
    db_session,
):
    now = datetime(2026, 4, 18, 21, 45, tzinfo=timezone.utc)
    user = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Analytics",
        url_template="https://example.com/hooks",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    stale_pending = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now - timedelta(minutes=10),
        delivery_state="pending",
        attempt_count=0,
    )
    fresh_pending = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now - timedelta(minutes=1),
        delivery_state="pending",
        attempt_count=0,
    )
    overdue_delayed_retry = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="retry",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now - timedelta(minutes=1),
        not_before=now - timedelta(seconds=30),
        delivery_state="pending",
        attempt_count=0,
    )
    future_delayed_retry = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="retry",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now - timedelta(minutes=10),
        not_before=now + timedelta(minutes=5),
        delivery_state="pending",
        attempt_count=0,
    )
    stale_sending = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now - timedelta(minutes=10),
        claimed_at=now - timedelta(minutes=10),
        delivery_state="sending",
        attempt_count=1,
    )
    fresh_sending = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now - timedelta(minutes=1),
        claimed_at=now - timedelta(minutes=1),
        delivery_state="sending",
        attempt_count=1,
    )

    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(
        db_session,
        stale_pending,
        fresh_pending,
        overdue_delayed_retry,
        future_delayed_retry,
        stale_sending,
        fresh_sending,
    )
    db_session.commit()

    recoverable = list_recoverable_notification_delivery_ids(db_session, now=now)

    assert recoverable == [stale_pending.id, stale_sending.id, overdue_delayed_retry.id]


def test_process_notification_webhook_delivery_marks_unclaimed_attempts(db_session):
    now = datetime.now(timezone.utc)
    user = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Analytics",
        url_template="https://example.com/hooks",
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
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now,
        claimed_at=now,
        delivery_state="sending",
        attempt_count=1,
    )

    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, delivery)
    db_session.commit()

    attempt = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)

    assert attempt.claimed is False
    assert attempt.delivery.id == delivery.id
    assert attempt.delivery.delivery_state == "sending"


def test_process_notification_webhook_delivery_does_not_claim_retry_before_not_before(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    user = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Analytics",
        url_template="https://example.com/hooks",
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
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="retry",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://example.com/hooks",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=now,
        not_before=now + timedelta(minutes=5),
        delivery_state="pending",
        attempt_count=0,
    )

    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, delivery)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delayed retry should not send early")
        ),
    )

    attempt = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)

    assert attempt.claimed is False
    assert attempt.delivery.id == delivery.id
    assert attempt.delivery.delivery_state == "pending"
    assert attempt.delivery.not_before == delivery.not_before


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
        user=User(
            id=uuid.uuid4(),
            email="viewer@example.com",
            password_hash="x",
            role="viewer",
            is_active=True,
            is_approved=True,
        ),
        feed=Feed(
            id=uuid.uuid4(),
            name="Unit42",
            url="https://example.com/feed.xml",
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
        user=User(
            id=uuid.uuid4(),
            email="viewer@example.com",
            password_hash="x",
            role="viewer",
            is_active=True,
            is_approved=True,
        ),
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


def test_test_notification_webhook_redacts_sensitive_request_and_response_previews(
    db_session, monkeypatch
):
    request_body = '{"signature":"top-secret"}'
    response_body = '{"ok":true,"token":"secret"}'
    user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
        is_approved=True,
    )
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify?token=abc123",
        method="POST",
        headers=[
            NotificationWebhookField(key="Authorization", value="Bearer secret-token")
        ],
        body_mode="raw",
        body_template=request_body,
    )
    db_session.add(user)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda rendered: NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=17,
            rendered_url=f"{rendered.url}?token=abc123",
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=response_body,
            error=None,
        ),
    )

    result = run_test_notification_webhook(db_session, user=user, payload=payload)

    assert result.success is True
    assert result.rendered_url == "https://hooks.example.com/notify?token=REDACTED"
    assert any(
        header.key == "Authorization" and header.value == "REDACTED"
        for header in result.rendered_headers
    )
    assert all("secret-token" not in header.value for header in result.rendered_headers)
    assert result.rendered_query_params == [
        NotificationWebhookField(key="token", value="REDACTED")
    ]
    assert result.rendered_body == f"Stored body withheld ({len(request_body)} chars)"
    assert (
        result.response_body_preview
        == f"Stored body withheld ({len(response_body)} chars)"
    )


def test_send_rendered_notification_request_reads_preview_before_client_closes(
    monkeypatch,
):
    client_closed = {"value": False}
    request = httpx.Request("POST", "https://hooks.example.com/notify")

    class _Client:
        def __enter__(self):
            client_closed["value"] = False
            return object()

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            client_closed["value"] = True
            return False

    class _Stream(httpx.SyncByteStream):
        def __iter__(self):
            if client_closed["value"]:
                raise httpx.ReadError("client closed before preview read")
            yield b'{"ok":true}'

    response = httpx.Response(204, request=request, stream=_Stream())

    monkeypatch.setattr(
        "app.services.notification_webhook_http.build_safe_http_client",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_request_with_redirects",
        lambda *_args, **_kwargs: response,
    )

    lease_seconds: list[int] = []
    with notification_delivery_lease_heartbeat(lease_seconds.append):
        result = send_rendered_notification_request(
            SimpleNamespace(
                timeout_seconds=10,
                url="https://hooks.example.com/notify",
                method="POST",
                headers=[],
                query_params=[],
                body=None,
                headers_dict={},
                query_param_pairs=[],
                json_body=None,
                form_body=None,
                raw_body=None,
            )
        )

    assert result.success is True
    assert result.status_code == 204
    assert result.response_body_preview == '{"ok":true}'
    assert client_closed["value"] is True
    assert lease_seconds == [35, 35]


def test_webhook_timeout_after_request_starts_is_not_returned_as_retryable_result(
    monkeypatch,
):
    class _TimeoutClient:
        timeout = SimpleNamespace(read=10)

        def build_request(self, method, url, **kwargs):
            return httpx.Request(method, url, **kwargs)

        def send(self, request, **_kwargs):
            raise httpx.ReadTimeout("response timed out", request=request)

    @contextmanager
    def _fake_client(**_kwargs):
        yield _TimeoutClient()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.build_safe_http_client",
        _fake_client,
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.ensure_runtime_fetchable_url",
        lambda *_args, **_kwargs: None,
    )
    marker_calls: list[bool] = []

    with (
        notification_delivery_external_io_marker(lambda: marker_calls.append(True)),
        pytest.raises(WebhookAmbiguousResponseError, match="after it began"),
    ):
        send_rendered_notification_request(
            SimpleNamespace(
                timeout_seconds=10,
                url="https://hooks.example.com/notify",
                method="POST",
                headers=[],
                query_params=[],
                body=None,
                headers_dict={},
                query_param_pairs=[],
                json_body=None,
                form_body=None,
                raw_body=None,
            )
        )

    assert marker_calls == [True]


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
        user=User(
            id=uuid.uuid4(),
            email="viewer@example.com",
            password_hash="x",
            role="viewer",
            is_active=True,
            is_approved=True,
        ),
        feed=Feed(
            id=uuid.uuid4(),
            name="Unit42",
            url="https://example.com/feed.xml",
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

    assert rendered.headers_dict["Content-Type"] == "application/json"
    assert rendered.headers_dict["X-ThreatLens-Delivery-ID"]
    assert ("Content-Type", "application/json") in [
        (field.key, field.value) for field in rendered.headers
    ]


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
            user=User(
                id=uuid.uuid4(),
                email="viewer@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            ),
            feed=Feed(
                id=uuid.uuid4(),
                name="Unit42",
                url="https://example.com/feed.xml",
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
            user=User(
                id=uuid.uuid4(),
                email="viewer@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            ),
            feed=Feed(
                id=uuid.uuid4(),
                name="Unit42",
                url="https://example.com/feed.xml",
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


def test_send_request_with_redirects_does_not_replay_original_query_params_after_redirect(
    monkeypatch,
):
    seen_urls: list[str] = []
    marker_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.notification_webhook_http.ensure_runtime_fetchable_url",
        lambda *args, **kwargs: None,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"Location": "https://hooks.example.com/final?server=1"}
            )
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(_handler)
    with notification_delivery_external_io_marker(lambda: marker_calls.append(True)):
        with httpx.Client(transport=transport) as client:
            response = send_request_with_redirects(
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
    assert marker_calls == [True]


def test_send_request_with_redirects_blocks_cross_origin_redirects(monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_webhook_http.ensure_runtime_fetchable_url",
        lambda *args, **kwargs: None,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "https://other.example.com/final"}
        )

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(
            RedirectError, match="Cross-origin redirects are not allowed"
        ):
            send_request_with_redirects(
                client,
                method="POST",
                url="https://hooks.example.com/start",
                headers={"Content-Type": "application/json"},
                params=[],
                json_body={"title": "ThreatLens"},
                form_body=None,
                raw_body=None,
            )


def test_send_request_with_redirects_allows_same_origin_redirect_with_explicit_default_port(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.notification_webhook_http.ensure_runtime_fetchable_url",
        lambda *args, **kwargs: None,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"Location": "https://hooks.example.com:443/final"}
            )
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        response = send_request_with_redirects(
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

    monkeypatch.setattr(
        "app.services.notification_webhook_http.settings.allow_private_network_fetch",
        True,
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.settings.allow_private_network_webhooks",
        False,
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.build_safe_http_client",
        lambda *args, **kwargs: (
            captured.setdefault(
                "allow_private_network", kwargs["allow_private_network"]
            )
            or _Client()
        ),
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_request_with_redirects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("stop after client setup")
        ),
    )

    result = send_rendered_notification_request(
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


def test_webhook_redirect_validation_uses_dedicated_private_network_setting(
    monkeypatch,
):
    observed: list[bool] = []

    monkeypatch.setattr(
        "app.services.notification_webhook_http.settings.allow_private_network_fetch",
        True,
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.settings.allow_private_network_webhooks",
        False,
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_http.ensure_runtime_fetchable_url",
        lambda _url, *, allow_private_network=False: observed.append(
            allow_private_network
        ),
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        response = send_request_with_redirects(
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

    assert read_response_preview(response, max_bytes=4000) == "a" * 4000


def test_send_notification_webhook_for_item_records_delivery_history(
    db_session, monkeypatch
):
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
        role="admin",
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
        captured_headers = {field.key: field.value for field in rendered.headers}
        assert "X-ThreatLens-Delivery-ID" in captured_headers
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

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _fake_send,
    )

    result = send_notification_webhook_for_item(
        db_session, webhook=webhook, item=item, feed=feed, user=user
    )

    assert result.success is True
    delivery = db_session.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.webhook_id == webhook.id
        )
    )
    assert delivery is not None
    assert delivery.event_type_snapshot == "rss_item_new"
    assert delivery.delivery_kind == "live"
    assert delivery.delivery_state == "succeeded"
    assert delivery.attempt_count == 1
    assert delivery.item_id == item.id
    assert delivery.feed_id == feed.id
    assert delivery.item_title_snapshot == item.title
    assert delivery.feed_name_snapshot == feed.name
    assert delivery.status_code == 202
    assert decrypt_text(delivery.response_body_preview) == "accepted"
    rendered_headers = decrypt_json(delivery.rendered_headers_json)
    assert any(
        header["key"] == "X-ThreatLens-Delivery-ID"
        for header in (rendered_headers or [])
    )


def test_process_notification_webhook_delivery_revalidates_runtime_url_safety(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="hashed",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Restricted webhook",
        url_template="http://hooks.example.com/hook",
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
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="http://hooks.example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        delivery_state="pending",
        attempt_count=0,
        attempted_at=datetime.now(timezone.utc),
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, delivery)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delivery should be blocked before send")
        ),
    )

    attempt = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)

    assert attempt.claimed is True
    assert attempt.result.success is False
    assert (
        attempt.result.error
        == "url_template must use https unless ALLOW_PRIVATE_NETWORK_WEBHOOKS is enabled"
    )
    assert attempt.delivery.delivery_state == "failed"
    assert attempt.delivery.status_code is None


def test_process_notification_webhook_delivery_fails_closed_for_offboarded_owner(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="hashed",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Restricted webhook",
        url_template="https://hooks.example.com/hook",
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
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        delivery_state="pending",
        attempt_count=0,
        attempted_at=datetime.now(timezone.utc),
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook, delivery)
    user.is_active = False
    db_session.add(user)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delivery should be blocked before send")
        ),
    )

    attempt = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)

    assert attempt.claimed is True
    assert attempt.result.success is False
    assert (
        attempt.result.error
        == "Webhook owner is no longer active and approved for outbound delivery"
    )
    assert attempt.delivery.delivery_state == "failed"
    assert attempt.delivery.status_code is None
    assert (
        attempt.delivery.error
        == "policy_error:Webhook owner is no longer active and approved for outbound delivery"
    )


def test_process_notification_webhook_delivery_fails_closed_for_downgraded_owner_role(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash="hashed",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Restricted webhook",
        url_template="https://hooks.example.com/hook",
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
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        delivery_state="pending",
        attempt_count=0,
        attempted_at=datetime.now(timezone.utc),
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook, delivery)
    user.role = "viewer"
    db_session.add(user)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delivery should be blocked before send")
        ),
    )

    attempt = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)

    assert attempt.claimed is True
    assert attempt.result.success is False
    assert (
        attempt.result.error
        == "Webhook owner is no longer authorized to manage outbound deliveries"
    )
    assert attempt.delivery.delivery_state == "failed"
    assert attempt.delivery.status_code is None
    assert (
        attempt.delivery.error
        == "policy_error:Webhook owner is no longer authorized to manage outbound deliveries"
    )


def test_presend_render_failures_stay_pending_until_processed(db_session, monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
        is_approved=True,
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
        name="Broken webhook",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[
            {"key": "Content-Type", "value": "application/json"},
            {"key": "content-type", "value": "text/plain"},
        ],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    _persist_rows(db_session, user, feed)
    _persist_rows(db_session, item, webhook)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pre-send render failure should not reach sender")
        ),
    )

    reserved = reserve_notification_webhook_delivery(
        db_session,
        webhook=webhook,
        user=user,
        event_type="rss_item_new",
        item=item,
        feed=feed,
    )
    assert reserved.delivery_state == "pending"
    assert reserved.attempt_count == 0
    assert reserved.error == "render_error:Duplicate header: content-type"

    attempt = process_notification_webhook_delivery(db_session, delivery_id=reserved.id)

    assert attempt.result.success is False
    assert attempt.result.error == "Duplicate header: content-type"
    assert attempt.delivery.delivery_state == "failed"
    assert attempt.delivery.attempt_count == 1
    assert attempt.delivery.error == "render_error:Duplicate header: content-type"


def test_presend_and_policy_failures_are_not_auto_retryable(db_session):
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
        is_approved=True,
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
    render_failure = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=12,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error="render_error:Duplicate header: content-type",
    )
    policy_failure = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=12,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error="policy_error:Webhook owner is no longer active and approved for outbound delivery",
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook, render_failure, policy_failure)
    db_session.commit()

    assert (
        reserve_retryable_notification_webhook_delivery(
            db_session, webhook=webhook, delivery=render_failure
        )
        is None
    )
    assert (
        reserve_retryable_notification_webhook_delivery(
            db_session, webhook=webhook, delivery=policy_failure
        )
        is None
    )


def test_process_notification_webhook_delivery_preserves_original_request_snapshot(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
        is_approved=True,
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
    delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=12,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[{"key": "Content-Type", "value": "application/json"}],
        rendered_query_params_json=[{"key": "token", "value": "abc"}],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview=None,
        error=None,
        delivery_state="pending",
        attempt_count=0,
        attempted_at=now,
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, delivery)
    db_session.commit()

    def _fake_send(rendered):
        assert rendered.url == "https://example.com/hook"
        assert rendered.query_param_pairs == [("token", "abc")]
        return NotificationWebhookTestResponse(
            success=False,
            status_code=500,
            duration_ms=11,
            rendered_url="https://example.com/final?server=1",
            rendered_method="GET",
            rendered_headers=[NotificationWebhookField(key="X-Redirected", value="1")],
            rendered_query_params=[],
            rendered_body=None,
            response_body_preview="server error",
            error="HTTP 500",
        )

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _fake_send,
    )

    attempt = process_notification_webhook_delivery(db_session, delivery_id=delivery.id)

    assert attempt.result.success is False
    assert decrypt_text(attempt.delivery.rendered_url) == "https://example.com/hook"
    assert decrypt_json(attempt.delivery.rendered_query_params_json) == [
        {"key": "token", "value": "abc"}
    ]
    assert decrypt_text(attempt.delivery.rendered_body) == '{"title":"ThreatLens"}'
    assert attempt.delivery.status_code == 500
    assert decrypt_text(attempt.delivery.response_body_preview) == "server error"


def test_retry_notification_webhook_delivery_rerenders_current_webhook_when_context_available(
    db_session, monkeypatch
):
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
        method="PATCH",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[{"key": "token", "value": "fresh"}],
        headers_json=[{"key": "X-Retry", "value": "{{ item.title }}"}],
        body_mode="raw",
        body_fields_json=[],
        body_template='{"title":"{{ item.title }}","mode":"fresh"}',
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
        delivery_state="failed",
        attempt_count=1,
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
        captured["rendered_headers"] = list(rendered.headers)
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

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _fake_send,
    )

    retried = retry_notification_webhook_delivery(
        db_session, webhook=webhook, delivery=original_delivery
    )

    assert captured["url"] == "https://example.com/hook"
    assert captured["query_param_pairs"] == [("token", "fresh")]
    assert captured["raw_body"] == b'{"title":"Threat report","mode":"fresh"}'
    assert captured["timeout_seconds"] == 10
    assert retried.delivery_kind == "retry"
    assert retried.delivery_state == "succeeded"
    assert retried.attempt_count == 1
    assert retried.item_id == original_delivery.item_id
    assert retried.feed_id == original_delivery.feed_id
    assert retried.item_title_snapshot == "Threat report"
    assert retried.feed_name_snapshot == "Unit42"
    assert retried.success is True
    assert retried.rendered_method == "PATCH"
    assert any(
        header.key == "X-ThreatLens-Delivery-ID"
        for header in captured["rendered_headers"]
    )
    assert any(
        header.key == "X-ThreatLens-Delivery-ID" and header.value == str(retried.id)
        for header in captured["rendered_headers"]
    )
    assert any(
        header.key == "X-Retry" and header.value == "Threat report"
        for header in captured["rendered_headers"]
    )
    assert any(
        header.key == "X-ThreatLens-Source-Delivery-ID"
        and header.value == str(original_delivery.id)
        for header in captured["rendered_headers"]
    )


def test_retry_notification_webhook_delivery_falls_back_to_saved_request_when_context_is_missing(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Retry webhook",
        url_template="https://example.com/current",
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
        item_id=None,
        feed_id=None,
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=41,
        timeout_seconds=12,
        rendered_url="https://example.com/historical?token=abc",
        rendered_method="POST",
        rendered_headers_json=[
            {"key": "Content-Type", "value": "application/json"},
            {"key": "X-ThreatLens-Delivery-ID", "value": "stale-delivery-id"},
        ],
        rendered_query_params_json=[{"key": "token", "value": "abc"}],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview="server error",
        error="HTTP 500",
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook, original_delivery)
    db_session.commit()

    captured: dict[str, object] = {}

    def _fake_send(rendered):
        captured["url"] = rendered.url
        captured["query_param_pairs"] = list(rendered.query_param_pairs)
        captured["raw_body"] = rendered.raw_body
        captured["rendered_headers"] = list(rendered.headers)
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

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _fake_send,
    )

    retried = retry_notification_webhook_delivery(
        db_session, webhook=webhook, delivery=original_delivery
    )

    assert captured["url"] == "https://example.com/historical"
    assert captured["query_param_pairs"] == [("token", "abc")]
    assert captured["raw_body"] == b'{"title":"ThreatLens"}'
    assert retried.delivery_kind == "retry"
    assert retried.delivery_state == "succeeded"
    assert any(
        header.key == "X-ThreatLens-Delivery-ID" and header.value == str(retried.id)
        for header in captured["rendered_headers"]
    )
    assert any(
        header.key == "X-ThreatLens-Source-Delivery-ID"
        and header.value == str(original_delivery.id)
        for header in captured["rendered_headers"]
    )


def test_retry_notification_webhook_delivery_reuses_existing_successful_retry(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
        is_approved=True,
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
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=503,
        duration_ms=41,
        timeout_seconds=12,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview="server error",
        error="HTTP 503",
    )
    successful_retry = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot="rss_item_new",
        source_delivery_id=original_delivery.id,
        delivery_kind="retry",
        delivery_state="succeeded",
        attempt_count=1,
        success=True,
        status_code=204,
        duration_ms=15,
        timeout_seconds=12,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview="ok",
        error=None,
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook, original_delivery, successful_retry)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        lambda _rendered: (_ for _ in ()).throw(
            AssertionError("existing retry should be reused")
        ),
    )

    retried = retry_notification_webhook_delivery(
        db_session, webhook=webhook, delivery=original_delivery
    )

    assert retried.id == successful_retry.id
    assert retried.delivery_state == "succeeded"


def test_retry_notification_webhook_delivery_raises_when_retry_lock_is_busy_without_reusable_candidate(
    db_session,
    monkeypatch,
):
    user = User(
        id=uuid.uuid4(),
        email="notify@example.com",
        password_hash="hashed",
        role="admin",
        is_active=True,
        is_approved=True,
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
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=503,
        duration_ms=41,
        timeout_seconds=12,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body='{"title":"ThreatLens"}',
        response_body_preview="server error",
        error="HTTP 503",
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook, original_delivery)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhooks.try_acquire_notification_delivery_lock",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(
        NotificationWebhookRetryInProgressError, match="already queued or in progress"
    ):
        retry_notification_webhook_delivery(
            db_session, webhook=webhook, delivery=original_delivery
        )


def test_dispatch_new_item_notification_webhooks_matches_feed_scope_and_active_user(
    db_session,
    monkeypatch,
    stub_smtp_enqueue,
):
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
        role="admin",
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
    _persist_rows(
        db_session, item, deliver_all, deliver_selected, skip_other_feed, skip_inactive
    )
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)

    result = dispatch_new_item_notification_webhooks(str(item.id))
    routed = route_integration_event(
        db_session,
        event_id=uuid.UUID(result["integration_event_id"]),
    )
    webhook_deliveries = db_session.scalars(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.integration_delivery_id.in_(
                routed.integration_delivery_ids
            )
        )
    ).all()

    assert result["status"] == "ok"
    assert result["delivery_status"] == "queued"
    assert result["smtp_enqueue_failed"] is False
    assert {delivery.webhook_id for delivery in webhook_deliveries} == {
        deliver_all.id,
        deliver_selected.id,
    }


def test_dispatch_new_item_notification_webhooks_skips_duplicate_successful_delivery(
    db_session,
    monkeypatch,
    stub_smtp_enqueue,
):
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
        dedupe_key="dedupe:item:1",
        content_hash="a" * 64,
        status="new",
    )
    webhook = NotificationWebhook(
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
    prior_delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        item_id=item.id,
        feed_id=feed.id,
        item_title_snapshot=item.title,
        feed_name_snapshot=feed.name,
        delivery_kind="live",
        success=True,
        status_code=204,
        duration_ms=9,
        timeout_seconds=10,
        rendered_url="https://example.com/a",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview="ok",
        error=None,
        attempted_at=datetime.now(timezone.utc),
        delivery_state="succeeded",
        attempt_count=1,
    )

    _persist_rows(db_session, feed, user)
    _persist_rows(db_session, item, webhook, prior_delivery)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr(
        "app.services.notification_webhooks.reserve_notification_webhook_delivery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate rss_item_new delivery should have been skipped")
        ),
    )
    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)

    result = dispatch_new_item_notification_webhooks(str(item.id))
    routed = route_integration_event(
        db_session,
        event_id=uuid.UUID(result["integration_event_id"]),
    )

    assert result["status"] == "ok"
    assert result["delivery_status"] == "queued"
    assert routed.status == "routed"
    assert db_session.query(NotificationWebhookDelivery).count() == 1


def test_dispatch_new_item_notification_webhooks_stages_without_synchronous_io(
    db_session,
    monkeypatch,
    stub_smtp_enqueue,
):
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
        dedupe_key="dedupe:item:lock-contention",
        content_hash="a" * 64,
        status="new",
    )
    webhook = NotificationWebhook(
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
    _persist_rows(db_session, feed, user)
    _persist_rows(db_session, item, webhook)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)
    result = dispatch_new_item_notification_webhooks(str(item.id))
    routed = route_integration_event(
        db_session,
        event_id=uuid.UUID(result["integration_event_id"]),
    )
    delivery = db_session.scalar(select(NotificationWebhookDelivery))

    assert result["status"] == "ok"
    assert result["delivery_status"] == "queued"
    assert routed.status == "routed"
    assert delivery is not None
    assert delivery.delivery_state == "pending"
    assert delivery.attempt_count == 0


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


def test_dispatch_alert_match_notification_webhooks_queues_matching_owner_snapshot(
    db_session,
    monkeypatch,
    stub_smtp_enqueue,
):
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
        matching_webhook,
        ignored_webhook,
        AlertInterest(
            id=uuid.uuid4(),
            user_id=matching_user.id,
            name="Ransomware Watch",
            category="malware",
            keywords=["lockbit"],
            enabled=True,
            durable_since=datetime.now(timezone.utc) - timedelta(minutes=1),
        ),
        AlertInterest(
            id=uuid.uuid4(),
            user_id=non_matching_user.id,
            name="Cloud Watch",
            category="cloud",
            keywords=["aws"],
            enabled=True,
            durable_since=datetime.now(timezone.utc) - timedelta(minutes=1),
        ),
    )
    db_session.commit()
    _persist_rows(db_session, item)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)

    result = dispatch_alert_match_notification_webhooks(str(item.id))
    evaluation = db_session.get(
        AlertEvaluationRequest,
        uuid.UUID(result["evaluation_request_id"]),
    )
    assert evaluation is not None
    match_owner_ids = set(
        db_session.scalars(
            select(AlertEvaluationMatch.owner_user_id).where(
                AlertEvaluationMatch.request_id == evaluation.id
            )
        ).all()
    )

    assert result["status"] == "ok"
    assert result["delivery_status"] == "queued"
    assert evaluation.notify is True
    assert match_owner_ids == {matching_user.id}
    assert db_session.scalar(select(IntegrationEvent.id)) is None


def test_snapshot_alert_routing_adopts_legacy_unscoped_delivery(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="legacy-alert-owner@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Legacy alert feed",
        url="https://example.com/legacy-alert.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/legacy-alert",
        title="LockBit activity",
        summary="LockBit infrastructure changed.",
        published_at=datetime.now(timezone.utc),
        dedupe_key=f"legacy-alert:{uuid.uuid4()}",
        content_hash="f" * 64,
        status="content_fetched",
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Legacy alert webhook",
        event_type="alert_match",
        url_template="https://example.com/legacy-alert-hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    interest = AlertInterest(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Ransomware watch",
        category="malware",
        keywords=["lockbit"],
        enabled=True,
        durable_since=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    _persist_rows(db_session, user, feed, item, webhook, interest)
    context = build_alert_match_context_for_item(
        db_session,
        user_id=user.id,
        item=item,
    )
    assert context is not None
    payload = build_alert_match_snapshot_payload(
        item=item,
        feed=feed,
        contexts_by_owner={user.id: context},
        occurrence_ids=[],
        occurrence_count=context.count,
        occurrence_ids_truncated=True,
        evaluation_request_id=uuid.uuid4(),
        owner_user_id=user.id,
    )
    event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"legacy-alert-event:{item.id}",
        payload=payload,
        schema_version=3,
        actor_user_id=user.id,
    )
    legacy = reserve_notification_webhook_delivery(
        db_session,
        webhook=webhook,
        user=user,
        event_type="alert_match",
        feed=feed,
        item=item,
        alert_context=context,
    )
    assert legacy.integration_delivery_id is not None
    generic = db_session.get(IntegrationDelivery, legacy.integration_delivery_id)
    assert generic is not None
    legacy.delivery_state = "succeeded"
    legacy.success = True
    legacy.status_code = 204
    legacy.attempt_count = 1
    generic.state = "succeeded"
    generic.attempt_count = 1
    generic.completed_at = datetime.now(timezone.utc)
    db_session.add_all([legacy, generic])
    db_session.commit()

    routed = route_integration_event(db_session, event_id=event.id)

    db_session.refresh(legacy)
    db_session.refresh(generic)
    assert routed.status == "routed"
    assert db_session.query(NotificationWebhookDelivery).count() == 1
    assert db_session.query(IntegrationDelivery).count() == 1
    assert legacy.scope_key == f"alert_event:{event.id}"
    assert generic.event_id == event.id
    assert generic.state == "succeeded"
    assert generic.idempotency_key.startswith(f"event:{event.id}:subscription:")

    later_payload = build_alert_match_snapshot_payload(
        item=item,
        feed=feed,
        contexts_by_owner={user.id: context},
        occurrence_ids=[],
        occurrence_count=context.count,
        occurrence_ids_truncated=True,
        evaluation_request_id=uuid.uuid4(),
        owner_user_id=user.id,
    )
    later_event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"later-alert-event:{item.id}",
        payload=later_payload,
        schema_version=3,
        actor_user_id=user.id,
    )
    later_event.created_at = legacy.attempted_at + timedelta(minutes=10)
    db_session.add(later_event)
    db_session.flush()

    later_routed = route_integration_event(db_session, event_id=later_event.id)

    later_generic = db_session.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == later_event.id,
            IntegrationDelivery.connector_type == "webhook",
        )
    )
    assert later_routed.status == "routed"
    assert later_generic is not None
    assert later_generic.id != generic.id
    assert db_session.query(NotificationWebhookDelivery).count() == 2
    assert db_session.query(IntegrationDelivery).count() == 2

    contention_event = emit_integration_event(
        db_session,
        event_type="alert_match",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"contended-alert-event:{item.id}",
        payload=build_alert_match_snapshot_payload(
            item=item,
            feed=feed,
            contexts_by_owner={user.id: context},
            occurrence_ids=[],
            occurrence_count=context.count,
            occurrence_ids_truncated=True,
            evaluation_request_id=uuid.uuid4(),
            owner_user_id=user.id,
        ),
        schema_version=3,
        actor_user_id=user.id,
    )
    lock_results = iter((True, False))
    monkeypatch.setattr(
        "app.services.integration_connectors.webhook.try_acquire_notification_delivery_lock",
        lambda *_args, **_kwargs: next(lock_results),
    )

    contention_result = route_integration_event(
        db_session,
        event_id=contention_event.id,
    )

    assert contention_result.status == "failed"
    assert "rolling-upgrade compatibility lock" in (contention_event.last_error or "")
    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == contention_event.id
            )
        )
        is None
    )


def test_dispatch_feed_failing_notification_webhooks_respects_recent_cooldown(
    db_session,
    monkeypatch,
    stub_smtp_enqueue,
):
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
        delivery_state="succeeded",
        attempt_count=1,
    )
    _persist_rows(db_session, feed, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, recent_delivery)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)

    result = dispatch_feed_failing_notification_webhooks(str(feed.id))
    routed = route_integration_event(
        db_session,
        event_id=uuid.UUID(result["integration_event_id"]),
    )

    assert result["status"] == "ok"
    assert result["delivery_status"] == "queued"
    assert routed.status == "routed"
    assert db_session.query(NotificationWebhookDelivery).count() == 1


def test_dispatch_webhook_failed_notification_webhooks_skips_duplicate_successful_source_delivery(
    db_session, monkeypatch, stub_smtp_enqueue
):
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
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    source_webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Primary webhook",
        event_type="rss_item_new",
        url_template="https://example.com/source",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    target_webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Failure webhook",
        event_type="webhook_failed",
        url_template="https://example.com/failure",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    failed_delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=source_webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        feed_id=feed.id,
        delivery_kind="live",
        success=False,
        status_code=500,
        duration_ms=25,
        timeout_seconds=10,
        rendered_url="https://example.com/source",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview="error",
        error="HTTP 500",
        feed_name_snapshot=feed.name,
        attempted_at=datetime.now(timezone.utc),
        delivery_state="failed",
        attempt_count=1,
    )
    prior_failure_notice = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=target_webhook.id,
        user_id=user.id,
        event_type_snapshot="webhook_failed",
        feed_id=feed.id,
        source_delivery_id=failed_delivery.id,
        delivery_kind="live",
        success=True,
        status_code=204,
        duration_ms=11,
        timeout_seconds=10,
        rendered_url="https://example.com/failure",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview="ok",
        error=None,
        feed_name_snapshot=feed.name,
        attempted_at=datetime.now(timezone.utc),
        delivery_state="succeeded",
        attempt_count=1,
    )
    _persist_rows(db_session, user, feed)
    _persist_rows(db_session, source_webhook, target_webhook)
    _persist_rows(db_session, failed_delivery, prior_failure_notice)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.services.notification_webhooks.reserve_notification_webhook_delivery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate failure notice should be skipped")
        ),
    )

    result = dispatch_webhook_failed_notification_webhooks(str(failed_delivery.id))
    routed = route_integration_event(
        db_session,
        event_id=uuid.UUID(result["integration_event_id"]),
    )

    assert result["status"] == "ok"
    assert result["delivery_status"] == "queued"
    assert routed.status == "routed"
    assert db_session.query(NotificationWebhookDelivery).count() == 2


def test_dispatch_pending_notification_webhook_deliveries_recovers_reserved_rows(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Recovery webhook",
        event_type="rss_item_new",
        url_template="https://example.com/recovery",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    pending_delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="pending",
        attempt_count=0,
        success=False,
        timeout_seconds=10,
        rendered_url="https://example.com/recovery",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    _persist_rows(db_session, user)
    _persist_rows(db_session, webhook)
    _persist_rows(db_session, pending_delivery)
    db_session.commit()

    def _fake_send(rendered):
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=9,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview="ok",
            error=None,
        )

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _fake_send,
    )
    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)

    result = dispatch_pending_notification_webhook_deliveries()
    db_session.refresh(pending_delivery)

    assert result["scanned"] == 1
    assert result["delivered"] == 1
    assert result["failed"] == 0
    assert pending_delivery.delivery_state == "succeeded"
    assert pending_delivery.attempt_count == 1
    assert pending_delivery.status_code == 204


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
            delivery_state="succeeded",
            attempt_count=1,
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
            delivery_state="failed",
            attempt_count=1,
        ),
        NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=user.id,
            event_type_snapshot="rss_item_new",
            delivery_kind="live",
            success=False,
            status_code=None,
            duration_ms=None,
            timeout_seconds=10,
            rendered_url="https://example.com/analytics",
            rendered_method="POST",
            rendered_headers_json=[],
            rendered_query_params_json=[],
            rendered_body=None,
            response_body_preview=None,
            error=None,
            attempted_at=datetime.now(timezone.utc) - timedelta(minutes=7),
            delivery_state="pending",
            attempt_count=0,
        ),
        NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=user.id,
            event_type_snapshot="rss_item_new",
            delivery_kind="live",
            success=False,
            status_code=None,
            duration_ms=None,
            timeout_seconds=10,
            rendered_url="https://example.com/analytics",
            rendered_method="POST",
            rendered_headers_json=[],
            rendered_query_params_json=[],
            rendered_body=None,
            response_body_preview=None,
            error=None,
            attempted_at=datetime.now(timezone.utc) - timedelta(minutes=3),
            claimed_at=datetime.now(timezone.utc) - timedelta(minutes=3),
            delivery_state="sending",
            attempt_count=1,
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
    assert [
        (entry.event_type, entry.total_deliveries, entry.failed_deliveries)
        for entry in analytics.events
    ] == [
        ("alert_match", 1, 1),
        ("rss_item_new", 1, 0),
    ]
    assert analytics.queue.status == "critical"
    assert analytics.queue.pending_deliveries == 1
    assert analytics.queue.sending_deliveries == 1
    assert analytics.queue.stale_sending_deliveries == 1
    assert analytics.queue.oldest_pending_age_seconds is not None
    assert analytics.queue.oldest_pending_age_seconds >= 420
    assert analytics.queue.oldest_sending_age_seconds is not None
    assert analytics.queue.oldest_sending_age_seconds >= 180
