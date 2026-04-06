import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_integration import AICompletionResult
from app.services.ai_ops import AI_TASK_TYPE_ITEM_ENRICHMENT, AI_TASK_TYPE_REPROCESS, AI_TRIGGER_MANUAL, queue_ai_task_run
from app.schemas.ai import AISettingsUpdate
from app.tasks.feed_tasks import (
    _scheduled_daily_ai_brief_due,
    backfill_feed_metadata,
    classify_item,
    fetch_article,
    fetch_feed,
    generate_item_ai_enrichment_task,
    reprocess_recent_ai_items,
)


def test_fetch_feed_skips_when_feed_is_no_longer_due(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        last_fetch_at=datetime.now(timezone.utc),
    )
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _feed_lock_override(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.safe_stream_with_redirects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network fetch should not run for non-due feeds")),
    )

    result = fetch_feed.run(str(feed.id))

    assert result == {"status": "skipped", "reason": "not_due", "feed_id": str(feed.id)}


def test_fetch_feed_force_bypasses_due_check(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        last_fetch_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _feed_lock_override(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    class _Response:
        status_code = 304
        headers: dict[str, str] = {}
        url = "https://example.com/feed.xml"

        def iter_bytes(self):
            yield b""

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)
    monkeypatch.setattr("app.tasks.feed_tasks.httpx.Client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())

    result = fetch_feed.run(str(feed.id), force=True)

    assert result == {"status": "not_modified", "feed_id": str(feed.id)}


def test_fetch_feed_rejects_invalid_feed_ids(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _feed_lock_override(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)

    result = fetch_feed.run("not-a-uuid")

    assert result == {"status": "skipped", "reason": "invalid_feed_id", "feed_id": "not-a-uuid"}


def test_backfill_feed_metadata_rejects_invalid_feed_ids(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _feed_lock_override(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)

    result = backfill_feed_metadata.run("not-a-uuid")

    assert result == {"status": "skipped", "reason": "invalid_feed_id", "feed_id": "not-a-uuid"}


def test_fetch_article_rejects_invalid_item_ids(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = fetch_article.run("not-a-uuid")

    assert result == {"status": "skipped", "reason": "invalid_item_id", "item_id": "not-a-uuid"}


def test_classify_item_queues_ai_enrichment_when_enabled(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="item-1",
        url="https://example.com/articles/1",
        canonical_url="https://example.com/articles/1",
        title="Fortinet exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="item-1",
        content_hash="a" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed Fortinet exploitation in the wild.",
        extraction_method="readable",
    )
    db_session.add_all([feed, item, article])
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    captured: dict[str, str] = {}

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.load_active_ai_settings",
        lambda _db: type(
            "ActiveAISettings",
            (),
            {
                "ai_enabled": True,
                "ai_configured": True,
                "auto_enrich_new_items": True,
            },
        )(),
    )
    monkeypatch.setattr("app.tasks.feed_tasks.dispatch_alert_match_notification_webhooks.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.tasks.feed_tasks.extract_item_iocs.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda item_id, force=False, task_run_id=None: captured.update({"item_id": item_id, "force": str(force), "task_run_id": str(task_run_id or "")}),
    )

    result = classify_item.run(str(item.id))

    assert result["status"] == "ok"
    assert captured["item_id"] == str(item.id)
    assert captured["force"] == "False"
    assert captured["task_run_id"]


def test_reprocess_recent_ai_items_tracks_parent_progress(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)

    item_ids: list[uuid.UUID] = []
    for index in range(2):
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"item-{index}",
            url=f"https://example.com/articles/{index}",
            canonical_url=f"https://example.com/articles/{index}",
            title=f"Fortinet exploitation observed {index}",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
            dedupe_key=f"item-{index}",
            content_hash=str(index + 1) * 64,
            status="content_fetched",
        )
        article = Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Researchers observed Fortinet exploitation in the wild.",
            extraction_method="readable",
        )
        db_session.add_all([item, article])
        item_ids.append(item.id)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            daily_brief_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json",
        lambda active, *, messages: AICompletionResult(
            payload={
                "summary_text": "AI summary.",
                "relevance_score": 0.9,
                "relevance_reasons": ["Mentions Fortinet"],
            },
            provider=active.provider_type,
            model=active.model,
            latency_ms=25,
            prompt_tokens=30,
            completion_tokens=10,
            total_tokens=40,
        ),
    )

    scheduled: list[tuple[str, bool, str | None]] = []

    class _FakeTask:
        def __init__(self, task_id: str):
            self.id = task_id

    def _fake_delay(item_id: str, force: bool = False, task_run_id: str | None = None):
        scheduled.append((item_id, force, task_run_id))
        return _FakeTask(f"child-{len(scheduled)}")

    monkeypatch.setattr("app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay", _fake_delay)

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 2},
    )
    db_session.commit()

    result = reprocess_recent_ai_items.run(7, 2, task_run_id=str(parent_run.id))
    assert result["queued"] == 2

    child_runs = db_session.scalars(
        select(AITaskRun)
        .where(AITaskRun.parent_run_id == parent_run.id, AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT)
        .order_by(AITaskRun.created_at.asc())
    ).all()
    assert len(child_runs) == 2

    for child_run in child_runs:
        generate_item_ai_enrichment_task.run(str(child_run.item_id), force=True, task_run_id=str(child_run.id))

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert refreshed_parent is not None
    assert refreshed_parent.target_count == 2
    assert refreshed_parent.processed_count == 2
    assert refreshed_parent.success_count == 2
    assert refreshed_parent.error_count == 0
    assert refreshed_parent.status == "ready"
    get_settings.cache_clear()


def test_generate_item_ai_enrichment_task_marks_unexpected_failures_on_task_runs(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_item_ai_enrichment",
        lambda db, *, item_id, force=False, task_run_id=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 1, "limit": 1},
    )
    parent_run.target_count = 1
    db_session.add(parent_run)
    child_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        parent_run_id=parent_run.id,
        item_id=uuid.uuid4(),
        metadata={"parent_task": "reprocess"},
    )
    db_session.commit()

    result = generate_item_ai_enrichment_task.run(str(child_run.item_id), force=True, task_run_id=str(child_run.id))

    assert result == {"status": "error", "reason": "unexpected_error", "item_id": str(child_run.item_id)}

    db_session.expire_all()
    refreshed_child = db_session.scalar(select(AITaskRun).where(AITaskRun.id == child_run.id))
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))

    assert refreshed_child is not None
    assert refreshed_child.status == "error"
    assert refreshed_child.reason == "unexpected_error"
    assert refreshed_child.error == "boom"

    assert refreshed_parent is not None
    assert refreshed_parent.processed_count == 1
    assert refreshed_parent.error_count == 1
    assert refreshed_parent.status == "error"


def test_reprocess_recent_ai_items_can_target_specific_items(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)

    item_ids: list[uuid.UUID] = []
    for index in range(3):
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"specific-{index}",
            url=f"https://example.com/articles/specific-{index}",
            canonical_url=f"https://example.com/articles/specific-{index}",
            title=f"Targeted article {index}",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
            dedupe_key=f"specific-{index}",
            content_hash=str(index + 3) * 64,
            status="content_fetched",
        )
        article = Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Researchers observed Fortinet exploitation in the wild.",
            extraction_method="readable",
        )
        db_session.add_all([item, article])
        item_ids.append(item.id)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            daily_brief_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    scheduled: list[tuple[str, bool, str | None]] = []

    class _FakeTask:
        def __init__(self, task_id: str):
            self.id = task_id

    def _fake_delay(item_id: str, force: bool = False, task_run_id: str | None = None):
        scheduled.append((item_id, force, task_run_id))
        return _FakeTask(f"child-{len(scheduled)}")

    monkeypatch.setattr("app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay", _fake_delay)

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": None, "limit": 100},
    )
    db_session.commit()

    result = reprocess_recent_ai_items.run(
        None,
        100,
        None,
        None,
        None,
        [str(item_ids[2]), str(item_ids[0])],
        task_run_id=str(parent_run.id),
    )

    assert result["queued"] == 2
    assert [scheduled_item_id for scheduled_item_id, _force, _task_run_id in scheduled] == [str(item_ids[2]), str(item_ids[0])]

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert refreshed_parent is not None
    assert refreshed_parent.target_count == 2
    get_settings.cache_clear()


def test_scheduled_daily_ai_brief_due_respects_configured_time(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
            daily_brief_schedule_hour_utc=12,
            daily_brief_schedule_minute_utc=30,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    before_due, before_reason = _scheduled_daily_ai_brief_due(
        db_session,
        now=datetime(2026, 3, 27, 12, 29, tzinfo=timezone.utc),
    )
    after_due, after_reason = _scheduled_daily_ai_brief_due(
        db_session,
        now=datetime(2026, 3, 27, 12, 30, tzinfo=timezone.utc),
    )

    assert before_due is False
    assert before_reason == "scheduled_time_not_reached"
    assert after_due is True
    assert after_reason is None
    get_settings.cache_clear()


def test_scheduled_daily_ai_brief_due_skips_after_today_ready_brief(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            daily_brief_enabled=True,
            daily_brief_schedule_hour_utc=9,
            daily_brief_schedule_minute_utc=0,
        ),
    )
    db_session.add(settings)
    db_session.add(
        AIDailyBrief(
            id=uuid.uuid4(),
            brief_date=datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc).date(),
            window_start=datetime(2026, 3, 26, 9, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc),
            status="ready",
            item_count=4,
            generated_at=datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    due, reason = _scheduled_daily_ai_brief_due(
        db_session,
        now=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
    )

    assert due is False
    assert reason == "already_generated"
    get_settings.cache_clear()
