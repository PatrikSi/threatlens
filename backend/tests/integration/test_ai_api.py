import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.ai_integration import AICompletionResult


@pytest.fixture()
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_admin_can_manage_ai_settings_generate_daily_brief_and_read_usage(
    client: TestClient,
    auth_headers,
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="cisa-1",
        url="https://example.com/articles/cisa-1",
        canonical_url="https://example.com/articles/cisa-1",
        title="Critical edge-device activity observed",
        summary="New exploitation activity was observed against exposed edge infrastructure.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="cisa-1",
        content_hash="a" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Analysts observed exploitation targeting exposed edge devices and remote access platforms.",
        extraction_method="readable",
    )
    db_session.add_all([feed, item, article])
    db_session.commit()

    update_response = client.put(
        "/ai/settings",
        json={
            "provider_type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "local-threat-model",
            "summary_enabled": True,
            "relevance_enabled": True,
            "daily_brief_enabled": True,
            "auto_enrich_new_items": True,
            "daily_brief_window_hours": 24,
            "daily_brief_max_items": 10,
            "relevance_medium_threshold": 0.55,
            "relevance_high_threshold": 0.8,
            "company_name": "Example Corp",
            "company_industry": "technology",
            "company_regions": ["US"],
            "company_stack": ["Fortinet", "Microsoft 365"],
            "company_priority_topics": ["edge security"],
            "company_keywords": ["vpn", "sso"],
            "company_exclusions": ["consumer scams"],
            "company_profile_text": "Protects enterprise edge services and identity systems.",
            "global_instructions": "Keep it concise.",
            "item_summary_instructions": "Summaries should highlight operational impact.",
            "relevance_instructions": "Prioritize enterprise edge security mentions.",
            "daily_brief_instructions": "Focus on what the security team should care about first.",
        },
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200
    assert update_response.json()["ai_configured"] is True

    def _fake_call(active, *, messages):
        _ = messages
        return AICompletionResult(
            payload={
                "title": "Daily Brief",
                "brief_text": "Edge exploitation activity deserves review today.",
                "key_points": ["Exposed edge services were targeted."],
                "recommended_actions": ["Review exposed VPN and remote access systems."],
            },
            provider=active.provider_type,
            model=active.model,
            latency_ms=118,
            prompt_tokens=111,
            completion_tokens=45,
            total_tokens=156,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    generate_response = client.post("/ai/daily-brief/generate", headers=auth_headers["admin"])
    assert generate_response.status_code == 200
    payload = generate_response.json()
    assert payload["status"] == "ready"
    assert payload["title"] == "Daily Brief"
    assert payload["items"][0]["id"] == str(item.id)

    latest_response = client.get("/ai/daily-brief/latest", headers=auth_headers["viewer"])
    assert latest_response.status_code == 200
    assert latest_response.json()["title"] == "Daily Brief"

    usage_response = client.get("/ai/usage", headers=auth_headers["admin"])
    assert usage_response.status_code == 200
    usage_payload = usage_response.json()
    assert usage_payload["total_requests"] == 1
    assert usage_payload["successful_requests"] == 1
    assert usage_payload["total_tokens"] == 156

    usage_events = db_session.scalars(select(AIUsageEvent)).all()
    assert len(usage_events) == 1
    assert usage_events[0].feature_type == "daily_brief"


def test_admin_can_test_connection_and_queue_ai_reprocess(
    client: TestClient,
    auth_headers,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    client.put(
        "/ai/settings",
        json={
            "provider_type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "local-threat-model",
            "summary_enabled": True,
            "relevance_enabled": True,
            "daily_brief_enabled": True,
            "auto_enrich_new_items": True,
            "daily_brief_window_hours": 24,
            "daily_brief_max_items": 10,
            "relevance_medium_threshold": 0.55,
            "relevance_high_threshold": 0.8,
            "company_regions": [],
            "company_stack": [],
            "company_priority_topics": [],
            "company_keywords": [],
            "company_exclusions": [],
        },
        headers=auth_headers["admin"],
    )

    def _fake_call(active, *, messages):
        _ = messages
        return AICompletionResult(
            payload={"ok": True, "message": "ready"},
            provider=active.provider_type,
            model=active.model,
            latency_ms=32,
            prompt_tokens=12,
            completion_tokens=6,
            total_tokens=18,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    test_response = client.post("/ai/test-connection", headers=auth_headers["admin"])
    assert test_response.status_code == 200
    assert test_response.json()["success"] is True

    captured: dict[str, object] = {}

    class _FakeTask:
        id = "ai-reprocess-123"

    def _fake_delay(days: int, limit: int):
        captured["days"] = days
        captured["limit"] = limit
        return _FakeTask()

    monkeypatch.setattr("app.api.routes.ai.reprocess_recent_ai_items.delay", _fake_delay)

    reprocess_response = client.post(
        "/ai/reprocess",
        json={"days": 14, "limit": 250},
        headers=auth_headers["admin"],
    )
    assert reprocess_response.status_code == 200
    assert reprocess_response.json() == {"task_id": "ai-reprocess-123", "queued": True}
    assert captured == {"days": 14, "limit": 250}


def test_viewer_cannot_access_ai_admin_routes(client: TestClient, auth_headers, ai_enabled_env):
    response = client.get("/ai/settings", headers=auth_headers["viewer"])
    assert response.status_code == 403
