from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.models.feed import Feed
from app.schemas.ai import AISettingsUpdate
from app.schemas.notification import NotificationWebhookField, NotificationWebhookWrite
from app.services.ai_config import get_or_create_ai_settings, load_active_ai_settings, resolve_ai_api_key_for_base_url
from app.services.feed_pipeline import upsert_item_from_parsed
from app.services.notification_webhooks import render_notification_request
from app.services.url_utils import normalize_url


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_normalize_url_strips_sensitive_query_params():
    url = "https://example.com/article?token=secret&sig=abc123&a=1&utm_source=newsletter"

    assert normalize_url(url) == "https://example.com/article?a=1"


def test_upsert_item_from_parsed_strips_sensitive_query_params(db_session):
    feed = Feed(
        id=uuid.uuid4(),
        name="Security Feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.flush()

    parsed = SimpleNamespace(
        guid="feed-item-1",
        url="https://example.com/article?token=secret&a=1",
        title="Threat report",
        summary="summary",
        published_at=datetime.now(timezone.utc),
    )

    item, created, is_new = upsert_item_from_parsed(db_session, feed, parsed)

    assert created is True
    assert is_new is True
    assert item.url == "https://example.com/article?a=1"


def test_ai_settings_reject_unsafe_base_url_when_server_ai_api_key_is_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_API_KEY", "shared-provider-secret")

    with pytest.raises(ValueError, match="https://api.openai.com"):
        AISettingsUpdate(base_url="http://localhost:11434/v1")


def test_load_active_ai_settings_does_not_share_server_key_with_non_shared_base_url(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "shared-provider-secret")

    settings = get_or_create_ai_settings(db_session)
    settings.base_url = "https://attacker.example.com/v1"
    settings.model = "gpt-test"
    db_session.add(settings)
    db_session.commit()

    active = load_active_ai_settings(db_session)

    assert active.ai_enabled is True
    assert active.ai_configured is True
    assert active.api_key is None


def test_resolve_ai_api_key_keeps_shared_key_only_for_openai_host():
    assert (
        resolve_ai_api_key_for_base_url("https://api.openai.com/v1", "shared-provider-secret")
        == "shared-provider-secret"
    )
    assert resolve_ai_api_key_for_base_url("http://localhost:11434/v1", "shared-provider-secret") is None
    assert resolve_ai_api_key_for_base_url("https://attacker.example.com/v1", "shared-provider-secret") is None


def test_render_notification_request_sanitizes_item_url_template_values():
    payload = NotificationWebhookWrite(
        name="Example",
        url_template="https://hooks.example.com/notify",
        method="POST",
        body_mode="json",
        body_fields=[NotificationWebhookField(key="event.url", value="{{ item.url }}")],
    )

    rendered = render_notification_request(
        payload,
        user=SimpleNamespace(id=uuid.uuid4(), email="viewer@example.com"),
        feed=SimpleNamespace(id=uuid.uuid4(), name="Unit42", url="https://example.com/feed.xml"),
        item=SimpleNamespace(
            id=uuid.uuid4(),
            title="Threat report",
            url="https://example.com/articles/1?token=secret&ref=partner&keep=1",
            canonical_url=None,
            summary="summary",
            status="new",
            published_at=datetime(2026, 3, 25, 9, 15, tzinfo=timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
        ),
    )

    assert rendered.json_body == {
        "event": {
            "url": "https://example.com/articles/1?keep=1",
        }
    }
