import json
import uuid
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

import app.services.ai_integration as ai_integration_module
from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.ai_settings import AISettings
from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.integration import IntegrationEvent
from app.services.ai_config import (
    DEFAULT_DAILY_BRIEF_SYSTEM_PROMPT,
    DEFAULT_ITEM_ENRICHMENT_SYSTEM_PROMPT,
    apply_ai_settings_update,
    build_daily_brief_system_prompt,
    build_item_enrichment_system_prompt,
    get_or_create_ai_settings,
    load_active_ai_settings,
)
from app.services.ai_integration import (
    AICompletionResult,
    AIIntegrationError,
    FEATURE_DAILY_BRIEF,
    FEATURE_REPORT,
    _call_ai_json,
    _build_chat_completion_url,
    _next_retry_max_completion_tokens,
    generate_daily_brief,
    generate_item_ai_enrichment,
    get_latest_daily_brief,
    run_daily_brief_generation,
    run_item_ai_enrichment,
    request_ai_json_with_usage,
)
from app.services.ai_ops import AI_TASK_TYPE_DAILY_BRIEF, AI_TASK_TYPE_ITEM_ENRICHMENT, AI_TRIGGER_MANUAL, queue_ai_task_run
from app.schemas.ai import AISettingsUpdate


@pytest.fixture()
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _fake_httpx_client_factory(response_payload: dict[str, object]):
    response_text = json.dumps(response_payload)

    class _FakeResponse:
        status_code = 200
        text = response_text

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return response_payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers, json):
            _ = (url, headers, json)
            return _FakeResponse()

    return _FakeClient


def _fake_httpx_client_sequence_factory(response_payloads: list[dict[str, object]]):
    payloads = list(response_payloads)

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers, json):
            _ = (url, headers, json)
            if not payloads:
                raise AssertionError("No fake AI payloads remaining")
            current = payloads.pop(0)

            class _FakeResponse:
                status_code = 200
                text = json.dumps(current)

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, object]:
                    return current

            return _FakeResponse()

    return _FakeClient


def _ai_chat_response(content: str, *, model: str = "local-threat-model") -> dict[str, object]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1774613335,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 4,
            "total_tokens": 12,
        },
    }


def test_build_chat_completion_url_accepts_bare_openai_compatible_origins():
    assert (
        _build_chat_completion_url("http://192.168.0.113:11434")
        == "http://192.168.0.113:11434/v1/chat/completions"
    )
    assert _build_chat_completion_url("http://localhost:11434/") == "http://localhost:11434/v1/chat/completions"
    assert _build_chat_completion_url("http://localhost:11434/v1") == "http://localhost:11434/v1/chat/completions"
    assert (
        _build_chat_completion_url("http://localhost:11434/v1/chat/completions")
        == "http://localhost:11434/v1/chat/completions"
    )


def test_call_ai_json_omits_authorization_for_local_unauthenticated_endpoint(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()
    active = load_active_ai_settings(db_session)
    captured: dict[str, object] = {}
    payload = _ai_chat_response('{"ok": true}')

    class _FakeResponse:
        status_code = 200
        text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return payload

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["body"] = dict(json)
            return _FakeResponse()

    monkeypatch.setattr("app.services.ai_integration.build_safe_http_client", lambda *args, **kwargs: _FakeClient())

    result = _call_ai_json(active, messages=[{"role": "user", "content": '{"task":"connection_test"}'}])

    assert result.payload == {"ok": True}
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in captured["headers"]


def test_call_ai_json_sends_authorization_for_shared_openai_endpoint(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_API_KEY", "shared-provider-secret")
    get_settings.cache_clear()
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="https://api.openai.com/v1",
            model="gpt-test",
        ),
    )
    db_session.add(settings)
    db_session.commit()
    active = load_active_ai_settings(db_session)
    captured: dict[str, object] = {}
    payload = _ai_chat_response('{"ok": true}', model="gpt-test")

    class _FakeResponse:
        status_code = 200
        text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return payload

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers, json):
            _ = (url, json)
            captured["headers"] = dict(headers)
            return _FakeResponse()

    monkeypatch.setattr("app.services.ai_integration.build_safe_http_client", lambda *args, **kwargs: _FakeClient())

    result = _call_ai_json(active, messages=[{"role": "user", "content": '{"task":"connection_test"}'}])

    assert result.payload == {"ok": True}
    assert captured["headers"]["Authorization"] == "Bearer shared-provider-secret"


def test_call_ai_json_surfaces_nonstandard_provider_auth_error_without_retry_flag(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()
    active = load_active_ai_settings(db_session)
    payload = {"error": "Authentication required."}

    class _FakeResponse:
        status_code = 200
        text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return payload

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers, json):
            _ = (url, headers, json)
            return _FakeResponse()

    monkeypatch.setattr("app.services.ai_integration.build_safe_http_client", lambda *args, **kwargs: _FakeClient())

    with pytest.raises(AIIntegrationError) as exc_info:
        _call_ai_json(active, messages=[{"role": "user", "content": '{"task":"connection_test"}'}])

    assert str(exc_info.value) == "Authentication required."
    assert exc_info.value.retryable is False


def _persist_feed_item(db_session, feed: Feed, item: Item, *children: object) -> None:
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    if children:
        db_session.add_all(list(children))
        db_session.flush()


def test_get_or_create_ai_settings_uses_updated_runtime_defaults(db_session):
    settings = get_or_create_ai_settings(db_session)

    assert settings.max_completion_tokens == 5000
    assert settings.request_timeout_seconds == 300
    assert settings.request_max_retries == 3


def test_run_daily_brief_generation_returns_skipped_result_when_window_is_empty(db_session, ai_enabled_env):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    result = run_daily_brief_generation(db_session, force=True)

    assert result.status == "skipped"
    assert result.reason == "no_items"
    assert result.brief is None
    assert result.items_considered == 0
    assert result.items_selected == 0


def test_run_daily_brief_generation_caps_source_rows_before_model_call(db_session, ai_enabled_env, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    now = datetime.now(timezone.utc)
    db_session.add(feed)
    db_session.flush()
    for index in range(8):
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"brief-cap-{index}",
            url=f"https://example.com/articles/brief-cap-{index}",
            canonical_url=f"https://example.com/articles/brief-cap-{index}",
            title=f"Daily brief cap item {index}",
            summary="Summary",
            published_at=now - timedelta(minutes=index),
            first_seen_at=now - timedelta(minutes=index),
            dedupe_key=f"brief-cap-{index}",
            content_hash=f"{index}" * 64,
            status="content_fetched",
        )
        db_session.add(item)
        db_session.flush()
        db_session.add(
            ItemClassification(
                item_id=item.id,
                primary_category="vulnerability",
                secondary_categories=[],
                confidence=0.8,
                scores_json={"vulnerability": 8},
                matched_terms_json={"vulnerability": ["title:brief"]},
                source_hash=f"classification-{index}",
                rules_version="v2",
                classified_at=now,
            )
        )
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
            daily_brief_max_items=5,
        ),
    )
    db_session.add(settings)
    db_session.commit()
    monkeypatch.setattr(get_settings(), "ai_daily_brief_source_audit_limit", 6)

    captured: dict[str, int] = {}

    def _fake_call(active, *, messages):
        _ = active
        assert not db_session.in_transaction()
        captured["prompt_item_mentions"] = sum(str(message.get("content", "")).count("Daily brief cap item") for message in messages)
        return AICompletionResult(
            payload={
                "title": "Daily Brief",
                "brief_text": "Brief text",
                "key_points": ["Point"],
                "recommended_actions": ["Action"],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=10,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    result = run_daily_brief_generation(db_session, force=True)
    db_session.commit()

    source_rows = db_session.scalars(select(AIDailyBriefSourceItem).where(AIDailyBriefSourceItem.daily_brief_id == result.brief.id)).all()
    assert result.items_considered == 6
    assert result.items_selected == 5
    assert result.brief.item_count == 8
    assert len(source_rows) == 6
    assert sum(1 for row in source_rows if row.included) == 5
    assert captured["prompt_item_mentions"] == 5


def test_first_daily_brief_claim_is_single_winner_across_postgresql_sessions(
    database_engine,
    ai_enabled_env,
    monkeypatch,
):
    reference_time = datetime(2040, 5, 17, 12, 0, tzinfo=timezone.utc)
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    with Session(database_engine) as setup:
        settings = get_or_create_ai_settings(setup)
        apply_ai_settings_update(
            settings,
            AISettingsUpdate(
                base_url="http://localhost:11434/v1",
                model="local-threat-model",
                daily_brief_enabled=True,
            ),
        )
        setup.add_all(
            [
                settings,
                Feed(
                    id=feed_id,
                    name="Concurrent brief feed",
                    url=f"https://example.com/{feed_id}.xml",
                ),
            ]
        )
        setup.flush()
        setup.add(
            Item(
                id=item_id,
                feed_id=feed_id,
                source_guid=str(item_id),
                url=f"https://example.com/items/{item_id}",
                canonical_url=f"https://example.com/items/{item_id}",
                title="Concurrent brief source",
                summary="Only one provider request should win the daily claim.",
                published_at=reference_time - timedelta(minutes=5),
                first_seen_at=reference_time - timedelta(minutes=4),
                dedupe_key=f"concurrent-brief:{item_id}",
                content_hash=uuid.uuid4().hex,
            )
        )
        setup.commit()

    barrier = Barrier(2)
    result_lock = Lock()
    results = []
    errors: list[BaseException] = []
    provider_calls = 0
    original_build_messages = ai_integration_module._build_daily_brief_messages

    def _gated_build_messages(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_build_messages(*args, **kwargs)

    def _fake_call(_active, *, messages):
        nonlocal provider_calls
        assert messages
        with result_lock:
            provider_calls += 1
        return AICompletionResult(
            payload={
                "title": "Concurrent Daily Brief",
                "brief_text": "One durable result.",
                "key_points": ["One winner"],
                "recommended_actions": ["Review"],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=10,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(
        ai_integration_module,
        "_build_daily_brief_messages",
        _gated_build_messages,
    )
    monkeypatch.setattr(ai_integration_module, "_call_ai_json", _fake_call)

    def _generate() -> None:
        try:
            with Session(database_engine) as worker:
                result = run_daily_brief_generation(
                    worker,
                    force=True,
                    reference_time=reference_time,
                    emit_notification=False,
                )
                worker.commit()
                with result_lock:
                    results.append(result)
        except BaseException as exc:
            with result_lock:
                errors.append(exc)

    threads = [Thread(target=_generate), Thread(target=_generate)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert provider_calls == 1
        assert sorted(result.status for result in results) == ["ready", "skipped"]
        assert {result.reason for result in results} <= {
            None,
            "already_generated",
            "already_running",
        }
        with Session(database_engine) as check:
            briefs = list(
                check.scalars(
                    select(AIDailyBrief).where(
                        AIDailyBrief.brief_date == reference_time.date()
                    )
                )
            )
            assert len(briefs) == 1
            assert briefs[0].status == "ready"
    finally:
        with Session(database_engine) as cleanup:
            brief_ids = select(AIDailyBrief.id).where(
                AIDailyBrief.brief_date == reference_time.date()
            )
            cleanup.execute(
                delete(AIUsageEvent).where(
                    AIUsageEvent.daily_brief_id.in_(brief_ids)
                )
            )
            cleanup.execute(
                delete(AIDailyBrief).where(
                    AIDailyBrief.brief_date == reference_time.date()
                )
            )
            cleanup.execute(delete(Item).where(Item.id == item_id))
            cleanup.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup.execute(delete(AISettings))
            cleanup.commit()


def test_run_daily_brief_generation_uses_published_at_before_first_seen(db_session, ai_enabled_env, monkeypatch):
    reference_time = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa-window.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.flush()

    def add_item(title: str, *, published_at: datetime | None, first_seen_at: datetime, suffix: str) -> None:
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"brief-window-{suffix}",
            url=f"https://example.com/articles/brief-window-{suffix}",
            canonical_url=f"https://example.com/articles/brief-window-{suffix}",
            title=title,
            summary="Summary",
            published_at=published_at,
            first_seen_at=first_seen_at,
            dedupe_key=f"brief-window-{suffix}",
            content_hash=suffix[-1] * 64,
            status="content_fetched",
        )
        db_session.add(item)
        db_session.flush()

    add_item(
        "Old published backfill",
        published_at=reference_time - timedelta(days=3),
        first_seen_at=reference_time - timedelta(minutes=30),
        suffix="old-backfill-1",
    )
    add_item(
        "Current publication seen earlier",
        published_at=reference_time - timedelta(hours=2),
        first_seen_at=reference_time - timedelta(days=2),
        suffix="current-old-seen-2",
    )
    add_item(
        "Current publication seen now",
        published_at=reference_time - timedelta(hours=1),
        first_seen_at=reference_time - timedelta(minutes=30),
        suffix="current-new-seen-3",
    )
    add_item(
        "Undated recent arrival",
        published_at=None,
        first_seen_at=reference_time - timedelta(hours=3),
        suffix="undated-recent-4",
    )
    add_item(
        "Undated stale arrival",
        published_at=None,
        first_seen_at=reference_time - timedelta(days=3),
        suffix="undated-stale-5",
    )
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
            daily_brief_window_hours=24,
            daily_brief_max_items=10,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    captured_titles: list[str] = []

    def _fake_call(active, *, messages):
        _ = active
        request_payload = json.loads(messages[1]["content"])
        captured_titles[:] = [item["title"] for item in request_payload["items"]]
        return AICompletionResult(
            payload={
                "title": "Daily Brief",
                "brief_text": "Brief text",
                "key_points": ["Point"],
                "recommended_actions": ["Action"],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=10,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    result = run_daily_brief_generation(db_session, force=True, reference_time=reference_time)
    db_session.commit()

    assert result.status == "ready"
    assert result.items_considered == 3
    assert result.items_selected == 3
    assert result.brief.item_count == 3
    assert captured_titles == [
        "Current publication seen now",
        "Current publication seen earlier",
        "Undated recent arrival",
    ]

    source_rows = db_session.scalars(
        select(AIDailyBriefSourceItem).where(AIDailyBriefSourceItem.daily_brief_id == result.brief.id)
    ).all()
    assert {row.title_snapshot for row in source_rows} == set(captured_titles)


def test_daily_brief_retry_budget_never_shrinks_after_truncation():
    error = AIIntegrationError("truncated", retry_hint="expand_completion_budget")

    next_budget = _next_retry_max_completion_tokens(
        feature_type=FEATURE_DAILY_BRIEF,
        current=5000,
        error=error,
    )

    assert next_budget >= 5000


def test_report_retry_expands_only_to_exact_context_headroom(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=2,
            max_completion_tokens=2000,
        ),
    )
    db_session.add(settings)
    db_session.commit()
    active = load_active_ai_settings(db_session)
    requested: list[int] = []
    checkpoints: list[int] = []

    def _truncated_then_valid(active, *, messages, max_completion_tokens):
        _ = (active, messages)
        requested.append(max_completion_tokens)
        if len(requested) < 3:
            raise AIIntegrationError(
                "truncated", retry_hint="expand_completion_budget", retryable=True
            )
        return AICompletionResult(
            payload={"ok": True},
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=10,
            prompt_tokens=100,
            completion_tokens=600,
            total_tokens=700,
        )

    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json", _truncated_then_valid
    )

    result = request_ai_json_with_usage(
        db_session,
        active,
        feature_type=FEATURE_REPORT,
        messages=[{"role": "user", "content": "report"}],
        max_completion_tokens=256,
        max_retry_completion_tokens=700,
        execution_checkpoint=lambda: checkpoints.append(len(requested)),
    )

    assert result.payload == {"ok": True}
    assert requested == [256, 512, 700]
    assert result.attempt_count == 3
    assert checkpoints == [0, 1, 1, 2, 2, 3]


def test_report_retry_respects_provider_attempt_budget(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=3,
            max_completion_tokens=2000,
        ),
    )
    db_session.add(settings)
    db_session.commit()
    active = load_active_ai_settings(db_session)
    requested: list[int] = []

    def _always_truncated(active, *, messages, max_completion_tokens):
        _ = (active, messages)
        requested.append(max_completion_tokens)
        raise AIIntegrationError(
            "truncated", retry_hint="expand_completion_budget", retryable=True
        )

    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json", _always_truncated
    )

    with pytest.raises(AIIntegrationError, match="truncated") as exc_info:
        request_ai_json_with_usage(
            db_session,
            active,
            feature_type=FEATURE_REPORT,
            messages=[{"role": "user", "content": "report"}],
            max_completion_tokens=256,
            max_retry_completion_tokens=1000,
            max_provider_attempts=2,
        )

    assert requested == [256, 512]
    assert exc_info.value.attempt_count == 2


def test_generate_item_ai_enrichment_stores_summary_relevance_and_usage(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-1",
        url="https://example.com/articles/unit42-1",
        canonical_url="https://example.com/articles/unit42-1",
        title="Fortinet edge exploitation observed",
        summary="Researchers observed new edge exploitation activity.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-1",
        content_hash="a" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices and remote access services.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=["incident_breach"],
        confidence=0.87,
        scores_json={"vulnerability": 7.8},
        matched_terms_json={"vulnerability": ["title:exploit"]},
        source_hash="hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    _persist_feed_item(db_session, feed, item, article, classification)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            company_name="Example Corp",
            company_stack=["Fortinet"],
            company_keywords=["vpn"],
        ),
    )
    db_session.add(settings)
    db_session.commit()

    def _fake_call(active, *, messages):
        _ = (active, messages)
        assert not db_session.in_transaction()
        return AICompletionResult(
            payload={
                "summary_text": "AI summary of the exploitation activity.",
                "relevance_score": 0.91,
                "relevance_reasons": ["Mentions Fortinet", "Targets exposed edge systems"],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=75,
            prompt_tokens=80,
            completion_tokens=20,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    enrichment = generate_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()

    assert enrichment is not None
    assert enrichment.status == "ready"
    assert enrichment.summary_text == "AI summary of the exploitation activity."
    assert enrichment.relevance_label == "high"

    stored = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    assert stored is not None
    assert stored.total_tokens == 100

    usage_events = db_session.scalars(select(AIUsageEvent)).all()
    assert len(usage_events) == 1
    assert usage_events[0].feature_type == "item_enrichment"


def test_run_item_ai_enrichment_skips_when_matching_enrichment_is_already_pending(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-pending",
        url="https://example.com/articles/unit42-pending",
        canonical_url="https://example.com/articles/unit42-pending",
        title="Fortinet edge exploitation observed",
        summary="Researchers observed new edge exploitation activity.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-pending",
        content_hash="c" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices and remote access services.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=["incident_breach"],
        confidence=0.87,
        scores_json={"vulnerability": 7.8},
        matched_terms_json={"vulnerability": ["title:exploit"]},
        source_hash="hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    _persist_feed_item(db_session, feed, item, article, classification)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            company_name="Example Corp",
            company_stack=["Fortinet"],
            company_keywords=["vpn"],
        ),
    )
    db_session.add(settings)
    db_session.commit()

    def _fake_call(active, *, messages):
        _ = (active, messages)
        return AICompletionResult(
            payload={
                "summary_text": "AI summary of the exploitation activity.",
                "relevance_score": 0.91,
                "relevance_reasons": ["Mentions Fortinet", "Targets exposed edge systems"],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=75,
            prompt_tokens=80,
            completion_tokens=20,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    enrichment = generate_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert enrichment is not None

    enrichment.status = "pending"
    enrichment.summary_text = None
    enrichment.relevance_score = None
    enrichment.relevance_label = None
    enrichment.relevance_reasons_json = []
    db_session.add(enrichment)
    db_session.commit()

    def _unexpected_call(active, *, messages):
        _ = (active, messages)
        raise AssertionError("AI request should not run while matching enrichment is already pending")

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _unexpected_call)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=False)

    assert result.status == "skipped"
    assert result.reason == "already_pending"
    assert result.enrichment is not None

    usage_events = db_session.scalars(select(AIUsageEvent)).all()
    assert len(usage_events) == 1
    assert usage_events[0].feature_type == "item_enrichment"


def test_run_item_ai_enrichment_recovers_matching_pending_row_for_task_redelivery(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-pending-redelivery",
        url="https://example.com/articles/unit42-pending-redelivery",
        canonical_url="https://example.com/articles/unit42-pending-redelivery",
        title="Fortinet edge exploitation observed",
        summary="Researchers observed new edge exploitation activity.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-pending-redelivery",
        content_hash="c" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices and remote access services.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=["incident_breach"],
        confidence=0.87,
        scores_json={"vulnerability": 7.8},
        matched_terms_json={"vulnerability": ["title:exploit"]},
        source_hash="hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    _persist_feed_item(db_session, feed, item, article, classification)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            company_name="Example Corp",
            company_stack=["Fortinet"],
            company_keywords=["vpn"],
        ),
    )
    db_session.add(settings)
    db_session.commit()

    enrichment = generate_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert enrichment is not None

    enrichment.status = "pending"
    enrichment.summary_text = None
    enrichment.relevance_score = None
    enrichment.relevance_label = None
    enrichment.relevance_reasons_json = []
    db_session.add(enrichment)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json",
        lambda active, *, messages: AICompletionResult(
            payload={
                "summary_text": "Recovered AI summary of the exploitation activity.",
                "relevance_score": 0.93,
                "relevance_reasons": ["Mentions Fortinet", "Recovered after redelivery"],
            },
            provider="openai_compatible",
            model=active.model,
            latency_ms=75,
            prompt_tokens=80,
            completion_tokens=20,
            total_tokens=100,
        ),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=False, task_run_id=run.id)
    db_session.commit()

    db_session.refresh(enrichment)
    assert result.status == "ready"
    assert result.reason is None
    assert enrichment.status == "ready"
    assert enrichment.summary_text == "Recovered AI summary of the exploitation activity."


def test_run_item_ai_enrichment_force_updates_existing_row_in_place(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-existing",
        url="https://example.com/articles/unit42-existing",
        canonical_url="https://example.com/articles/unit42-existing",
        title="Fortinet edge exploitation observed",
        summary="Researchers observed new edge exploitation activity.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-existing",
        content_hash="d" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices and remote access services.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=["incident_breach"],
        confidence=0.87,
        scores_json={"vulnerability": 7.8},
        matched_terms_json={"vulnerability": ["title:exploit"]},
        source_hash="hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    existing = ItemAIEnrichment(
        item_id=item.id,
        status="error",
        source_hash="old-hash",
        summary_text="Old summary",
        relevance_score=0.3,
        relevance_label="low",
        relevance_reasons_json=["Old reason"],
        provider="openai_compatible",
        model="old-model",
        error="Old failure",
    )
    _persist_feed_item(db_session, feed, item, article, classification, existing)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            company_name="Example Corp",
            company_stack=["Fortinet"],
            company_keywords=["vpn"],
        ),
    )
    db_session.add(settings)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json",
        lambda active, *, messages: AICompletionResult(
            payload={
                "summary_text": "New AI summary.",
                "relevance_score": 0.92,
                "relevance_reasons": ["Mentions Fortinet"],
            },
            provider="openai_compatible",
            model=active.model,
            latency_ms=50,
            prompt_tokens=60,
            completion_tokens=15,
            total_tokens=75,
        ),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()

    assert result.status == "ready"
    rows = db_session.scalars(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id)).all()
    assert len(rows) == 1
    assert rows[0].summary_text == "New AI summary."
    assert rows[0].model == "local-threat-model"
    assert rows[0].status == "ready"


def test_generate_daily_brief_persists_latest_brief_and_usage(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
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
        title="Edge devices under attack",
        summary="New attacks against edge devices were reported.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="cisa-1",
        content_hash="b" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity affected exposed edge devices and remote access systems.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            company_name="Example Corp",
            daily_brief_enabled=True,
            daily_brief_history_limit=2,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    captured_messages: list[dict[str, str]] = []

    def _fake_call(active, *, messages):
        _ = active
        captured_messages[:] = messages
        return AICompletionResult(
            payload={
                "title": "ThreatLens Daily Brief",
                "brief_text": "Edge-related activity stood out in the last day.",
                "key_points": ["Exposed edge systems were mentioned."],
                "recommended_actions": ["Review exposed remote access services."],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=82,
            prompt_tokens=70,
            completion_tokens=30,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    brief = generate_daily_brief(db_session, force=True)
    db_session.commit()

    assert brief is not None
    assert brief.status == "ready"
    assert brief.title == "ThreatLens Daily Brief"

    stored = db_session.scalar(select(AIDailyBrief).where(AIDailyBrief.id == brief.id))
    assert stored is not None
    assert stored.item_count == 1
    assert "Use only the provided company context and items." in captured_messages[0]["content"]
    request_payload = json.loads(captured_messages[1]["content"])
    assert request_payload["audience"] == "security leads and analysts preparing a daily triage and prioritization handoff"
    assert request_payload["requested_output"]["recommended_actions"].startswith("3-5 short, practical")
    assert request_payload["briefing_priorities"][2].startswith("Synthesis of overlapping stories")

    usage_events = db_session.scalars(select(AIUsageEvent)).all()
    assert len(usage_events) == 1
    assert usage_events[0].feature_type == "daily_brief"
    notification_event = db_session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.source_id == str(brief.id))
    )
    assert notification_event is not None
    assert notification_event.event_type == "daily_digest"
    assert notification_event.source_type == "ai_daily_brief"
    assert notification_event.payload_json["daily_brief"]["title"] == "ThreatLens Daily Brief"
    assert notification_event.payload_json["daily_brief"]["key_points"] == [
        "Exposed edge systems were mentioned."
    ]


def test_generate_daily_brief_extracts_text_from_object_lists(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
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
        source_guid="cisa-objects",
        url="https://example.com/articles/cisa-objects",
        canonical_url="https://example.com/articles/cisa-objects",
        title="Edge devices under attack",
        summary="New attacks against edge devices were reported.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="cisa-objects",
        content_hash="z" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity affected exposed edge devices and remote access systems.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    def _fake_call(active, *, messages):
        _ = (active, messages)
        return AICompletionResult(
            payload={
                "title": "ThreatLens Daily Brief",
                "brief_text": "Edge-related activity stood out in the last day.",
                "key_points": [
                    {"id": 1, "text": "Patch exposed edge systems."},
                    {"step": 2, "content": "Review remote access exposure."},
                ],
                "recommended_actions": [
                    {"id": 1, "action": "Rotate exposed credentials."},
                    '{"id": 2, "text": "Harden public-facing access flows."}',
                    "{'id': 3, 'message': 'Review exposed third-party integrations.'}",
                ],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=82,
            prompt_tokens=70,
            completion_tokens=30,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    brief = generate_daily_brief(db_session, force=True)
    db_session.commit()

    assert brief is not None
    assert brief.key_points_json == ["Patch exposed edge systems.", "Review remote access exposure."]
    assert brief.recommended_actions_json == [
        "Rotate exposed credentials.",
        "Harden public-facing access flows.",
        "Review exposed third-party integrations.",
    ]


def test_run_item_ai_enrichment_records_provider_exchange_event(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-debug-success",
        url="https://example.com/articles/unit42-debug-success",
        canonical_url="https://example.com/articles/unit42-debug-success",
        title="Threat activity targets edge access",
        summary="Researchers observed new activity against exposed access systems.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-debug-success",
        content_hash="e" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity targeted exposed access systems and edge devices.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json",
        lambda active, *, messages: AICompletionResult(
            payload={"summary_text": "summary", "relevance_score": 0.8, "relevance_reasons": ["edge systems"]},
            provider="openai_compatible",
            model=active.model,
            latency_ms=30,
            prompt_tokens=40,
            completion_tokens=10,
            total_tokens=50,
            request_url="http://localhost:11434/v1/chat/completions",
            request_payload={"model": active.model, "messages": messages},
            response_body='{"choices":[{"message":{"content":"{\\"summary_text\\":\\"summary\\"}"}}]}',
            response_json={"choices": [{"message": {"content": '{"summary_text":"summary"}'}}]},
            status_code=200,
        ),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "ready"
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange")
        .order_by(AITaskEvent.created_at.desc())
    )
    assert event is not None
    assert event.payload_json["request_url"] == "http://localhost:11434/v1/chat/completions"
    assert event.payload_json["status_code"] == 200
    assert event.payload_json["request_model"] == "local-threat-model"
    assert event.payload_json["request_message_count"] == 2
    assert event.payload_json["request_prompt_chars"] > 0
    assert event.payload_json["response_body_chars"] > 0
    assert event.payload_json["response_json_summary"]["top_level_keys"] == ["choices"]


def test_run_item_ai_enrichment_records_failed_provider_exchange_event(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-debug-failure",
        url="https://example.com/articles/unit42-debug-failure",
        canonical_url="https://example.com/articles/unit42-debug-failure",
        title="Threat activity targets edge access",
        summary="Researchers observed new activity against exposed access systems.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-debug-failure",
        content_hash="f" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity targeted exposed access systems and edge devices.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    def _raise_failure(active, *, messages):
        _ = (active, messages)
        raise AIIntegrationError(
            "AI request failed: provider 500",
            request_url="http://localhost:11434/v1/chat/completions",
            request_payload={"model": "local-threat-model", "messages": messages},
            response_body='{"error":"provider failed"}',
            response_json={"error": "provider failed"},
            status_code=500,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _raise_failure)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "error"
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_failed")
        .order_by(AITaskEvent.created_at.desc())
    )
    assert event is not None
    assert event.message == "AI request failed: provider 500"
    assert event.payload_json["status_code"] == 500
    assert event.payload_json["request_message_count"] == 2
    assert event.payload_json["response_body_chars"] == len('{"error":"provider failed"}')
    assert event.payload_json["response_json_summary"]["top_level_keys"] == ["error"]


def test_run_item_ai_enrichment_skips_before_provider_call_when_cancel_requested(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-cancel-before-call",
        url="https://example.com/articles/unit42-cancel-before-call",
        canonical_url="https://example.com/articles/unit42-cancel-before-call",
        title="Cancel before provider call",
        summary="This run should stop before calling the provider.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-cancel-before-call",
        content_hash="c" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity targeted exposed edge systems.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    run.reason = "cancel_requested"
    run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
    db_session.add(run)
    db_session.commit()

    def _unexpected_provider_call(active, *, messages):
        _ = (active, messages)
        raise AssertionError("provider should not be called after cancellation")

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _unexpected_provider_call)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    enrichment = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "cancel_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert result.status == "skipped"
    assert result.reason == "canceled"
    assert enrichment is None
    assert event is not None
    assert event.payload_json["stage"] == "before_pending_state"


def test_run_item_ai_enrichment_discards_provider_result_when_cancel_requested_midflight(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-cancel-midflight",
        url="https://example.com/articles/unit42-cancel-midflight",
        canonical_url="https://example.com/articles/unit42-cancel-midflight",
        title="Cancel after provider response",
        summary="This run should discard the provider result.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-cancel-midflight",
        content_hash="d" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity targeted edge devices and identity systems.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    def _cancel_during_provider_call(active, *, messages):
        _ = (active, messages)
        task_run = db_session.get(AITaskRun, run.id)
        assert task_run is not None
        task_run.reason = "cancel_requested"
        task_run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        db_session.add(task_run)
        db_session.flush()
        return AICompletionResult(
            payload={"summary_text": "summary", "relevance_score": 0.8, "relevance_reasons": ["edge systems"]},
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=30,
            prompt_tokens=40,
            completion_tokens=10,
            total_tokens=50,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _cancel_during_provider_call)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    enrichment = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "cancel_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert result.status == "skipped"
    assert result.reason == "canceled"
    assert enrichment is not None
    assert enrichment.status == "pending"
    assert enrichment.summary_text is None
    assert enrichment.relevance_label is None
    assert event is not None
    assert event.payload_json["stage"] == "after_provider_response"


def test_run_item_ai_enrichment_discards_result_after_claim_is_superseded(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-superseded-claim",
        url="https://example.com/articles/unit42-superseded-claim",
        canonical_url="https://example.com/articles/unit42-superseded-claim",
        title="Superseded provider claim",
        summary="A newer run should own the enrichment row.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-superseded-claim",
        content_hash="8" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity targeted edge devices and identity systems.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(base_url="http://localhost:11434/v1", model="local-threat-model"),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    def _supersede_claim_during_provider_call(active, *, messages):
        _ = (active, messages)
        assert not db_session.in_transaction()
        claim = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
        assert claim is not None
        db_session.execute(
            update(ItemAIEnrichment)
            .where(ItemAIEnrichment.item_id == item.id)
            .values(
                source_hash="newer-source-claim",
                updated_at=claim.updated_at + timedelta(seconds=1),
            )
        )
        db_session.commit()
        return AICompletionResult(
            payload={"summary_text": "stale summary", "relevance_score": 0.8, "relevance_reasons": ["stale"]},
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=30,
            prompt_tokens=40,
            completion_tokens=10,
            total_tokens=50,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _supersede_claim_during_provider_call)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    enrichment = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    discarded_event = db_session.scalar(
        select(AITaskEvent).where(
            AITaskEvent.task_run_id == run.id,
            AITaskEvent.event_type == "provider_result_discarded",
        )
    )
    assert result.status == "skipped"
    assert result.reason == "stale_result_discarded"
    assert enrichment is not None
    assert enrichment.status == "pending"
    assert enrichment.source_hash == "newer-source-claim"
    assert enrichment.summary_text is None
    assert discarded_event is not None
    assert discarded_event.payload_json["reason"] == "stale_claim"


def test_run_item_ai_enrichment_discards_provider_result_when_run_terminalizes_midflight(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/unit42.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="unit42-terminal-midflight",
        url="https://example.com/articles/unit42-terminal-midflight",
        canonical_url="https://example.com/articles/unit42-terminal-midflight",
        title="Terminalized after provider response",
        summary="This run should discard the provider result after terminalization.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unit42-terminal-midflight",
        content_hash="e" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Threat activity targeted edge devices and identity systems.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    def _terminalize_during_provider_call(active, *, messages):
        _ = (active, messages)
        task_run = db_session.get(AITaskRun, run.id)
        assert task_run is not None
        task_run.status = "error"
        task_run.reason = "stale_task_lost"
        task_run.error = "Task no longer appears in Celery and did not report completion"
        task_run.finished_at = datetime.now(timezone.utc)
        db_session.add(task_run)
        db_session.flush()
        return AICompletionResult(
            payload={"summary_text": "summary", "relevance_score": 0.8, "relevance_reasons": ["edge systems"]},
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=30,
            prompt_tokens=40,
            completion_tokens=10,
            total_tokens=50,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _terminalize_during_provider_call)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    enrichment = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "terminal_run_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert result.status == "skipped"
    assert result.reason == "stale_task_lost"
    assert enrichment is not None
    assert enrichment.status == "pending"
    assert enrichment.summary_text is None
    assert enrichment.relevance_label is None
    assert event is not None
    assert event.payload_json["stage"] == "after_provider_response"
    assert event.payload_json["resolved_reason"] == "stale_task_lost"


def test_run_item_ai_enrichment_recovers_from_extra_closing_brace_in_model_json(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-json-repair",
        url="https://example.com/articles/threat-post-json-repair",
        canonical_url="https://example.com/articles/threat-post-json-repair",
        title="Ransomware campaign expands against regional firms",
        summary="Researchers observed a ransomware group broadening its targets.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-json-repair",
        content_hash="9" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="A ransomware group expanded operations against regional businesses and used custom tooling.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1774613335,
        "model": "local-threat-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"summary_text":"AI summary of the campaign.","relevance_score":0.82,'
                        '"relevance_reasons":["Ransomware activity","Regional firms targeted"]}}'
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
    monkeypatch.setattr(
        "app.services.ai_integration.build_safe_http_client",
        lambda *args, **kwargs: _fake_httpx_client_factory(payload)(*args, **kwargs),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "ready"
    stored = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    assert stored is not None
    assert stored.status == "ready"
    assert stored.summary_text == "AI summary of the campaign."
    assert stored.relevance_label == "high"


def test_run_item_ai_enrichment_recovers_from_missing_closing_brace_in_model_json(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-json-balance",
        url="https://example.com/articles/threat-post-json-balance",
        canonical_url="https://example.com/articles/threat-post-json-balance",
        title="Keylogger and backdoor target regional finance",
        summary="Researchers identified a backdoor and keylogger aimed at a financial institution.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-json-balance",
        content_hash="6" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers identified custom malware targeting a financial institution with keylogging and USB propagation.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1774613335,
        "model": "local-threat-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"summary_text":"AI summary of the malware.","relevance_score":0.82,'
                        '"relevance_reasons":["Financial institution target","Custom malware components"]'
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
    monkeypatch.setattr(
        "app.services.ai_integration.build_safe_http_client",
        lambda *args, **kwargs: _fake_httpx_client_factory(payload)(*args, **kwargs),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "ready"
    stored = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    assert stored is not None
    assert stored.status == "ready"
    assert stored.summary_text == "AI summary of the malware."
    assert stored.relevance_label == "high"


def test_run_item_ai_enrichment_records_parse_failure_context(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-parse-failure",
        url="https://example.com/articles/threat-post-parse-failure",
        canonical_url="https://example.com/articles/threat-post-parse-failure",
        title="Malformed structured output from local model",
        summary="The local model returned malformed structured data.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-parse-failure",
        content_hash="8" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="A local language model returned malformed structured output for a security article.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1774613335,
        "model": "local-threat-model",
        "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "summary_text: malformed response",
                    },
                    "finish_reason": "stop",
                }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
    monkeypatch.setattr(
        "app.services.ai_integration.build_safe_http_client",
        lambda *args, **kwargs: _fake_httpx_client_factory(payload)(*args, **kwargs),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "error"
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_failed")
        .order_by(AITaskEvent.created_at.desc())
    )
    assert event is not None
    assert event.message == "AI response did not contain valid JSON"
    assert event.payload_json["request_url"] == "http://localhost:11434/v1/chat/completions"
    assert event.payload_json["request_model"] == "local-threat-model"
    assert event.payload_json["status_code"] == 200
    assert event.payload_json["response_json_summary"]["top_level_keys"] == ["choices", "created", "id", "model", "object", "usage"]
    assert event.payload_json["response_json_summary"]["choices_count"] == 1


def test_run_item_ai_enrichment_retries_after_malformed_model_output(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-retry",
        url="https://example.com/articles/threat-post-retry",
        canonical_url="https://example.com/articles/threat-post-retry",
        title="Retry malformed model output",
        summary="The first model response is malformed but a retry succeeds.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-retry",
        content_hash="7" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="The first AI response is malformed and the second one is valid JSON.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=1,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    invalid_payload = {
        "id": "chatcmpl-invalid",
        "object": "chat.completion",
        "created": 1774613335,
        "model": "local-threat-model",
        "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "summary_text: malformed response",
                    },
                    "finish_reason": "stop",
                }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
    valid_payload = {
        "id": "chatcmpl-valid",
        "object": "chat.completion",
        "created": 1774613336,
        "model": "local-threat-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"summary_text":"Retry succeeded.","relevance_score":0.7,'
                        '"relevance_reasons":["Retry returned valid JSON"]}'
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 110,
            "completion_tokens": 24,
            "total_tokens": 134,
        },
    }
    fake_client_factory = _fake_httpx_client_sequence_factory([invalid_payload, valid_payload])
    monkeypatch.setattr(
        "app.services.ai_integration.build_safe_http_client",
        lambda *args, **kwargs: fake_client_factory(*args, **kwargs),
    )

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "ready"
    retry_event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_retry")
        .order_by(AITaskEvent.created_at.asc())
    )
    assert retry_event is not None
    assert retry_event.payload_json["attempt"] == 1
    assert retry_event.payload_json["max_attempts"] == 2

    usage_events = db_session.scalars(
        select(AIUsageEvent)
        .where(AIUsageEvent.item_id == item.id)
        .order_by(AIUsageEvent.created_at.asc())
    ).all()
    assert [event.success for event in usage_events] == [False, True]


def test_run_item_ai_enrichment_applies_backoff_and_jitter_between_provider_retries(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post-backoff.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-backoff",
        url="https://example.com/articles/threat-post-backoff",
        canonical_url="https://example.com/articles/threat-post-backoff",
        title="Retry with backoff",
        summary="The provider fails once and retries after a delay.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-backoff",
        content_hash="8" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="The provider should pause before the second attempt.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=1,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    attempts = {"count": 0}
    sleep_calls: list[float] = []

    def _fail_once_then_succeed(active, *, messages):
        _ = (active, messages)
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise AIIntegrationError("provider timeout", retryable=True)
        return AICompletionResult(
            payload={
                "summary_text": "Retry succeeded.",
                "relevance_score": 0.62,
                "relevance_reasons": ["Retry completed after backoff"],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=55,
            prompt_tokens=30,
            completion_tokens=12,
            total_tokens=42,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fail_once_then_succeed)
    monkeypatch.setattr("app.services.ai_integration.random.uniform", lambda _start, _end: 0.25)
    monkeypatch.setattr("app.services.ai_integration.time.sleep", lambda delay: sleep_calls.append(delay))

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    retry_event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_retry")
        .order_by(AITaskEvent.created_at.asc())
    )

    assert attempts["count"] == 2
    assert result.status == "ready"
    assert sleep_calls == [pytest.approx(0.75)]
    assert retry_event is not None
    assert retry_event.payload_json["retry_delay_seconds"] == pytest.approx(0.75, rel=1e-3)


def test_run_item_ai_enrichment_does_not_retry_terminal_provider_auth_errors(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post-auth-error.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-auth-error",
        url="https://example.com/articles/threat-post-auth-error",
        canonical_url="https://example.com/articles/threat-post-auth-error",
        title="Terminal auth error",
        summary="Provider credentials are invalid.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-auth-error",
        content_hash="9" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="The provider rejects this request with an authentication failure.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=3,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    attempts = {"count": 0}

    def _always_auth_error(active, *, messages):
        _ = (active, messages)
        attempts["count"] += 1
        raise AIIntegrationError("provider rejected credentials", status_code=401, retryable=False)

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _always_auth_error)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    retry_events = db_session.scalars(
        select(AITaskEvent).where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_retry")
    ).all()
    failure_event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_failed")
        .order_by(AITaskEvent.created_at.asc())
    )

    assert attempts["count"] == 1
    assert result.status == "error"
    assert retry_events == []
    assert failure_event is not None
    assert failure_event.payload_json["status_code"] == 401
    assert failure_event.payload_json["retryable"] is False


def test_run_item_ai_enrichment_stops_before_retry_when_cancel_requested_after_failure(
    db_session,
    ai_enabled_env,
    monkeypatch: pytest.MonkeyPatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Threat Post",
        url="https://example.com/threat-post.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="threat-post-retry-cancel",
        url="https://example.com/articles/threat-post-retry-cancel",
        canonical_url="https://example.com/articles/threat-post-retry-cancel",
        title="Retry canceled after first failure",
        summary="The run should stop before a second provider attempt.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="threat-post-retry-cancel",
        content_hash="8" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="The provider fails once and the task is canceled before retry.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=1,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    db_session.commit()

    attempts = {"count": 0}

    def _fail_then_cancel(active, *, messages):
        _ = (active, messages)
        attempts["count"] += 1
        if attempts["count"] > 1:
            raise AssertionError("provider retry should not run after cancellation")
        task_run = db_session.get(AITaskRun, run.id)
        assert task_run is not None
        task_run.reason = "cancel_requested"
        task_run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        db_session.add(task_run)
        db_session.flush()
        raise AIIntegrationError("provider timeout", retryable=True)

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fail_then_cancel)

    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()

    enrichment = db_session.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id))
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "cancel_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert attempts["count"] == 1
    assert result.status == "skipped"
    assert result.reason == "canceled"
    assert enrichment is not None
    assert enrichment.status == "pending"
    assert event is not None
    assert event.payload_json["stage"] == "before_provider_retry"


def test_run_daily_brief_generation_retries_after_truncated_model_output(
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
        source_guid="daily-brief-retry",
        url="https://example.com/articles/daily-brief-retry",
        canonical_url="https://example.com/articles/daily-brief-retry",
        title="Critical edge infrastructure exposure",
        summary="A critical edge exposure requires immediate review.",
        published_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        dedupe_key="daily-brief-retry",
        content_hash="8" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Citrix and F5 edge exposures require immediate patching and review.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            request_max_retries=1,
            max_completion_tokens=700,
            daily_brief_enabled=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    db_session.commit()

    invalid_payload = {
        "id": "chatcmpl-invalid-daily-brief",
        "object": "chat.completion",
        "created": 1774739300,
        "model": "local-threat-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        '{'
                        '"title":"Daily Security Brief – March 28, 2026",'
                        '"brief_text":"Critical vulnerabilities require immediate action.",'
                        '"key_points":["Citrix NetScaler is under active recon.","F5 BIG-IP APM is in KEV.","TeamPCP pushed malicious telnyx versions (4'
                    ),
                },
                "finish_reason": "length",
            }
        ],
        "usage": {
            "prompt_tokens": 2500,
            "completion_tokens": 700,
            "total_tokens": 3200,
        },
    }
    valid_payload = {
        "id": "chatcmpl-valid-daily-brief",
        "object": "chat.completion",
        "created": 1774739301,
        "model": "local-threat-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"title":"Daily Security Brief – March 28, 2026",'
                        '"brief_text":"Critical vulnerabilities require immediate action.",'
                        '"key_points":["Citrix NetScaler is under active recon.","F5 BIG-IP APM is in KEV."],'
                        '"recommended_actions":["Patch exposed edge systems immediately.","Review third-party packages for compromise."]}'
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 2500,
            "completion_tokens": 320,
            "total_tokens": 2820,
        },
    }
    fake_client_factory = _fake_httpx_client_sequence_factory([invalid_payload, valid_payload])
    monkeypatch.setattr(
        "app.services.ai_integration.build_safe_http_client",
        lambda *args, **kwargs: fake_client_factory(*args, **kwargs),
    )

    result = run_daily_brief_generation(db_session, force=True, task_run_id=run.id)
    db_session.commit()

    assert result.status == "ready"
    assert result.brief is not None
    assert result.brief.key_points_json == [
        "Citrix NetScaler is under active recon.",
        "F5 BIG-IP APM is in KEV.",
    ]

    retry_event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "provider_exchange_retry")
        .order_by(AITaskEvent.created_at.asc())
    )
    assert retry_event is not None
    assert retry_event.payload_json["attempt"] == 1
    assert retry_event.payload_json["max_attempts"] == 2
    assert retry_event.payload_json["retry_hint"] == "expand_completion_budget"
    assert retry_event.payload_json["requested_max_tokens"] == 700
    assert retry_event.payload_json["next_max_tokens"] > 700

    usage_events = db_session.scalars(
        select(AIUsageEvent)
        .where(AIUsageEvent.daily_brief_id == result.brief.id)
        .order_by(AIUsageEvent.created_at.asc())
    ).all()
    assert [event.success for event in usage_events] == [False, True]


def test_run_daily_brief_generation_stops_before_retry_when_cancel_requested_after_failure(
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
        source_guid="daily-brief-retry-cancel",
        url="https://example.com/articles/daily-brief-retry-cancel",
        canonical_url="https://example.com/articles/daily-brief-retry-cancel",
        title="Retry canceled after first failure",
        summary="The daily brief should stop before a second provider attempt.",
        published_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        dedupe_key="daily-brief-retry-cancel",
        content_hash="9" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="The provider fails once and the daily brief is canceled before retry.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
            request_max_retries=1,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    db_session.commit()

    attempts = {"count": 0}

    def _fail_then_cancel(active, *, messages):
        _ = (active, messages)
        attempts["count"] += 1
        if attempts["count"] > 1:
            raise AssertionError("provider retry should not run after cancellation")
        task_run = db_session.get(AITaskRun, run.id)
        assert task_run is not None
        task_run.reason = "cancel_requested"
        task_run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        db_session.add(task_run)
        db_session.flush()
        raise AIIntegrationError("provider timeout", retryable=True)

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fail_then_cancel)

    result = run_daily_brief_generation(db_session, force=True, task_run_id=run.id)
    db_session.commit()

    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "cancel_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert attempts["count"] == 1
    assert result.status == "skipped"
    assert result.reason == "canceled"
    assert result.brief is not None
    assert result.brief.status == "pending"
    assert event is not None
    assert event.payload_json["stage"] == "before_provider_retry"


def test_run_daily_brief_generation_discards_provider_result_when_cancel_requested_midflight(
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
        source_guid="daily-brief-cancel-midflight",
        url="https://example.com/articles/daily-brief-cancel-midflight",
        canonical_url="https://example.com/articles/daily-brief-cancel-midflight",
        title="Critical edge infrastructure exposure",
        summary="A critical edge exposure requires immediate review.",
        published_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        dedupe_key="daily-brief-cancel-midflight",
        content_hash="7" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Citrix and F5 edge exposures require immediate patching and review.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    db_session.commit()

    def _cancel_during_provider_call(active, *, messages):
        _ = (active, messages)
        task_run = db_session.get(AITaskRun, run.id)
        assert task_run is not None
        task_run.reason = "cancel_requested"
        task_run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        db_session.add(task_run)
        db_session.flush()
        return AICompletionResult(
            payload={
                "title": "ThreatLens Daily Brief",
                "brief_text": "Critical vulnerabilities require immediate action.",
                "key_points": ["Citrix NetScaler is under active recon."],
                "recommended_actions": ["Patch exposed edge systems immediately."],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=82,
            prompt_tokens=70,
            completion_tokens=30,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _cancel_during_provider_call)

    result = run_daily_brief_generation(db_session, force=True, task_run_id=run.id)
    db_session.commit()

    task_run = db_session.get(AITaskRun, run.id)
    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "cancel_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert result.status == "skipped"
    assert result.reason == "canceled"
    assert result.brief is not None
    assert result.brief.status == "pending"
    assert result.brief.title is None
    assert task_run is not None
    assert task_run.daily_brief_id == result.brief.id
    assert event is not None
    assert event.payload_json["stage"] == "after_provider_response"


def test_run_daily_brief_generation_discards_provider_result_when_run_terminalizes_midflight(
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
        source_guid="daily-brief-terminal-midflight",
        url="https://example.com/articles/daily-brief-terminal-midflight",
        canonical_url="https://example.com/articles/daily-brief-terminal-midflight",
        title="Critical edge infrastructure exposure",
        summary="A critical edge exposure requires immediate review.",
        published_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        dedupe_key="daily-brief-terminal-midflight",
        content_hash="6" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Citrix and F5 edge exposures require immediate patching and review.",
        extraction_method="readable",
    )
    _persist_feed_item(db_session, feed, item, article)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
    )
    db_session.commit()

    def _terminalize_during_provider_call(active, *, messages):
        _ = (active, messages)
        task_run = db_session.get(AITaskRun, run.id)
        assert task_run is not None
        task_run.status = "error"
        task_run.reason = "stale_task_lost"
        task_run.error = "Task no longer appears in Celery and did not report completion"
        task_run.finished_at = datetime.now(timezone.utc)
        db_session.add(task_run)
        db_session.flush()
        return AICompletionResult(
            payload={
                "title": "ThreatLens Daily Brief",
                "brief_text": "Critical vulnerabilities require immediate action.",
                "key_points": ["Citrix NetScaler is under active recon."],
                "recommended_actions": ["Patch exposed edge systems immediately."],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=82,
            prompt_tokens=70,
            completion_tokens=30,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _terminalize_during_provider_call)

    result = run_daily_brief_generation(db_session, force=True, task_run_id=run.id)
    db_session.commit()

    event = db_session.scalar(
        select(AITaskEvent)
        .where(AITaskEvent.task_run_id == run.id, AITaskEvent.event_type == "terminal_run_observed")
        .order_by(AITaskEvent.created_at.desc())
    )

    assert result.status == "skipped"
    assert result.reason == "stale_task_lost"
    assert result.brief is not None
    assert result.brief.status == "pending"
    assert result.brief.title is None
    assert event is not None
    assert event.payload_json["stage"] == "after_provider_response"
    assert event.payload_json["resolved_reason"] == "stale_task_lost"


def test_generate_daily_brief_prunes_history_to_configured_limit(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.flush()

    for index in range(3):
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"cisa-{index}",
            url=f"https://example.com/articles/cisa-{index}",
            canonical_url=f"https://example.com/articles/cisa-{index}",
            title=f"Edge devices under attack {index}",
            summary="New attacks against edge devices were reported.",
            published_at=datetime(2026, 3, 20 + index, 10, 0, tzinfo=timezone.utc),
            dedupe_key=f"cisa-{index}",
            content_hash=str(index + 1) * 64,
            status="content_fetched",
            first_seen_at=datetime(2026, 3, 20 + index, 10, 0, tzinfo=timezone.utc),
        )
        db_session.add(item)
        db_session.flush()
        article = Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Threat activity affected exposed edge devices and remote access systems.",
            extraction_method="readable",
        )
        db_session.add(article)

    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
            daily_brief_history_limit=2,
            daily_brief_max_items=10,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    def _fake_call(active, *, messages):
        _ = (active, messages)
        return AICompletionResult(
            payload={
                "title": "ThreatLens Daily Brief",
                "brief_text": "Edge-related activity stood out in the last day.",
                "key_points": ["Exposed edge systems were mentioned."],
                "recommended_actions": ["Review exposed remote access services."],
            },
            provider="openai_compatible",
            model="local-threat-model",
            latency_ms=82,
            prompt_tokens=70,
            completion_tokens=30,
            total_tokens=100,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", _fake_call)

    for day in range(3):
        brief = generate_daily_brief(
            db_session,
            force=True,
            reference_time=datetime(2026, 3, 20 + day, 12, 0, tzinfo=timezone.utc),
        )
        assert brief is not None
        db_session.commit()

    brief_dates = list(db_session.scalars(select(AIDailyBrief.brief_date).order_by(AIDailyBrief.brief_date.desc())))
    assert brief_dates == [datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc).date(), datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc).date()]


def test_get_latest_daily_brief_returns_most_recent_ready_brief(db_session):
    ready_brief = AIDailyBrief(
        brief_date=datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc).date(),
        status="ready",
        window_start=datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
        title="Ready brief",
        brief_text="Ready brief text",
        generated_at=datetime(2026, 3, 25, 0, 5, tzinfo=timezone.utc),
    )
    failed_brief = AIDailyBrief(
        brief_date=datetime(2026, 3, 26, 0, 0, tzinfo=timezone.utc).date(),
        status="error",
        window_start=datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 3, 26, 0, 0, tzinfo=timezone.utc),
        error="provider unavailable",
        generated_at=datetime(2026, 3, 26, 0, 5, tzinfo=timezone.utc),
    )
    db_session.add_all([ready_brief, failed_brief])
    db_session.commit()

    latest = get_latest_daily_brief(db_session)

    assert latest is not None
    assert latest.id == ready_brief.id


def test_prompt_builders_include_saved_custom_instructions(db_session, ai_enabled_env):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            global_instructions="Always stay concise.",
            item_summary_instructions="Lead with analyst impact.",
            relevance_instructions="Prioritize identity systems.",
            daily_brief_instructions="Write for a SOC handoff.",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    active = load_active_ai_settings(db_session)
    item_prompt = build_item_enrichment_system_prompt(active)
    daily_prompt = build_daily_brief_system_prompt(active)

    assert "Always stay concise." in item_prompt
    assert "Summary instructions: Lead with analyst impact." in item_prompt
    assert "Relevance instructions: Prioritize identity systems." in item_prompt
    assert "Always stay concise." in daily_prompt
    assert "Daily brief instructions: Write for a SOC handoff." in daily_prompt


def test_prompt_builders_allow_editable_base_prompts(db_session, ai_enabled_env):
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            item_enrichment_system_prompt="You are a focused SOC analyst. Return JSON only.",
            daily_brief_system_prompt="You are writing a crisp morning brief. Return JSON only.",
            global_instructions="Keep every answer under 120 words.",
        ),
    )
    db_session.add(settings)
    db_session.commit()

    active = load_active_ai_settings(db_session)

    assert build_item_enrichment_system_prompt(active).startswith("You are a focused SOC analyst.")
    assert build_daily_brief_system_prompt(active).startswith("You are writing a crisp morning brief.")
    assert "arrays of short plain strings only" in build_daily_brief_system_prompt(active)
    assert DEFAULT_ITEM_ENRICHMENT_SYSTEM_PROMPT != active.item_enrichment_system_prompt
    assert DEFAULT_DAILY_BRIEF_SYSTEM_PROMPT != active.daily_brief_system_prompt


def test_default_prompts_emphasize_grounding_and_security_audience():
    assert "Use only the provided article text" in DEFAULT_ITEM_ENRICHMENT_SYSTEM_PROMPT
    assert "Approximate relevance rubric" in DEFAULT_ITEM_ENRICHMENT_SYSTEM_PROMPT
    assert "Write for security leads and analysts" in DEFAULT_DAILY_BRIEF_SYSTEM_PROMPT
    assert "Recommended actions must be brief, practical, and evidence-based." in DEFAULT_DAILY_BRIEF_SYSTEM_PROMPT
