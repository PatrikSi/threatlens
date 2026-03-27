import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
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
    generate_daily_brief,
    generate_item_ai_enrichment,
    get_latest_daily_brief,
    run_item_ai_enrichment,
)
from app.services.ai_ops import AI_TASK_TYPE_ITEM_ENRICHMENT, AI_TRIGGER_MANUAL, queue_ai_task_run
from app.schemas.ai import AISettingsUpdate


@pytest.fixture()
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


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
    db_session.add_all([feed, item, article, classification])
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
    db_session.add_all([feed, item, article, classification])
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
    db_session.add_all([feed, item, article, classification, existing])
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
    db_session.add_all([feed, item, article])
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

    brief = generate_daily_brief(db_session, force=True)
    db_session.commit()

    assert brief is not None
    assert brief.status == "ready"
    assert brief.title == "ThreatLens Daily Brief"

    stored = db_session.scalar(select(AIDailyBrief).where(AIDailyBrief.id == brief.id))
    assert stored is not None
    assert stored.item_count == 1

    usage_events = db_session.scalars(select(AIUsageEvent)).all()
    assert len(usage_events) == 1
    assert usage_events[0].feature_type == "daily_brief"


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
    db_session.add_all([feed, item, article])
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
    assert "request_payload" in event.payload_json
    assert "response_body" in event.payload_json


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
    db_session.add_all([feed, item, article])
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
    assert event.payload_json["response_body"] == '{"error":"provider failed"}'


def test_generate_daily_brief_prunes_history_to_configured_limit(db_session, ai_enabled_env, monkeypatch: pytest.MonkeyPatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="CISA",
        url="https://example.com/cisa.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)

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
        article = Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Threat activity affected exposed edge devices and remote access systems.",
            extraction_method="readable",
        )
        db_session.add_all([item, article])

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
    assert DEFAULT_ITEM_ENRICHMENT_SYSTEM_PROMPT != active.item_enrichment_system_prompt
    assert DEFAULT_DAILY_BRIEF_SYSTEM_PROMPT != active.daily_brief_system_prompt
