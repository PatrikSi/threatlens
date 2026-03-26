import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.tasks.feed_tasks import classify_item, fetch_feed


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
        lambda item_id, force=False: captured.update({"item_id": item_id, "force": str(force)}),
    )

    result = classify_item.run(str(item.id))

    assert result["status"] == "ok"
    assert captured == {"item_id": str(item.id), "force": "False"}
