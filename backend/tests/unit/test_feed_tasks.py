import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.alert_interest import AlertInterest
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_integration import AICompletionResult
from app.services.ai_ops import AI_TASK_TYPE_ITEM_ENRICHMENT, AI_TASK_TYPE_REPROCESS, AI_TRIGGER_MANUAL, queue_ai_task_run
from app.services.safe_fetch import RedirectError
from app.schemas.ai import AISettingsUpdate
from app.tasks.feed_tasks import (
    _scheduled_daily_ai_brief_due,
    _queue_item_ai_enrichment_run,
    backfill_feed_metadata,
    classify_item,
    dispatch_due_feeds,
    dispatch_items_missing_articles,
    enqueue_notification_webhook_delivery_processing,
    fetch_article,
    fetch_feed,
    generate_item_ai_enrichment_task,
    reprocess_recent_ai_items,
)


@pytest.fixture(autouse=True)
def _allow_private_network_ai(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_core_pipeline_tasks_ack_late_and_reject_on_worker_loss():
    assert backfill_feed_metadata.acks_late is True
    assert backfill_feed_metadata.reject_on_worker_lost is True
    assert fetch_feed.acks_late is True
    assert fetch_feed.reject_on_worker_lost is True
    assert fetch_article.acks_late is True
    assert fetch_article.reject_on_worker_lost is True
    assert classify_item.acks_late is True
    assert classify_item.reject_on_worker_lost is True
    assert generate_item_ai_enrichment_task.acks_late is True
    assert generate_item_ai_enrichment_task.reject_on_worker_lost is True
    assert reprocess_recent_ai_items.acks_late is True
    assert reprocess_recent_ai_items.reject_on_worker_lost is True


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
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())

    result = fetch_feed.run(str(feed.id), force=True)

    assert result == {"status": "not_modified", "feed_id": str(feed.id)}


def test_dispatch_due_feeds_claims_due_feed_until_worker_clears_it(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Claimed Feed",
        url="https://example.com/claim.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        last_fetch_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(feed)
    db_session.commit()

    queued_feed_ids: list[str] = []

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.fetch_feed.delay", lambda feed_id: queued_feed_ids.append(feed_id))
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_due_feeds_batch_size", 10)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_feed_claim_seconds", 900)

    first = dispatch_due_feeds.run()
    second = dispatch_due_feeds.run()

    assert first == {"queued": 1}
    assert second == {"queued": 0}
    assert queued_feed_ids == [str(feed.id)]
    db_session.refresh(feed)
    assert feed.dispatch_claimed_at is not None
    assert feed.dispatch_backoff_until is not None


def test_dispatch_due_feeds_releases_claim_when_enqueue_fails(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Enqueue Failure Feed",
        url="https://example.com/failure.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        last_fetch_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_feed.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    result = dispatch_due_feeds.run()

    assert result == {"queued": 0}
    db_session.refresh(feed)
    assert feed.dispatch_claimed_at is None
    assert feed.dispatch_backoff_until is None


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


def test_fetch_feed_reserves_new_item_notification_deliveries_when_enqueue_fails(db_session, monkeypatch):
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
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="New item webhook",
        event_type="rss_item_new",
        url_template="https://hooks.example.com/items",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([feed, user])
    db_session.flush()
    db_session.add(webhook)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _feed_lock_override(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}
        url = "https://example.com/feed.xml"

        def iter_bytes(self):
            yield b"<rss />"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    created_items = 0

    def _upsert_item(_db, current_feed, _parsed):
        nonlocal created_items
        created_items += 1
        item = Item(
            id=uuid.uuid4(),
            feed_id=current_feed.id,
            source_guid=f"feed-item-{created_items}",
            url=f"https://example.com/articles/{created_items}",
            canonical_url=f"https://example.com/articles/{created_items}",
            title=f"New item {created_items}",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            dedupe_key=f"feed-item-{created_items}",
            content_hash=str(created_items) * 64,
            status="new",
        )
        _db.add(item)
        _db.flush()
        return item, True, True

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr("app.tasks.feed_tasks.RSSConnector.poll", lambda *_args, **_kwargs: ([{"id": "1"}], None))
    monkeypatch.setattr("app.tasks.feed_tasks._backfill_feed_metadata_from_body", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("app.tasks.feed_tasks._upsert_item_from_parsed", _upsert_item)
    monkeypatch.setattr("app.tasks.feed_tasks.fetch_article.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.process_notification_webhook_deliveries.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    result = fetch_feed.run(str(feed.id), force=True)

    delivery = db_session.scalar(select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.webhook_id == webhook.id))

    assert result["status"] == "ok"
    assert result["notification_deliveries_reserved"] == 1
    assert result["notification_enqueue_failed"] is True
    assert delivery is not None
    assert delivery.delivery_state == "pending"
    assert delivery.event_type_snapshot == "rss_item_new"


def test_enqueue_notification_webhook_delivery_processing_chunks_large_batches(monkeypatch):
    queued_batches: list[list[str]] = []
    delivery_ids = [uuid.uuid4() for _ in range(5)]

    monkeypatch.setattr("app.tasks.feed_tasks.settings.notification_delivery_enqueue_batch_size", 2)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.process_notification_webhook_deliveries.delay",
        lambda batch: queued_batches.append(batch),
    )

    result = enqueue_notification_webhook_delivery_processing(delivery_ids)

    assert result is True
    assert queued_batches == [
        [str(delivery_ids[0]), str(delivery_ids[1])],
        [str(delivery_ids[2]), str(delivery_ids[3])],
        [str(delivery_ids[4])],
    ]


def test_fetch_feed_reports_article_enqueue_failure_without_rolling_back_items(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
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
        status_code = 200
        headers: dict[str, str] = {}
        url = "https://example.com/feed.xml"

        def iter_bytes(self):
            yield b"<rss />"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    created_item_id: uuid.UUID | None = None

    def _upsert_item(_db, current_feed, _parsed):
        nonlocal created_item_id
        item = Item(
            id=uuid.uuid4(),
            feed_id=current_feed.id,
            source_guid="feed-item-1",
            url="https://example.com/articles/1",
            canonical_url="https://example.com/articles/1",
            title="New item 1",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            dedupe_key="feed-item-1",
            content_hash="1" * 64,
            status="new",
        )
        created_item_id = item.id
        _db.add(item)
        _db.flush()
        return item, True, True

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr("app.tasks.feed_tasks.RSSConnector.poll", lambda *_args, **_kwargs: ([{"id": "1"}], None))
    monkeypatch.setattr("app.tasks.feed_tasks._backfill_feed_metadata_from_body", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("app.tasks.feed_tasks._upsert_item_from_parsed", _upsert_item)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_article.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    result = fetch_feed.run(str(feed.id), force=True)

    assert result["status"] == "ok"
    assert result["article_enqueue_failed"] is True
    assert created_item_id is not None
    assert db_session.scalar(select(Item).where(Item.id == created_item_id)) is not None
    assert db_session.scalar(select(Article).where(Article.item_id == created_item_id)) is None


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


def test_dispatch_items_missing_articles_queues_repairable_items_after_grace_period(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Repair feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    old_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="old-item",
        url="https://example.com/articles/old",
        canonical_url="https://example.com/articles/old",
        title="Old item",
        summary="Summary",
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        first_seen_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        dedupe_key="old-item",
        content_hash="2" * 64,
        status="new",
    )
    recent_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="recent-item",
        url="https://example.com/articles/recent",
        canonical_url="https://example.com/articles/recent",
        title="Recent item",
        summary="Summary",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        first_seen_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        dedupe_key="recent-item",
        content_hash="3" * 64,
        status="new",
    )
    fetched_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="fetched-item",
        url="https://example.com/articles/fetched",
        canonical_url="https://example.com/articles/fetched",
        title="Fetched item",
        summary="Summary",
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        first_seen_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        dedupe_key="fetched-item",
        content_hash="4" * 64,
        status="content_fetched",
    )
    fetched_article = Article(
        item_id=fetched_item.id,
        final_url=fetched_item.url,
        http_status=200,
        content_type="text/html",
    )
    failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="failed-item",
        url="https://example.com/articles/failed",
        canonical_url="https://example.com/articles/failed",
        title="Failed item",
        summary="Summary",
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        first_seen_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        dedupe_key="failed-item",
        content_hash="5" * 64,
        status="error",
    )
    failed_article = Article(
        item_id=failed_item.id,
        final_url=failed_item.url,
        http_status=503,
        content_type="text/html",
        retrieved_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        error="network_or_rate_limit_error:gateway timeout",
        text=None,
        extraction_method="none",
    )
    db_session.add_all([feed, old_item, recent_item, fetched_item, failed_item])
    db_session.flush()
    db_session.add_all([fetched_article, failed_article])
    db_session.commit()

    queued_item_ids: list[str] = []

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_items_missing_articles_after_seconds", 300)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_items_missing_articles_batch_size", 10)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_article.delay",
        lambda item_id: queued_item_ids.append(item_id),
    )

    result = dispatch_items_missing_articles.run()

    assert result == {"queued": 2}
    assert queued_item_ids == [str(failed_item.id), str(old_item.id)]


def test_fetch_article_rejects_invalid_item_ids(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = fetch_article.run("not-a-uuid")

    assert result == {"status": "skipped", "reason": "invalid_item_id", "item_id": "not-a-uuid"}


def test_fetch_article_falls_back_to_original_url_when_canonical_fetch_fails(db_session, monkeypatch):
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
        source_guid="fallback-item",
        url="https://example.com/articles/original",
        canonical_url="https://example.com/articles/canonical",
        title="Fallback target",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="fallback-item",
        content_hash="b" * 64,
        status="new",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _domain_slot_override(_domain: str, max_wait_seconds: int = 30):
        _ = max_wait_seconds
        yield

    class _Response:
        def __init__(self, url: str):
            self.status_code = 200
            self.headers = {"content-type": "text/html; charset=utf-8"}
            self.url = url

        def iter_bytes(self):
            yield b"<html><body><article><h1>Recovered article</h1><p>Readable text.</p></article></body></html>"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    queued: list[str] = []

    def _safe_stream(_client, _method: str, url: str, **_kwargs):
        if url.endswith("/canonical"):
            raise RedirectError("broken canonical redirect")
        return _Response(url)

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.domain_slot", _domain_slot_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", _safe_stream)
    monkeypatch.setattr("app.tasks.feed_tasks.classify_item.delay", lambda queued_item_id: queued.append(queued_item_id))
    monkeypatch.setattr("app.tasks.feed_tasks.extract_canonical_url", lambda _html: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_readable_text",
        lambda _html: {
            "title": "Recovered article",
            "text": "Readable text.",
            "method": "readable",
            "language": "en",
            "word_count": 2,
            "error": None,
        },
    )

    result = fetch_article.run(str(item.id))

    assert result == {"status": "ok", "item_id": str(item.id)}
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    assert article is not None
    assert article.final_url == item.url
    assert article.text == "Readable text."
    assert queued == [str(item.id)]


def test_queue_item_ai_enrichment_run_marks_run_error_when_broker_publish_fails(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

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
        source_guid="queue-error-item",
        url="https://example.com/articles/queue-error-item",
        canonical_url="https://example.com/articles/queue-error-item",
        title="Queue failure target",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="queue-error-item",
        content_hash="f" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    item_id = item.id

    with pytest.raises(RuntimeError, match="broker down"):
        _queue_item_ai_enrichment_run(
            item_id=item_id,
            trigger_source=AI_TRIGGER_MANUAL,
            reason=None,
            force=True,
        )

    run = db_session.scalar(
        select(AITaskRun)
        .where(AITaskRun.item_id == item_id, AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT)
        .order_by(AITaskRun.created_at.desc())
    )
    assert run is not None
    assert run.status == "error"
    assert run.reason == "enqueue_failed"
    assert run.error == "broker down"


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
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)
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


def test_classify_item_continues_when_ioc_enqueue_fails(db_session, monkeypatch):
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
        source_guid="ioc-enqueue-item",
        url="https://example.com/articles/ioc-enqueue-item",
        canonical_url="https://example.com/articles/ioc-enqueue-item",
        title="Fortinet exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ioc-enqueue-item",
        content_hash="2" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed Fortinet exploitation in the wild.",
        extraction_method="readable",
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)
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
                "model": "local-threat-model",
            },
        )(),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_item_iocs.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ioc broker down")),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda item_id, force=False, task_run_id=None: captured.update(
            {"item_id": item_id, "force": str(force), "task_run_id": str(task_run_id or "")}
        ),
    )

    result = classify_item.run(str(item.id))

    assert result["status"] == "ok"
    assert result["ioc_enqueue_failed"] is True
    assert result["ai_enqueue_failed"] is False
    assert captured["item_id"] == str(item.id)
    assert captured["task_run_id"]


def test_classify_item_continues_when_ai_enqueue_fails(db_session, monkeypatch):
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
        source_guid="ai-enqueue-item",
        url="https://example.com/articles/ai-enqueue-item",
        canonical_url="https://example.com/articles/ai-enqueue-item",
        title="Fortinet exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ai-enqueue-item",
        content_hash="3" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed Fortinet exploitation in the wild.",
        extraction_method="readable",
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

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
                "model": "local-threat-model",
            },
        )(),
    )
    monkeypatch.setattr("app.tasks.feed_tasks.extract_item_iocs.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ai broker down")),
    )

    result = classify_item.run(str(item.id))

    child_runs = db_session.scalars(
        select(AITaskRun)
        .where(AITaskRun.item_id == item.id, AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT)
        .order_by(AITaskRun.created_at.asc())
    ).all()

    assert result["status"] == "ok"
    assert result["ai_enqueue_failed"] is True
    assert len(child_runs) == 1
    assert child_runs[0].status == "error"
    assert child_runs[0].reason == "enqueue_failed"


def test_classify_item_reserves_alert_match_notification_deliveries_when_enqueue_fails(db_session, monkeypatch):
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
        source_guid="item-alert-1",
        url="https://example.com/articles/1",
        canonical_url="https://example.com/articles/1",
        title="Fortinet exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="item-alert-1",
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
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Alert webhook",
        event_type="alert_match",
        url_template="https://hooks.example.com/alerts",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    alert = AlertInterest(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Fortinet watch",
        category="appliance",
        keywords=["fortinet"],
        enabled=True,
    )
    db_session.add_all([feed, user, item])
    db_session.flush()
    db_session.add_all([article, webhook, alert])
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.load_active_ai_settings",
        lambda _db: type(
            "InactiveAISettings",
            (),
            {
                "ai_enabled": False,
                "ai_configured": False,
                "auto_enrich_new_items": False,
            },
        )(),
    )
    monkeypatch.setattr("app.tasks.feed_tasks.extract_item_iocs.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.process_notification_webhook_deliveries.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    result = classify_item.run(str(item.id))

    delivery = db_session.scalar(
        select(NotificationWebhookDelivery).where(NotificationWebhookDelivery.webhook_id == webhook.id)
    )

    assert result["status"] == "ok"
    assert delivery is not None
    assert delivery.delivery_state == "pending"
    assert delivery.event_type_snapshot == "alert_match"


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
    db_session.flush()

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
        db_session.add(item)
        db_session.flush()
        db_session.add(article)
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


def test_reprocess_recent_ai_items_continues_after_enqueue_failure(db_session, monkeypatch):
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
    db_session.flush()

    item_ids: list[uuid.UUID] = []
    for index in range(2):
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"reprocess-enqueue-{index}",
            url=f"https://example.com/articles/reprocess-enqueue-{index}",
            canonical_url=f"https://example.com/articles/reprocess-enqueue-{index}",
            title=f"Reprocess enqueue target {index}",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
            dedupe_key=f"reprocess-enqueue-{index}",
            content_hash=str(index + 5) * 64,
            status="content_fetched",
        )
        article = Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Researchers observed Fortinet exploitation in the wild.",
            extraction_method="readable",
        )
        db_session.add(item)
        db_session.flush()
        db_session.add(article)
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

    class _FakeTask:
        def __init__(self, task_id: str):
            self.id = task_id

    queue_calls: list[str] = []

    def _fake_delay(item_id: str, force: bool = False, task_run_id: str | None = None):
        _ = force
        _ = task_run_id
        queue_calls.append(item_id)
        if item_id == str(item_ids[0]):
            raise RuntimeError("broker down")
        return _FakeTask(f"child-{len(queue_calls)}")

    monkeypatch.setattr("app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay", _fake_delay)

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 2},
    )
    db_session.commit()

    result = reprocess_recent_ai_items.run(7, 2, task_run_id=str(parent_run.id))

    child_runs = db_session.scalars(
        select(AITaskRun)
        .where(AITaskRun.parent_run_id == parent_run.id, AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT)
        .order_by(AITaskRun.created_at.asc())
    ).all()

    assert result["queued"] == 1
    assert result["queue_errors"] == 1
    assert len(queue_calls) == 2
    assert set(queue_calls) == {str(item_ids[0]), str(item_ids[1])}
    assert len(child_runs) == 2
    assert {run.status for run in child_runs} == {"queued", "error"}

    queued_child = next(run for run in child_runs if run.status == "queued")
    generate_item_ai_enrichment_task.run(str(queued_child.item_id), force=True, task_run_id=str(queued_child.id))

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))

    assert refreshed_parent is not None
    assert refreshed_parent.target_count == 2
    assert refreshed_parent.processed_count == 2
    assert refreshed_parent.error_count == 1
    assert refreshed_parent.status == "error"
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
        source_guid="unexpected-error-item",
        url="https://example.com/articles/unexpected-error-item",
        canonical_url="https://example.com/articles/unexpected-error-item",
        title="Unexpected error target",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="unexpected-error-item",
        content_hash="9" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.flush()

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
        item_id=item.id,
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
    db_session.flush()

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
        db_session.add(item)
        db_session.flush()
        db_session.add(article)
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


def test_scheduled_daily_ai_brief_due_retries_after_non_ready_brief(db_session, monkeypatch):
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
            status="error",
            item_count=0,
            generated_at=datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc),
            error="provider timeout",
        )
    )
    db_session.commit()

    due, reason = _scheduled_daily_ai_brief_due(
        db_session,
        now=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
    )

    assert due is True
    assert reason is None
    get_settings.cache_clear()


def test_scheduled_daily_ai_brief_due_skips_while_today_run_is_in_flight(db_session, monkeypatch):
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
    in_flight_run = queue_ai_task_run(
        db_session,
        task_type="daily_brief",
        trigger_source=AI_TRIGGER_MANUAL,
    )
    in_flight_run.status = "running"
    in_flight_run.queued_at = datetime(2026, 3, 27, 9, 5, tzinfo=timezone.utc)
    db_session.add(in_flight_run)
    db_session.commit()

    due, reason = _scheduled_daily_ai_brief_due(
        db_session,
        now=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
    )

    assert due is False
    assert reason == "already_running"
    get_settings.cache_clear()


def test_scheduled_daily_ai_brief_due_recovers_stale_pending_brief(db_session, monkeypatch):
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
    stale_time = datetime(2026, 3, 27, 8, 30, tzinfo=timezone.utc)
    db_session.add(
        AIDailyBrief(
            id=uuid.uuid4(),
            brief_date=datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc).date(),
            window_start=datetime(2026, 3, 26, 9, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc),
            status="pending",
            item_count=0,
            generated_at=None,
            created_at=stale_time,
            updated_at=stale_time,
        )
    )
    db_session.commit()

    due, reason = _scheduled_daily_ai_brief_due(
        db_session,
        now=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
    )

    assert due is True
    assert reason is None
    get_settings.cache_clear()
