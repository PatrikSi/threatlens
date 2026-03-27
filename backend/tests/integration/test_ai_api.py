import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.ai_daily_brief import AIDailyBrief
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
    assert update_response.json()["item_enrichment_system_prompt"].startswith("You are ThreatLens")
    assert update_response.json()["daily_brief_system_prompt"].startswith("You are ThreatLens")
    assert "ThreatLens, producing structured security analysis" in update_response.json()["prompt_previews"]["item_enrichment"]["system_prompt"]
    assert "Keep it concise." in update_response.json()["prompt_previews"]["item_enrichment"]["system_prompt"]
    assert "ThreatLens, writing an executive security briefing" in update_response.json()["prompt_previews"]["daily_brief"]["system_prompt"]

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
    assert payload["item_count"] == 1

    latest_response = client.get("/ai/daily-brief/latest", headers=auth_headers["viewer"])
    assert latest_response.status_code == 200
    assert latest_response.json()["title"] == "Daily Brief"

    list_response = client.get("/ai/daily-briefs", headers=auth_headers["viewer"])
    assert list_response.status_code == 200
    briefs_payload = list_response.json()
    assert len(briefs_payload) == 1
    assert briefs_payload[0]["id"] == payload["id"]

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

    def _fake_delay(days: int, limit: int, task_run_id: str | None = None, actor_user_id: str | None = None):
        captured["days"] = days
        captured["limit"] = limit
        captured["task_run_id"] = task_run_id
        captured["actor_user_id"] = actor_user_id
        return _FakeTask()

    monkeypatch.setattr("app.api.routes.ai.reprocess_recent_ai_items.delay", _fake_delay)

    reprocess_response = client.post(
        "/ai/reprocess",
        json={"days": 14, "limit": 250},
        headers=auth_headers["admin"],
    )
    assert reprocess_response.status_code == 200
    response_payload = reprocess_response.json()
    assert response_payload["task_id"] == "ai-reprocess-123"
    assert response_payload["queued"] is True
    assert response_payload["run_id"]
    assert captured["days"] == 14
    assert captured["limit"] == 250
    assert captured["task_run_id"] == response_payload["run_id"]
    assert captured["actor_user_id"]


def test_viewer_cannot_access_ai_admin_routes(client: TestClient, auth_headers, ai_enabled_env):
    response = client.get("/ai/settings", headers=auth_headers["viewer"])
    assert response.status_code == 403


def test_daily_brief_history_limit_update_prunes_old_briefs(client: TestClient, auth_headers, db_session, ai_enabled_env):
    older_brief = AIDailyBrief(
        brief_date=datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc).date(),
        status="ready",
        window_start=datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc),
        title="Older brief",
        brief_text="Older brief text",
        generated_at=datetime(2026, 3, 24, 0, 5, tzinfo=timezone.utc),
    )
    latest_brief = AIDailyBrief(
        brief_date=datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc).date(),
        status="ready",
        window_start=datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
        title="Latest brief",
        brief_text="Latest brief text",
        generated_at=datetime(2026, 3, 25, 0, 5, tzinfo=timezone.utc),
    )
    db_session.add_all([older_brief, latest_brief])
    db_session.commit()

    response = client.put(
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
            "daily_brief_history_limit": 1,
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

    assert response.status_code == 200
    assert response.json()["daily_brief_history_limit"] == 1

    remaining_briefs = db_session.scalars(select(AIDailyBrief).order_by(AIDailyBrief.brief_date.desc())).all()
    assert [brief.title for brief in remaining_briefs] == ["Latest brief"]


def test_ai_ops_endpoints_expose_runs_sources_and_audit_logs(
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
        source_guid="cisa-ops-1",
        url="https://example.com/articles/cisa-ops-1",
        canonical_url="https://example.com/articles/cisa-ops-1",
        title="Fortinet edge activity escalates",
        summary="Operational teams should review exposed systems.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="cisa-ops-1",
        content_hash="c" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat actors are targeting exposed Fortinet edge infrastructure and remote access systems.",
        extraction_method="readable",
    )
    db_session.add_all([feed, item, article])
    db_session.commit()

    settings_response = client.put(
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
            "daily_brief_max_items": 5,
            "relevance_medium_threshold": 0.55,
            "relevance_high_threshold": 0.8,
            "company_regions": [],
            "company_stack": ["Fortinet"],
            "company_priority_topics": [],
            "company_keywords": ["vpn"],
            "company_exclusions": [],
            "global_instructions": "Stay concise.",
        },
        headers=auth_headers["admin"],
    )
    assert settings_response.status_code == 200

    def _fake_call(active, *, messages):
        prompt = messages[-1]["content"]
        if "connection_test" in prompt:
            return AICompletionResult(
                payload={"ok": True, "message": "ready"},
                provider=active.provider_type,
                model=active.model,
                latency_ms=15,
                prompt_tokens=8,
                completion_tokens=4,
                total_tokens=12,
            )
        return AICompletionResult(
            payload={
                "title": "Ops Daily Brief",
                "brief_text": "Edge infrastructure deserves immediate review.",
                "key_points": ["Fortinet edge devices were mentioned."],
                "recommended_actions": ["Review exposed remote access services."],
            },
            provider=active.provider_type,
            model=active.model,
            latency_ms=41,
            prompt_tokens=60,
            completion_tokens=20,
            total_tokens=80,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    connection_response = client.post("/ai/test-connection", headers=auth_headers["admin"])
    assert connection_response.status_code == 200
    assert connection_response.json()["success"] is True

    brief_response = client.post("/ai/daily-brief/generate", headers=auth_headers["admin"])
    assert brief_response.status_code == 200
    brief_payload = brief_response.json()

    overview_response = client.get("/ai/ops/overview?days=30", headers=auth_headers["admin"])
    assert overview_response.status_code == 200
    overview_payload = overview_response.json()
    assert overview_payload["kpis"]["total_requests"] >= 2
    assert overview_payload["per_model"][0]["model"] == "local-threat-model"
    assert overview_payload["storage"]["task_history_rows"] >= 2

    runs_response = client.get("/ai/ops/runs?task_type=daily_brief", headers=auth_headers["admin"])
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["total"] >= 1
    run_id = runs_payload["items"][0]["id"]

    detail_response = client.get(f"/ai/ops/runs/{run_id}", headers=auth_headers["admin"])
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["run"]["metadata"]["items_selected"] == 1
    assert [event["event_type"] for event in detail_payload["events"]] == ["queued", "started", "completed"]

    sources_response = client.get(f"/ai/daily-briefs/{brief_payload['id']}/sources", headers=auth_headers["admin"])
    assert sources_response.status_code == 200
    sources_payload = sources_response.json()
    assert len(sources_payload) == 1
    assert sources_payload[0]["included"] is True
    assert sources_payload[0]["item_id"] == str(item.id)

    prompt_history_response = client.get("/ai/ops/prompt-history", headers=auth_headers["admin"])
    assert prompt_history_response.status_code == 200
    prompt_history_payload = prompt_history_response.json()
    assert prompt_history_payload[0]["action"] == "ai.settings.update"
    assert "changed_fields" in prompt_history_payload[0]["metadata"]

    manual_actions_response = client.get("/ai/ops/manual-actions", headers=auth_headers["admin"])
    assert manual_actions_response.status_code == 200
    manual_actions = manual_actions_response.json()
    assert {entry["action"] for entry in manual_actions} >= {"ai.connection.test", "ai.daily_brief.generate"}
