import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.core.config import get_settings
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.services.ai_integration import AICompletionResult
from app.services.ai_ops import (
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TASK_TYPE_REPROCESS,
    AI_TRIGGER_MANUAL,
    queue_ai_task_run,
)


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
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)
    db_session.commit()

    update_response = client.put(
        "/ai/settings",
        json={
            "provider_type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "local-threat-model",
            "request_max_retries": 2,
            "summary_enabled": True,
            "relevance_enabled": True,
            "daily_brief_enabled": True,
            "auto_enrich_new_items": True,
            "daily_brief_window_hours": 24,
            "daily_brief_max_items": 10,
            "daily_brief_schedule_hour_utc": 6,
            "daily_brief_schedule_minute_utc": 45,
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
    assert update_response.json()["request_max_retries"] == 2
    assert update_response.json()["daily_brief_schedule_hour_utc"] == 6
    assert update_response.json()["daily_brief_schedule_minute_utc"] == 45
    assert update_response.json()["item_enrichment_system_prompt"].startswith("You are ThreatLens")
    assert update_response.json()["daily_brief_system_prompt"].startswith("You are ThreatLens")
    assert "ThreatLens, producing structured security analysis" in update_response.json()["prompt_previews"]["item_enrichment"]["system_prompt"]
    assert "Keep it concise." in update_response.json()["prompt_previews"]["item_enrichment"]["system_prompt"]
    assert "ThreatLens, writing a concise security briefing" in update_response.json()["prompt_previews"]["daily_brief"]["system_prompt"]

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


def test_daily_brief_generate_returns_conflict_when_generation_is_already_running(
    client: TestClient,
    auth_headers,
    db_session,
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

    @contextmanager
    def _busy_lock():
        yield False

    monkeypatch.setattr("app.api.routes.ai.daily_ai_brief_lock", _busy_lock)

    response = client.post("/ai/daily-brief/generate", headers=auth_headers["admin"])
    assert response.status_code == 409
    assert response.json()["detail"] == "Daily brief is already running"

    run = db_session.scalar(select(AITaskRun).order_by(AITaskRun.created_at.desc()))
    assert run is not None
    assert run.task_type == "daily_brief"
    assert run.status == "skipped"
    assert run.reason == "already_running"


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

    def _fake_delay(
        days: int | None,
        limit: int,
        start_time: str | None = None,
        end_time: str | None = None,
        feed_ids: list[str] | None = None,
        item_ids: list[str] | None = None,
        task_run_id: str | None = None,
        actor_user_id: str | None = None,
    ):
        captured["days"] = days
        captured["limit"] = limit
        captured["start_time"] = start_time
        captured["end_time"] = end_time
        captured["feed_ids"] = feed_ids or []
        captured["item_ids"] = item_ids or []
        captured["task_run_id"] = task_run_id
        captured["actor_user_id"] = actor_user_id
        return _FakeTask()

    monkeypatch.setattr("app.api.routes.ai.reprocess_recent_ai_items.delay", _fake_delay)

    reprocess_response = client.post(
        "/ai/reprocess",
        json={
            "days": 14,
            "limit": 250,
            "start_time": "2026-03-01T00:00:00Z",
            "end_time": "2026-03-20T12:00:00Z",
            "feed_ids": [str(uuid.uuid4())],
            "item_ids": [str(uuid.uuid4())],
        },
        headers=auth_headers["admin"],
    )
    assert reprocess_response.status_code == 200
    response_payload = reprocess_response.json()
    assert response_payload["task_id"] == "ai-reprocess-123"
    assert response_payload["queued"] is True
    assert response_payload["run_id"]
    assert captured["days"] == 14
    assert captured["limit"] == 250
    assert captured["start_time"] == "2026-03-01T00:00:00+00:00"
    assert captured["end_time"] == "2026-03-20T12:00:00+00:00"
    assert len(captured["feed_ids"]) == 1
    assert len(captured["item_ids"]) == 1
    assert captured["task_run_id"] == response_payload["run_id"]
    assert captured["actor_user_id"]


def test_reprocess_queue_marks_run_error_when_broker_publish_fails(
    client: TestClient,
    auth_headers,
    db_session,
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

    monkeypatch.setattr(
        "app.api.routes.ai.reprocess_recent_ai_items.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    response = client.post(
        "/ai/reprocess",
        json={"days": 14, "limit": 250},
        headers=auth_headers["admin"],
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Task queue is temporarily unavailable. Try again later."

    run = db_session.scalar(select(AITaskRun).order_by(AITaskRun.created_at.desc()))
    assert run is not None
    assert run.task_type == AI_TASK_TYPE_REPROCESS
    assert run.status == "error"
    assert run.reason == "enqueue_failed"
    assert run.error == "broker down"


def test_generate_daily_brief_without_items_returns_clean_422(
    client: TestClient,
    auth_headers,
    ai_enabled_env,
):
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
    assert settings_response.status_code == 200

    response = client.post("/ai/daily-brief/generate", headers=auth_headers["admin"])

    assert response.status_code == 422
    assert response.json()["detail"] == "No items are available for a daily brief"


def test_admin_can_queue_daily_brief_and_cancel_ai_runs(
    client: TestClient,
    auth_headers,
    db_session,
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
        },
        headers=auth_headers["admin"],
    )

    captured: dict[str, object] = {}

    class _FakeBriefTask:
        id = "ai-brief-123"

    def _fake_brief_delay(force: bool = False, task_run_id: str | None = None, actor_user_id: str | None = None):
        captured["force"] = force
        captured["task_run_id"] = task_run_id
        captured["actor_user_id"] = actor_user_id
        return _FakeBriefTask()

    monkeypatch.setattr("app.api.routes.ai.dispatch_daily_ai_brief_generation.delay", _fake_brief_delay)

    queue_response = client.post("/ai/daily-brief/queue", headers=auth_headers["admin"])
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    assert queue_payload["task_id"] == "ai-brief-123"
    assert queue_payload["queued"] is True
    assert queue_payload["run_id"]
    assert captured["force"] is True
    assert captured["task_run_id"] == queue_payload["run_id"]
    assert captured["actor_user_id"]

    run_id = uuid.UUID(queue_payload["run_id"])
    run = db_session.get(AITaskRun, run_id)
    assert run is not None
    run.status = "queued"
    run.celery_task_id = "ai-brief-123"
    db_session.add(run)
    db_session.commit()

    revoked: list[tuple[str, bool, str]] = []

    def _fake_revoke(task_id: str, terminate: bool = False, signal: str = "SIGTERM"):
        revoked.append((task_id, terminate, signal))

    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: ([], [], [], []))
    monkeypatch.setattr("app.services.ai_ops.celery_app.control.revoke", _fake_revoke)

    cancel_response = client.post(f"/ai/ops/runs/{run_id}/cancel", headers=auth_headers["admin"])
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    assert cancel_payload["id"] == str(run_id)
    assert cancel_payload["status"] == "skipped"
    assert cancel_payload["reason"] == "canceled"
    assert revoked == [("ai-brief-123", False, "SIGTERM")]


def test_daily_brief_queue_marks_run_error_when_broker_publish_fails(
    client: TestClient,
    auth_headers,
    db_session,
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
        },
        headers=auth_headers["admin"],
    )

    monkeypatch.setattr(
        "app.api.routes.ai.dispatch_daily_ai_brief_generation.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    response = client.post("/ai/daily-brief/queue", headers=auth_headers["admin"])
    assert response.status_code == 503
    assert response.json()["detail"] == "Task queue is temporarily unavailable. Try again later."

    run = db_session.scalar(select(AITaskRun).order_by(AITaskRun.created_at.desc()))
    assert run is not None
    assert run.task_type == "daily_brief"
    assert run.status == "error"
    assert run.reason == "enqueue_failed"
    assert run.error == "broker down"


def test_admin_can_list_reprocess_child_runs_with_article_context(
    client: TestClient,
    auth_headers,
    db_session,
    ai_enabled_env,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Blog",
        url="https://example.com/threat-blog.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-blog-1",
        url="https://example.com/articles/threat-blog-1",
        canonical_url="https://example.com/articles/threat-blog-1",
        title="Zero-day exploitation expands to edge appliances",
        summary="Attackers are expanding campaigns against exposed edge appliances.",
        published_at=datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc),
        dedupe_key="threat-blog-1",
        content_hash="b" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.flush()

    actor = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert actor is not None

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=actor.id,
        metadata={"days": 7, "limit": 10},
    )
    child_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        parent_run_id=parent_run.id,
        item_id=item.id,
        metadata={"parent_task": "reprocess"},
    )
    db_session.commit()

    response = client.get(
        f"/ai/ops/runs?parent_run_id={parent_run.id}&limit=10",
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(child_run.id)
    assert payload["items"][0]["item_id"] == str(item.id)
    assert payload["items"][0]["item_title"] == item.title
    assert payload["items"][0]["feed_name"] == feed.name
    assert payload["items"][0]["item_url"] == item.url
    assert payload["items"][0]["item_first_seen_at"] is not None


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
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)
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
    assert [event["event_type"] for event in detail_payload["events"]] == [
        "queued",
        "started",
        "provider_exchange",
        "completed",
    ]

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
