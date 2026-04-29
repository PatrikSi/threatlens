import uuid
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.alert_interest import AlertInterest
from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_integration import AICompletionResult, AIDailyBriefGenerationResult, AIItemEnrichmentResult
from app.services.ai_ops import AI_TASK_TYPE_ITEM_ENRICHMENT, AI_TASK_TYPE_REPROCESS, AI_TRIGGER_MANUAL, queue_ai_task_run
from app.services.ai_ops import AI_TASK_TYPE_DAILY_BRIEF, start_ai_task_run
from app.services.safe_fetch import RedirectError
from app.schemas.ai import AISettingsUpdate
from app.schemas.notification import NotificationWebhookTestResponse
from app.services.feed_pipeline import mark_feed_failure as _mark_feed_failure
from app.tasks.feed_tasks import (
    _process_reserved_notification_deliveries,
    _scheduled_daily_ai_brief_due,
    _queue_item_ai_enrichment_run,
    backfill_feed_metadata,
    classify_item,
    dispatch_daily_ai_brief_generation,
    dispatch_due_feeds,
    dispatch_feed_metadata_backfill,
    dispatch_items_missing_articles,
    dispatch_items_missing_ai_enrichment,
    dispatch_items_missing_iocs,
    enqueue_notification_webhook_delivery_processing,
    extract_item_iocs,
    fetch_article,
    fetch_feed,
    generate_item_ai_enrichment_task,
    feed_lock,
    domain_slot,
    reconcile_ai_task_runs,
    reapply_recent_item_tags,
    reprocess_recent_ai_items,
    daily_ai_brief_lock,
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


class _HeartbeatRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expirations: dict[str, float] = {}
        self.expire_counts: dict[str, int] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, expires_at in self.expirations.items() if expires_at <= now]
        for key in expired:
            self.values.pop(key, None)
            self.expirations.pop(key, None)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        self._purge_expired()
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expire(key, ex)
        else:
            self.expirations.pop(key, None)
        return True

    def get(self, key: str):
        self._purge_expired()
        return self.values.get(key)

    def expire(self, key: str, seconds: int):
        self._purge_expired()
        if key not in self.values:
            return False
        self.expire_counts[key] = self.expire_counts.get(key, 0) + 1
        self.expirations[key] = time.monotonic() + max(0, int(seconds))
        return True

    def pttl(self, key: str):
        self._purge_expired()
        if key not in self.values:
            return -2
        expires_at = self.expirations.get(key)
        if expires_at is None:
            return -1
        return max(0, int((expires_at - time.monotonic()) * 1000))

    def incr(self, key: str):
        self._purge_expired()
        current = int(self.values.get(key, 0)) + 1
        self.values[key] = str(current)
        return current

    def decr(self, key: str):
        self._purge_expired()
        current = int(self.values.get(key, 0)) - 1
        self.values[key] = str(current)
        return current

    def delete(self, key: str):
        self._purge_expired()
        existed = key in self.values or key in self.expirations
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return 1 if existed else 0

    def eval(self, _script: str, _numkeys: int, *args):
        self._purge_expired()
        if _numkeys == 1 and len(args) == 2:
            key, token = args
            if self.values.get(key) == token:
                self.delete(key)
                return 1
            return 0
        if _numkeys == 2 and len(args) == 7:
            key, heartbeat_key, observed_token, observed_heartbeat, new_token, new_heartbeat, ttl_seconds = args
            current_token = self.values.get(key)
            current_heartbeat = self.values.get(heartbeat_key)
            if current_token != observed_token:
                return 0
            if self.pttl(key) != -1:
                return 0
            if observed_heartbeat == "__missing__":
                if current_heartbeat is not None:
                    return 0
            elif current_heartbeat != observed_heartbeat:
                return 0
            self.values[key] = new_token
            self.values[heartbeat_key] = new_heartbeat
            self.expire(key, int(ttl_seconds))
            self.expire(heartbeat_key, int(ttl_seconds))
            return 1
        return 0


def test_lease_heartbeats_renew_feed_daily_and_domain_locks(monkeypatch: pytest.MonkeyPatch):
    redis_client = _HeartbeatRedis()
    monkeypatch.setattr("app.tasks.feed_tasks.redis_client", redis_client)
    monkeypatch.setattr("app.tasks.feed_tasks._lease_renewal_interval_seconds", lambda _ttl: 0.01)

    feed_key = "threatlens:feed:lock:feed-1"
    brief_key = "threatlens:ai:daily_brief:lock"
    domain_key = "threatlens:domain:example.com:slot:1"

    with feed_lock("feed-1", ttl_seconds=1) as acquired:
        assert acquired is True
        time.sleep(0.05)
        assert redis_client.expire_counts[feed_key] >= 2

    with daily_ai_brief_lock(ttl_seconds=1) as acquired:
        assert acquired is True
        time.sleep(0.05)
        assert redis_client.expire_counts[brief_key] >= 2

    with domain_slot("example.com", max_wait_seconds=0.1):
        time.sleep(0.05)
        assert redis_client.expire_counts[domain_key] >= 2


def test_feed_lock_does_not_take_over_before_lock_ttl_expires(monkeypatch: pytest.MonkeyPatch):
    redis_client = _HeartbeatRedis()
    stale_feed_key = "threatlens:feed:lock:feed-stale"
    stale_heartbeat_key = f"{stale_feed_key}:heartbeat"
    redis_client.set(stale_feed_key, "dead-token", ex=900)
    redis_client.set(stale_heartbeat_key, f"dead-token|{time.time() - 901:.6f}", ex=900)
    monkeypatch.setattr("app.tasks.feed_tasks.redis_client", redis_client)

    with feed_lock("feed-stale", ttl_seconds=900) as acquired:
        assert acquired is False
        assert redis_client.get(stale_feed_key) == "dead-token"


def test_feed_lock_can_take_over_stale_lease_without_ttl(monkeypatch: pytest.MonkeyPatch):
    redis_client = _HeartbeatRedis()
    stale_feed_key = "threatlens:feed:lock:feed-stale"
    stale_heartbeat_key = f"{stale_feed_key}:heartbeat"
    redis_client.set(stale_feed_key, "dead-token")
    redis_client.set(stale_heartbeat_key, f"dead-token|{time.time() - 901:.6f}", ex=900)
    monkeypatch.setattr("app.tasks.feed_tasks.redis_client", redis_client)

    with feed_lock("feed-stale", ttl_seconds=900) as acquired:
        assert acquired is True
        assert redis_client.get(stale_feed_key) != "dead-token"


def test_domain_slot_uses_open_slot_instead_of_preempting_live_ttl_slot(monkeypatch: pytest.MonkeyPatch):
    redis_client = _HeartbeatRedis()
    stale_slot_key = "threatlens:domain:example.com:slot:1"
    open_slot_key = "threatlens:domain:example.com:slot:2"
    redis_client.set(stale_slot_key, "dead-token", ex=30)
    redis_client.set(f"{stale_slot_key}:heartbeat", f"dead-token|{time.time() - 31:.6f}", ex=30)
    monkeypatch.setattr("app.tasks.feed_tasks.redis_client", redis_client)
    monkeypatch.setattr("app.tasks.feed_tasks.settings", SimpleNamespace(per_domain_concurrency=2))

    with domain_slot("example.com", max_wait_seconds=0.1):
        assert redis_client.get(stale_slot_key) == "dead-token"
        assert redis_client.get(open_slot_key) is not None


def test_generate_item_ai_enrichment_task_claims_api_started_run_and_skips_duplicate_redelivery(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="AI Feed",
        url="https://example.com/ai.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item_id = uuid.uuid4()
    item = Item(
        id=item_id,
        feed_id=feed.id,
        source_guid="ai-item-1",
        url="https://example.com/articles/ai-item-1",
        canonical_url="https://example.com/articles/ai-item-1",
        title="AI item",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ai-item-1",
        content_hash="a" * 64,
        status="new",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    started_enrichment = SimpleNamespace(
        summary_text="summary",
        relevance_label="high",
        model="local-threat-model",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        latency_ms=4,
    )
    ready_result = AIItemEnrichmentResult(
        enrichment=started_enrichment,
        status="ready",
        reason=None,
        input_text_chars=42,
        prompt_char_count=10,
        response_char_count=11,
    )

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item_id,
        metadata={"force": True},
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="api", metadata_updates={"force": True})
    db_session.commit()

    called: list[uuid.UUID] = []

    def _run_item_ai_enrichment(_db, *, item_id: uuid.UUID, force: bool = False, task_run_id: uuid.UUID | None = None):
        _ = (force, task_run_id)
        called.append(item_id)
        return ready_result

    monkeypatch.setattr("app.tasks.feed_tasks.run_item_ai_enrichment", _run_item_ai_enrichment)

    first = generate_item_ai_enrichment_task.apply(
        args=(str(item_id),),
        kwargs={"force": True, "task_run_id": str(run.id)},
        task_id="worker-a",
    ).get()

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert first == {"status": "ready", "reason": None, "item_id": str(item_id)}
    assert called == [item_id]
    assert refreshed is not None
    assert refreshed.celery_task_id == "worker-a"
    assert refreshed.status == "ready"

    duplicate_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item_id,
        metadata={"force": True},
    )
    start_ai_task_run(
        db_session,
        run_id=duplicate_run.id,
        worker_name="celery@test",
        celery_task_id="worker-a",
        metadata_updates={"force": True},
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_item_ai_enrichment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate redelivery should not execute the body")),
    )

    duplicate = generate_item_ai_enrichment_task.apply(
        args=(str(item_id),),
        kwargs={"force": True, "task_run_id": str(duplicate_run.id)},
        task_id="worker-b",
    ).get()

    db_session.expire_all()
    refreshed_duplicate = db_session.scalar(select(AITaskRun).where(AITaskRun.id == duplicate_run.id))
    assert duplicate == {"status": "skipped", "reason": "already_running", "item_id": str(item_id)}
    assert refreshed_duplicate is not None
    assert refreshed_duplicate.celery_task_id == "worker-a"
    assert refreshed_duplicate.status == "running"


def test_generate_item_ai_enrichment_task_skips_when_item_claim_reports_another_run(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="AI Feed",
        url="https://example.com/ai-lock.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item_id = uuid.uuid4()
    item = Item(
        id=item_id,
        feed_id=feed.id,
        source_guid="ai-item-locked",
        url="https://example.com/articles/ai-item-locked",
        canonical_url="https://example.com/articles/ai-item-locked",
        title="AI item locked",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ai-item-locked",
        content_hash="b" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    child_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item_id,
        metadata={"force": True},
    )
    db_session.commit()
    child_run_id = child_run.id

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_item_ai_enrichment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("the provider body should not run while the item is locked")),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks._claim_item_ai_enrichment_target",
        lambda _db, *, item_id: (None, "already_running"),
    )

    result = generate_item_ai_enrichment_task.run(str(item_id), force=True, task_run_id=str(child_run.id))

    db_session.expire_all()
    refreshed_child = db_session.scalar(select(AITaskRun).where(AITaskRun.id == child_run_id))

    assert result == {"status": "skipped", "reason": "already_running", "item_id": str(item_id)}
    assert refreshed_child is not None
    assert refreshed_child.status == "skipped"
    assert refreshed_child.reason == "already_running"


def test_dispatch_daily_ai_brief_generation_claims_api_started_run_and_skips_duplicate_redelivery(db_session, monkeypatch):
    brief_id = uuid.uuid4()
    brief = AIDailyBrief(
        id=brief_id,
        brief_date=datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc).date(),
        window_start=datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc),
        status="pending",
        item_count=0,
    )
    db_session.add(brief)
    db_session.commit()

    ready_result = AIDailyBriefGenerationResult(
        brief=None,
        status="ready",
        reason=None,
        items_considered=0,
        items_selected=0,
    )

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _brief_lock_override(ttl_seconds: int = 900):
        _ = ttl_seconds
        yield True

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.daily_ai_brief_lock", _brief_lock_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.load_active_ai_settings",
        lambda _db: SimpleNamespace(ai_enabled=True, ai_configured=True, daily_brief_enabled=True, model="local-threat-model"),
    )

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        daily_brief_id=brief_id,
        metadata={"force": True},
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="api", metadata_updates={"force": True})
    db_session.commit()

    called: list[uuid.UUID] = []

    def _run_daily_brief_generation(_db, *, force: bool = False, task_run_id: uuid.UUID | None = None):
        _ = force
        called.append(task_run_id)
        return ready_result

    monkeypatch.setattr("app.tasks.feed_tasks.run_daily_brief_generation", _run_daily_brief_generation)

    first = dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(run.id), "actor_user_id": None},
        task_id="worker-a",
    ).get()

    db_session.expire_all()
    refreshed = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert first == {"status": "ready", "reason": None}
    assert called == [run.id]
    assert refreshed is not None
    assert refreshed.celery_task_id == "worker-a"
    assert refreshed.status == "ready"

    duplicate_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        daily_brief_id=brief_id,
        metadata={"force": True},
    )
    start_ai_task_run(
        db_session,
        run_id=duplicate_run.id,
        worker_name="celery@test",
        celery_task_id="worker-a",
        metadata_updates={"force": True},
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_daily_brief_generation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate redelivery should not execute the body")),
    )

    duplicate = dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(duplicate_run.id), "actor_user_id": None},
        task_id="worker-b",
    ).get()

    db_session.expire_all()
    refreshed_duplicate = db_session.scalar(select(AITaskRun).where(AITaskRun.id == duplicate_run.id))
    assert duplicate == {"status": "skipped", "reason": "already_running", "run_id": str(duplicate_run.id)}
    assert refreshed_duplicate is not None
    assert refreshed_duplicate.celery_task_id == "worker-a"
    assert refreshed_duplicate.status == "running"


def test_dispatch_daily_ai_brief_marks_manual_run_skipped_when_lock_is_busy(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _brief_lock_override(ttl_seconds: int = 900):
        _ = ttl_seconds
        yield False

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_DAILY_BRIEF,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"force": True},
    )
    db_session.commit()

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.daily_ai_brief_lock", _brief_lock_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_daily_brief_generation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("busy lock should skip execution")),
    )

    result = dispatch_daily_ai_brief_generation.apply(
        kwargs={"force": True, "task_run_id": str(run.id), "actor_user_id": None},
        task_id="worker-lock-busy",
    ).get()

    db_session.expire_all()
    refreshed_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert result == {"status": "skipped", "reason": "already_running", "run_id": str(run.id)}
    assert refreshed_run is not None
    assert refreshed_run.status == "skipped"
    assert refreshed_run.reason == "already_running"
    assert refreshed_run.worker_name


def test_reapply_recent_item_tags_skips_when_reapply_lock_is_busy(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _tagging_lock_override(ttl_seconds: int = 900, token: str | None = None):
        _ = (ttl_seconds, token)
        yield False

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.tagging_reapply_lock", _tagging_lock_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.classify_item_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("busy lock should skip execution")),
    )

    result = reapply_recent_item_tags.run(30, 0)

    assert result == {"status": "skipped", "reason": "already_running", "days": 30, "limit": 0}


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
    db_session.refresh(feed)
    assert feed.next_fetch_at is not None
    assert feed.next_fetch_at > datetime.now(timezone.utc)


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


def test_fetch_feed_retry_bypasses_dispatch_claim_backoff(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Retry Claimed Feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        last_fetch_at=datetime.now(timezone.utc),
        dispatch_claimed_at=datetime.now(timezone.utc),
        dispatch_backoff_until=datetime.now(timezone.utc) + timedelta(minutes=10),
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
    monkeypatch.setattr(fetch_feed.request, "retries", 1, raising=False)

    result = fetch_feed.run(str(feed.id))

    assert result == {"status": "not_modified", "feed_id": str(feed.id)}
    db_session.refresh(feed)
    assert feed.dispatch_claimed_at is None
    assert feed.dispatch_backoff_until is None


def test_fetch_feed_uses_decrypted_url_for_authenticated_feeds(db_session, monkeypatch):
    plaintext_url = "https://alice:secret@example.com/feed.xml?token=alpha"
    feed = Feed(
        id=uuid.uuid4(),
        name="Authenticated Feed",
        url=plaintext_url,
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
        url = plaintext_url

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

    captured: dict[str, str] = {}

    def _safe_stream_with_redirects(_client, _method, url, **_kwargs):
        captured["url"] = url
        return _Response()

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", _safe_stream_with_redirects)

    result = fetch_feed.run(str(feed.id), force=True)

    assert result == {"status": "not_modified", "feed_id": str(feed.id)}
    assert captured["url"] == plaintext_url


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


def test_dispatch_due_feeds_uses_persisted_next_fetch_at(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Future Feed",
        url="https://example.com/future.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        last_fetch_at=datetime.now(timezone.utc) - timedelta(hours=2),
        next_fetch_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_feed.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("future feed should not be queued")),
    )

    result = dispatch_due_feeds.run()

    assert result == {"queued": 0}


def test_mark_feed_failure_applies_growing_dispatch_backoff(db_session):
    feed = Feed(
        id=uuid.uuid4(),
        name="Backoff Feed",
        url="https://example.com/backoff.xml",
        enabled=True,
        fetch_interval_seconds=120,
    )
    db_session.add(feed)
    db_session.commit()

    _mark_feed_failure(db_session, feed, "http_status:503")
    first_delay_seconds = int((feed.dispatch_backoff_until - feed.last_fetch_at).total_seconds())
    db_session.commit()

    _mark_feed_failure(db_session, feed, "http_status:503")
    second_delay_seconds = int((feed.dispatch_backoff_until - feed.last_fetch_at).total_seconds())

    assert feed.error_count == 2
    assert first_delay_seconds >= 300
    assert second_delay_seconds > first_delay_seconds


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


def test_fetch_feed_marks_non_feed_http_200_response_as_failure(db_session, monkeypatch):
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
            yield b"<html><body>not a feed</body></html>"

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

    db_session.refresh(feed)
    assert result == {"status": "error", "feed_id": str(feed.id)}
    assert feed.error_count == 1
    assert feed.last_error == "invalid_feed_content"


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


def test_enqueue_notification_webhook_delivery_processing_uses_countdown_apply_async(monkeypatch):
    queued_batches: list[tuple[list[list[str]], int]] = []
    delivery_ids = [uuid.uuid4() for _ in range(3)]

    def _fake_apply_async(*, args, countdown):
        queued_batches.append((args, countdown))

    monkeypatch.setattr("app.tasks.feed_tasks.settings.notification_delivery_enqueue_batch_size", 2)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.process_notification_webhook_deliveries.apply_async",
        _fake_apply_async,
    )

    result = enqueue_notification_webhook_delivery_processing(delivery_ids, countdown=15)

    assert result is True
    assert queued_batches == [
        ([[str(delivery_ids[0]), str(delivery_ids[1])]], 15),
        ([[str(delivery_ids[2])]], 15),
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


def test_dispatch_items_missing_articles_recovers_updated_items_with_existing_articles_after_enqueue_failure(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Updated Feed",
        url="https://example.com/updated.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="updated-item",
        url="https://example.com/articles/updated-item",
        canonical_url="https://example.com/articles/updated-item",
        title="Original title",
        summary="Original summary",
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        first_seen_at=datetime.now(timezone.utc) - timedelta(hours=1),
        dedupe_key="updated-item",
        content_hash="1" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        content_type="text/html",
        text="Original article body.",
        extraction_method="readable",
        retrieved_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add(article)
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
        url = "https://example.com/updated.xml"

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

    def _upsert_updated_item(_db, _feed, _parsed):
        item.title = "Updated title"
        item.summary = "Updated summary"
        item.content_hash = "2" * 64
        item.status = "new"
        item.last_error = None
        _db.add(item)
        _db.flush()
        return item, True, False

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.feed_lock", _feed_lock_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr("app.tasks.feed_tasks.RSSConnector.poll", lambda *_args, **_kwargs: ([{"id": "updated"}], None))
    monkeypatch.setattr("app.tasks.feed_tasks._backfill_feed_metadata_from_body", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("app.tasks.feed_tasks._upsert_item_from_parsed", _upsert_updated_item)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_article.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    result = fetch_feed.run(str(feed.id), force=True)

    db_session.expire_all()
    refreshed_item = db_session.scalar(select(Item).where(Item.id == item.id))
    refreshed_article = db_session.scalar(select(Article).where(Article.item_id == item.id))

    assert result["status"] == "ok"
    assert result["article_enqueue_failed"] is True
    assert refreshed_item is not None
    assert refreshed_item.status == "new"
    assert refreshed_article is not None
    assert refreshed_article.text == "Original article body."

    queued_item_ids: list[str] = []
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_items_missing_articles_after_seconds", 0)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.fetch_article.delay",
        lambda queued_item_id: queued_item_ids.append(queued_item_id),
    )

    repair_result = dispatch_items_missing_articles.run()

    assert repair_result == {"queued": 1}
    assert queued_item_ids == [str(item.id)]


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


def test_backfill_feed_metadata_marks_feeds_with_unreadable_urls(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Broken feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    feed._url_encrypted = "enc:v1:not-a-valid-fernet-token"
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
    monkeypatch.setattr("app.tasks.feed_tasks.enqueue_notification_webhook_delivery_processing", lambda _delivery_ids: True)

    result = backfill_feed_metadata.run(str(feed.id))

    db_session.refresh(feed)
    assert result == {"status": "error", "feed_id": str(feed.id), "reason": "feed_url_unavailable"}
    assert feed.last_error is not None
    assert "cannot be decrypted" in feed.last_error
    assert feed.error_count == 1


def test_fetch_feed_marks_feeds_with_unreadable_urls(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Broken feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    feed._url_encrypted = "enc:v1:not-a-valid-fernet-token"
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
    monkeypatch.setattr("app.tasks.feed_tasks.enqueue_notification_webhook_delivery_processing", lambda _delivery_ids: True)

    result = fetch_feed.run(str(feed.id))

    db_session.refresh(feed)
    assert result == {"status": "error", "feed_id": str(feed.id), "reason": "feed_url_unavailable"}
    assert feed.last_error is not None
    assert "cannot be decrypted" in feed.last_error
    assert feed.error_count == 1


def test_dispatch_items_missing_articles_queues_repairable_items_after_grace_period(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
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
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(minutes=10),
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
        published_at=now - timedelta(minutes=1),
        first_seen_at=now - timedelta(seconds=60),
        dedupe_key="recent-item",
        content_hash="3" * 64,
        status="new",
    )
    aged_out_missing_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="aged-out-missing-item",
        url="https://example.com/articles/aged-out-missing",
        canonical_url="https://example.com/articles/aged-out-missing",
        title="Aged out missing item",
        summary="Summary",
        published_at=now - timedelta(days=2),
        first_seen_at=now - timedelta(days=2),
        dedupe_key="aged-out-missing-item",
        content_hash="4" * 64,
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
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(minutes=15),
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
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(minutes=20),
        dedupe_key="failed-item",
        content_hash="5" * 64,
        status="error",
    )
    failed_article = Article(
        item_id=failed_item.id,
        final_url=failed_item.url,
        http_status=503,
        content_type="text/html",
        retrieved_at=now - timedelta(minutes=10),
        error="network_or_rate_limit_error:gateway timeout",
        text=None,
        extraction_method="none",
    )
    soft_failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="soft-failed-item",
        url="https://example.com/articles/soft-failed",
        canonical_url="https://example.com/articles/soft-failed",
        title="Soft failed item",
        summary="Summary",
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(minutes=18),
        dedupe_key="soft-failed-item",
        content_hash="6" * 64,
        status="error",
    )
    soft_failed_article = Article(
        item_id=soft_failed_item.id,
        final_url=soft_failed_item.url,
        http_status=200,
        content_type="application/json",
        retrieved_at=now - timedelta(hours=2),
        error="non_html_response",
        text=None,
        extraction_method="none",
    )
    extraction_failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="extraction-failed-item",
        url="https://example.com/articles/extraction-failed",
        canonical_url="https://example.com/articles/extraction-failed",
        title="Extraction failed item",
        summary="Summary",
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(minutes=16),
        dedupe_key="extraction-failed-item",
        content_hash="7" * 64,
        status="error",
    )
    extraction_failed_article = Article(
        item_id=extraction_failed_item.id,
        final_url=extraction_failed_item.url,
        http_status=200,
        content_type="text/html",
        retrieved_at=now - timedelta(hours=2),
        error="readability_error:parser exploded",
        text=None,
        extraction_method="none",
    )
    recent_soft_failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="recent-soft-failed-item",
        url="https://example.com/articles/recent-soft-failed",
        canonical_url="https://example.com/articles/recent-soft-failed",
        title="Recent soft failed item",
        summary="Summary",
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(minutes=14),
        dedupe_key="recent-soft-failed-item",
        content_hash="8" * 64,
        status="error",
    )
    recent_soft_failed_article = Article(
        item_id=recent_soft_failed_item.id,
        final_url=recent_soft_failed_item.url,
        http_status=200,
        content_type="text/html",
        retrieved_at=now - timedelta(minutes=30),
        error="no_extractor_succeeded",
        text=None,
        extraction_method="none",
    )
    stale_soft_failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="stale-soft-failed-item",
        url="https://example.com/articles/stale-soft-failed",
        canonical_url="https://example.com/articles/stale-soft-failed",
        title="Stale soft failed item",
        summary="Summary",
        published_at=now - timedelta(days=2),
        first_seen_at=now - timedelta(days=2),
        dedupe_key="stale-soft-failed-item",
        content_hash="9" * 64,
        status="error",
    )
    stale_soft_failed_article = Article(
        item_id=stale_soft_failed_item.id,
        final_url=stale_soft_failed_item.url,
        http_status=200,
        content_type="text/html",
        retrieved_at=now - timedelta(hours=2),
        error="no_extractor_succeeded",
        text=None,
        extraction_method="none",
    )
    aged_out_failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="aged-out-failed-item",
        url="https://example.com/articles/aged-out-failed",
        canonical_url="https://example.com/articles/aged-out-failed",
        title="Aged out failed item",
        summary="Summary",
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(minutes=11),
        dedupe_key="aged-out-failed-item",
        content_hash="0" * 64,
        status="error",
    )
    aged_out_failed_article = Article(
        item_id=aged_out_failed_item.id,
        final_url=aged_out_failed_item.url,
        http_status=200,
        content_type="text/html",
        retrieved_at=now - timedelta(days=2),
        error="no_extractor_succeeded",
        text=None,
        extraction_method="none",
    )
    terminal_failed_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="terminal-failed-item",
        url="http://10.0.0.8/private",
        canonical_url="http://10.0.0.8/private",
        title="Terminal failed item",
        summary="Summary",
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(minutes=12),
        dedupe_key="terminal-failed-item",
        content_hash="a" * 64,
        status="error",
    )
    terminal_failed_article = Article(
        item_id=terminal_failed_item.id,
        final_url=terminal_failed_item.url,
        http_status=0,
        content_type=None,
        retrieved_at=now - timedelta(hours=2),
        error="unsafe_article_url",
        text=None,
        extraction_method="none",
    )
    db_session.add_all(
        [
            feed,
            old_item,
            recent_item,
            aged_out_missing_item,
            fetched_item,
            failed_item,
            soft_failed_item,
            extraction_failed_item,
            recent_soft_failed_item,
            stale_soft_failed_item,
            aged_out_failed_item,
            terminal_failed_item,
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            fetched_article,
            failed_article,
            soft_failed_article,
            extraction_failed_article,
            recent_soft_failed_article,
            stale_soft_failed_article,
            aged_out_failed_article,
            terminal_failed_article,
        ]
    )
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

    assert result == {"queued": 7}
    assert set(queued_item_ids) == {
        str(aged_out_failed_item.id),
        str(aged_out_missing_item.id),
        str(failed_item.id),
        str(soft_failed_item.id),
        str(extraction_failed_item.id),
        str(old_item.id),
        str(stale_soft_failed_item.id),
    }


def test_dispatch_items_missing_ai_enrichment_requeues_classified_items_without_enrichment_rows(db_session, monkeypatch):
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
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="missing-enrichment-row",
        url="https://example.com/articles/missing-enrichment-row",
        canonical_url="https://example.com/articles/missing-enrichment-row",
        title="Fortinet edge exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        dedupe_key="missing-enrichment-row",
        content_hash="7" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.91,
        scores_json={"vulnerability": 9.1},
        matched_terms_json={"vulnerability": ["title:fortinet"]},
        source_hash="classification-hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    db_session.add_all([article, classification])
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks._update_task_run_celery_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: SimpleNamespace(id="repair-task-1"),
    )

    result = dispatch_items_missing_ai_enrichment.run()

    queued_run = db_session.scalar(
        select(AITaskRun).where(
            AITaskRun.item_id == item.id,
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.status == "queued",
        )
    )

    assert result == {"queued": 1}
    assert queued_run is not None
    get_settings.cache_clear()


def test_dispatch_items_missing_ai_enrichment_recovers_stale_inflight_runs_without_live_snapshot(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
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
        source_guid="stale-inflight-ai-run",
        url="https://example.com/articles/stale-inflight-ai-run",
        canonical_url="https://example.com/articles/stale-inflight-ai-run",
        title="Fortinet edge exploitation observed",
        summary="Summary",
        published_at=now,
        first_seen_at=now,
        dedupe_key="stale-inflight-ai-run",
        content_hash="9" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.91,
        scores_json={"vulnerability": 9.1},
        matched_terms_json={"vulnerability": ["title:fortinet"]},
        source_hash="classification-hash",
        rules_version="v2",
        classified_at=now,
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    db_session.add_all([article, classification])
    db_session.flush()

    stale_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    start_ai_task_run(db_session, run_id=stale_run.id, worker_name="celery@test", celery_task_id="stale-task-id")
    stale_time = now - timedelta(hours=1)
    stale_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == stale_run.id))
    assert stale_run is not None
    stale_run.queued_at = stale_time
    stale_run.started_at = stale_time
    stale_run.created_at = stale_time
    stale_run.updated_at = stale_time
    db_session.add(stale_run)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (False, [], [], [], []))
    monkeypatch.setattr("app.tasks.feed_tasks._update_task_run_celery_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: SimpleNamespace(id="repair-task-3"),
    )

    result = dispatch_items_missing_ai_enrichment.run()

    refreshed_stale_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == stale_run.id))
    queued_runs = db_session.scalars(
        select(AITaskRun).where(
            AITaskRun.item_id == item.id,
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.status == "queued",
        )
    ).all()

    assert result == {"queued": 1}
    assert refreshed_stale_run is not None
    assert refreshed_stale_run.status == "error"
    assert refreshed_stale_run.reason == "stale_task_snapshot_unavailable"
    assert len(queued_runs) == 1
    get_settings.cache_clear()


def test_dispatch_items_missing_ai_enrichment_requeues_failed_rows_after_backoff(db_session, monkeypatch):
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
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="failed-enrichment-row",
        url="https://example.com/articles/failed-enrichment-row",
        canonical_url="https://example.com/articles/failed-enrichment-row",
        title="Fortinet edge exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        dedupe_key="failed-enrichment-row",
        content_hash="8" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.91,
        scores_json={"vulnerability": 9.1},
        matched_terms_json={"vulnerability": ["title:fortinet"]},
        source_hash="classification-hash",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    db_session.add_all([article, classification])
    db_session.flush()

    failed_enrichment = ItemAIEnrichment(
        item_id=item.id,
        status="error",
        source_hash="existing-source-hash",
        error="provider unavailable",
        generated_at=datetime.now(timezone.utc) - timedelta(hours=2),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(failed_enrichment)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks._update_task_run_celery_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_items_failed_ai_enrichment_after_seconds", 60)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: SimpleNamespace(id="repair-task-2"),
    )

    result = dispatch_items_missing_ai_enrichment.run()

    queued_runs = db_session.scalars(
        select(AITaskRun).where(
            AITaskRun.item_id == item.id,
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.status == "queued",
        )
    ).all()

    assert result == {"queued": 1}
    assert len(queued_runs) == 1
    get_settings.cache_clear()


def test_dispatch_items_missing_ai_enrichment_skips_old_feed_backlog(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
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
        source_guid="old-feed-backlog",
        url="https://example.com/articles/old-feed-backlog",
        canonical_url="https://example.com/articles/old-feed-backlog",
        title="Old Fortinet edge exploitation observed",
        summary="Summary",
        published_at=now - timedelta(days=10),
        first_seen_at=now,
        dedupe_key="old-feed-backlog",
        content_hash="9" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.91,
        scores_json={"vulnerability": 9.1},
        matched_terms_json={"vulnerability": ["title:fortinet"]},
        source_hash="classification-hash",
        rules_version="v2",
        classified_at=now,
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    db_session.add_all([article, classification])
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.ai_auto_enrich_new_item_max_age_hours", 24)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("old backlog should not be auto-enriched")),
    )

    result = dispatch_items_missing_ai_enrichment.run()

    queued_run = db_session.scalar(
        select(AITaskRun).where(
            AITaskRun.item_id == item.id,
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.status == "queued",
        )
    )

    assert result == {"queued": 0}
    assert queued_run is None
    get_settings.cache_clear()


def test_dispatch_items_missing_ai_enrichment_skips_items_with_active_runs(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
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
        source_guid="active-enrichment-row",
        url="https://example.com/articles/active-enrichment-row",
        canonical_url="https://example.com/articles/active-enrichment-row",
        title="Fortinet edge exploitation observed",
        summary="Summary",
        published_at=now,
        first_seen_at=now,
        dedupe_key="active-enrichment-row",
        content_hash="a" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed exploitation affecting Fortinet edge devices.",
        extraction_method="readable",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.91,
        scores_json={"vulnerability": 9.1},
        matched_terms_json={"vulnerability": ["title:fortinet"]},
        source_hash="classification-hash",
        rules_version="v2",
        classified_at=now,
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    db_session.add_all([article, classification])
    db_session.flush()

    active_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    active_run.status = "queued"
    db_session.add(active_run)
    db_session.commit()

    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(
        settings,
        AISettingsUpdate(
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            summary_enabled=True,
            relevance_enabled=True,
            auto_enrich_new_items=True,
        ),
    )
    db_session.add(settings)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks._update_task_run_celery_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("active runs should not be duplicated")),
    )

    result = dispatch_items_missing_ai_enrichment.run()

    queued_runs = db_session.scalars(
        select(AITaskRun).where(
            AITaskRun.item_id == item.id,
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.status == "queued",
        )
    ).all()

    assert result == {"queued": 0}
    assert len(queued_runs) == 1
    get_settings.cache_clear()


def test_dispatch_feed_metadata_backfill_skips_old_prefix_and_finds_later_candidates(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    skipped_feed_one = Feed(
        id=uuid.uuid4(),
        name="Feed One",
        url="https://example.com/feed-one.xml",
        description="Feed one",
        site_url="https://example.com/site-one",
        enabled=True,
        fetch_interval_seconds=1800,
        created_at=now - timedelta(hours=3),
    )
    skipped_feed_two = Feed(
        id=uuid.uuid4(),
        name="Feed Two",
        url="https://example.com/feed-two.xml",
        description="Feed two",
        site_url="https://example.com/site-two",
        enabled=True,
        fetch_interval_seconds=1800,
        created_at=now - timedelta(hours=2),
    )
    candidate_feed = Feed(
        id=uuid.uuid4(),
        name="",
        url="https://example.com/feed-three.xml",
        description="Feed three",
        site_url=None,
        enabled=True,
        fetch_interval_seconds=1800,
        created_at=now - timedelta(hours=1),
    )
    db_session.add_all([skipped_feed_one, skipped_feed_two, candidate_feed])
    db_session.commit()

    queued_feed_ids: list[str] = []

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_feed_metadata_scan_limit", 1)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_feed_metadata_queue_limit", 10)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.backfill_feed_metadata.delay",
        lambda feed_id: queued_feed_ids.append(feed_id),
    )

    result = dispatch_feed_metadata_backfill.run()

    assert result == {"queued": 1}
    assert queued_feed_ids == [str(candidate_feed.id)]


def test_dispatch_feed_metadata_backfill_queues_url_placeholder_names_with_site_urls(db_session, monkeypatch):
    placeholder_url = "https://example.com/placeholder.xml"
    feed = Feed(
        id=uuid.uuid4(),
        name=placeholder_url,
        url=placeholder_url,
        description="Feed description",
        site_url="https://example.com",
        enabled=True,
        fetch_interval_seconds=1800,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(feed)
    db_session.commit()

    queued_feed_ids: list[str] = []

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_feed_metadata_scan_limit", 10)
    monkeypatch.setattr("app.tasks.feed_tasks.settings.dispatch_feed_metadata_queue_limit", 10)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.backfill_feed_metadata.delay",
        lambda feed_id: queued_feed_ids.append(feed_id),
    )

    result = dispatch_feed_metadata_backfill.run()

    assert result == {"queued": 1}
    assert queued_feed_ids == [str(feed.id)]


def test_fetch_article_recovers_existing_article_after_soft_failure(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Recovered feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="recoverable-item",
        url="https://example.com/articles/recoverable",
        canonical_url="https://example.com/articles/recoverable",
        title="Recoverable item",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="recoverable-item",
        content_hash="b" * 64,
        status="error",
        last_error="non_html_response",
    )
    db_session.add_all([feed, item])
    db_session.flush()
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        content_type="application/json",
        retrieved_at=datetime.now(timezone.utc) - timedelta(hours=2),
        error="non_html_response",
        text=None,
        extraction_method="none",
    )
    db_session.add(article)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    @contextmanager
    def _domain_slot_override(_domain: str, max_wait_seconds: int = 30):
        _ = max_wait_seconds
        yield

    class _Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        url = item.url

        def iter_bytes(self):
            yield b"<html><body><article><p>Recovered readable text.</p></article></body></html>"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    queued: list[str] = []

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.domain_slot", _domain_slot_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr("app.tasks.feed_tasks.classify_item.delay", lambda queued_item_id: queued.append(queued_item_id))
    monkeypatch.setattr("app.tasks.feed_tasks.extract_canonical_url", lambda _html: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_readable_text",
        lambda _html: {
            "title": "Recovered item",
            "text": "Recovered readable text.",
            "method": "readable",
            "language": "en",
            "word_count": 3,
            "error": None,
        },
    )

    result = fetch_article.run(str(item.id))

    assert result == {"status": "ok", "item_id": str(item.id)}
    db_session.refresh(item)
    db_session.refresh(article)
    assert item.status == "content_fetched"
    assert item.last_error is None
    assert article.text == "Recovered readable text."
    assert article.error is None
    assert article.content_type == "text/html; charset=utf-8"
    assert queued == [str(item.id)]


def test_fetch_article_uses_rss_summary_when_article_fetch_is_blocked_and_can_retry(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Blocked feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="blocked-item",
        url="https://example.com/articles/blocked",
        canonical_url="https://example.com/articles/blocked",
        title="Blocked article",
        summary="<p>RSS summary with <strong>usable context</strong> while the source blocks extraction.</p>",
        published_at=datetime.now(timezone.utc),
        dedupe_key="blocked-item",
        content_hash="d" * 64,
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

    class _BlockedResponse:
        status_code = 403
        headers = {"content-type": "text/html; charset=utf-8"}
        url = item.url

        def iter_bytes(self):
            yield b"<html><title>Just a moment...</title><body>Cloudflare challenge</body></html>"

        def close(self):
            pass

    class _SuccessResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        url = item.url

        def iter_bytes(self):
            yield b"<html><body><article><p>Full recovered article text.</p></article></body></html>"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    responses = [_BlockedResponse(), _SuccessResponse()]
    queued: list[str] = []

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.domain_slot", _domain_slot_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("app.tasks.feed_tasks.classify_item.delay", lambda queued_item_id: queued.append(queued_item_id))
    monkeypatch.setattr("app.tasks.feed_tasks.extract_canonical_url", lambda _html: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_readable_text",
        lambda _html: {
            "title": "Recovered article",
            "text": "Full recovered article text.",
            "method": "readable",
            "language": "en",
            "word_count": 4,
            "error": None,
        },
    )

    blocked_result = fetch_article.run(str(item.id))

    assert blocked_result == {
        "status": "degraded",
        "reason": "rss_summary_fallback",
        "item_id": str(item.id),
    }
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    assert article is not None
    db_session.refresh(item)
    assert item.status == "content_fetched"
    assert item.last_error == "http_status:403"
    assert article.text == "RSS summary with\nusable context\nwhile the source blocks extraction."
    assert article.extraction_method == "rss_summary_fallback"
    assert article.error == "http_status:403"

    retry_result = fetch_article.run(str(item.id))

    assert retry_result == {"status": "ok", "item_id": str(item.id)}
    db_session.refresh(item)
    db_session.refresh(article)
    assert item.status == "content_fetched"
    assert item.last_error is None
    assert article.text == "Full recovered article text."
    assert article.extraction_method == "readable"
    assert article.error is None
    assert queued == [str(item.id), str(item.id)]


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


def test_fetch_article_keeps_committed_article_state_when_classification_enqueue_fails(db_session, monkeypatch, caplog):
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
        source_guid="enqueue-failure-item",
        url="https://example.com/articles/enqueue-failure",
        canonical_url="https://example.com/articles/enqueue-failure",
        title="Queue boundary target",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="enqueue-failure-item",
        content_hash="c" * 64,
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
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        url = item.url

        def iter_bytes(self):
            yield b"<html><body><article><p>Recovered readable text.</p></article></body></html>"

        def close(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.domain_slot", _domain_slot_override)
    monkeypatch.setattr("app.tasks.feed_tasks.build_safe_http_client", lambda *args, **kwargs: _Client())
    monkeypatch.setattr("app.tasks.feed_tasks.safe_stream_with_redirects", lambda *_args, **_kwargs: _Response())
    def _raise_enqueue_failure(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr("app.tasks.feed_tasks.classify_item.delay", _raise_enqueue_failure)
    monkeypatch.setattr("app.tasks.feed_tasks.extract_canonical_url", lambda _html: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_readable_text",
        lambda _html: {
            "title": "Recovered article",
            "text": "Recovered readable text.",
            "method": "readable",
            "language": "en",
            "word_count": 3,
            "error": None,
        },
    )

    with caplog.at_level("ERROR"):
        result = fetch_article.run(str(item.id))

    assert result == {"status": "ok", "item_id": str(item.id)}
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    assert article is not None
    assert article.text == "Recovered readable text."
    db_session.refresh(item)
    assert item.status == "content_fetched"
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
    assert run.error == "task_queue_unavailable"


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


def test_classify_item_skips_ai_enrichment_for_old_feed_backlog(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
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
        source_guid="old-auto-ai-backlog",
        url="https://example.com/articles/old-auto-ai-backlog",
        canonical_url="https://example.com/articles/old-auto-ai-backlog",
        title="Fortinet exploitation observed",
        summary="Summary",
        published_at=now - timedelta(days=10),
        first_seen_at=now,
        dedupe_key="old-auto-ai-backlog",
        content_hash="d" * 64,
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
    monkeypatch.setattr("app.tasks.feed_tasks.settings.ai_auto_enrich_new_item_max_age_hours", 24)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.load_active_ai_settings",
        lambda _db: SimpleNamespace(
            ai_enabled=True,
            ai_configured=True,
            auto_enrich_new_items=True,
            model="local-threat-model",
        ),
    )
    monkeypatch.setattr("app.tasks.feed_tasks.dispatch_alert_match_notification_webhooks.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.tasks.feed_tasks.extract_item_iocs.delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("old backlog should not be auto-enriched")),
    )

    result = classify_item.run(str(item.id))

    skipped_run = db_session.scalar(
        select(AITaskRun).where(
            AITaskRun.item_id == item.id,
            AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
            AITaskRun.status == "skipped",
        )
    )

    assert result["status"] == "ok"
    assert result["ai_enqueue_failed"] is False
    assert skipped_run is not None
    assert skipped_run.reason == "outside_auto_enrich_new_item_window"


def test_classify_item_skips_stale_article_after_refetch(db_session, monkeypatch):
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
        source_guid="stale-classification-item",
        url="https://example.com/articles/stale-classification-item",
        canonical_url="https://example.com/articles/stale-classification-item",
        title="Fortinet exploitation observed",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="stale-classification-item",
        content_hash="1" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Old article text that should not win.",
        extraction_method="readable",
        retrieved_at=datetime.now(timezone.utc) - timedelta(minutes=5),
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
        lambda _db: SimpleNamespace(
            ai_enabled=True,
            ai_configured=True,
            auto_enrich_new_items=True,
            model="local-threat-model",
        ),
    )
    monkeypatch.setattr("app.tasks.feed_tasks.sync_item_algorithm_tags", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_item_iocs.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale classification should not enqueue IOC extraction")),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale classification should not enqueue AI enrichment")),
    )

    def _classify_item_content(**_kwargs):
        current_article = db_session.scalar(select(Article).where(Article.item_id == item.id))
        assert current_article is not None
        current_article.text = "New article text from a refetch."
        current_article.retrieved_at = datetime.now(timezone.utc)
        db_session.add(current_article)
        db_session.flush()
        return SimpleNamespace(
            primary_category="vulnerability",
            secondary_categories=[],
            confidence=0.91,
            scores={"vulnerability": 6.0},
            matched_terms={"vulnerability": ["article:exploit"]},
            source_hash="stale-source-hash",
            rules_version="v2",
        )

    monkeypatch.setattr("app.tasks.feed_tasks.classify_item_content", _classify_item_content)

    result = classify_item.run(str(item.id))

    classification = db_session.scalar(select(ItemClassification).where(ItemClassification.item_id == item.id))

    assert result == {"status": "skipped", "reason": "article_refetched", "item_id": str(item.id)}
    assert classification is None


def test_classify_item_skips_when_item_lock_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    item_id = uuid.uuid4()

    @contextmanager
    def _db_session_override():
        yield object()

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks._claim_item_article_processing_target",
        lambda _db, *, item_id: (None, "already_running"),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.classify_item_content",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("locked items should not be classified")),
    )

    result = classify_item.run(str(item_id))

    assert result == {"status": "skipped", "reason": "already_running", "item_id": str(item_id)}


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


def test_reprocess_recent_ai_items_uses_published_time_before_first_seen(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.flush()

    recent_published = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="recent-published",
        url="https://example.com/articles/recent-published",
        canonical_url="https://example.com/articles/recent-published",
        title="Recent published article",
        summary="Summary",
        published_at=now - timedelta(days=1),
        first_seen_at=now - timedelta(days=1),
        dedupe_key="recent-published",
        content_hash="a" * 64,
        status="content_fetched",
    )
    old_published_recent_seen = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="old-published-recent-seen",
        url="https://example.com/articles/old-published-recent-seen",
        canonical_url="https://example.com/articles/old-published-recent-seen",
        title="Old published but newly discovered article",
        summary="Summary",
        published_at=now - timedelta(days=180),
        first_seen_at=now - timedelta(days=1),
        dedupe_key="old-published-recent-seen",
        content_hash="b" * 64,
        status="content_fetched",
    )
    undated_recent_seen = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="undated-recent-seen",
        url="https://example.com/articles/undated-recent-seen",
        canonical_url="https://example.com/articles/undated-recent-seen",
        title="Undated recently discovered article",
        summary="Summary",
        published_at=None,
        first_seen_at=now - timedelta(days=2),
        dedupe_key="undated-recent-seen",
        content_hash="c" * 64,
        status="content_fetched",
    )
    for item in (recent_published, old_published_recent_seen, undated_recent_seen):
        db_session.add(item)
        db_session.flush()
        db_session.add(
            Article(
                item_id=item.id,
                final_url=item.url,
                http_status=200,
                text="Researchers observed Fortinet exploitation in the wild.",
                extraction_method="readable",
            )
        )
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

    scheduled: list[str] = []

    class _FakeTask:
        def __init__(self, task_id: str):
            self.id = task_id

    def _fake_delay(item_id: str, force: bool = False, task_run_id: str | None = None):
        _ = force
        _ = task_run_id
        scheduled.append(item_id)
        return _FakeTask(f"child-{len(scheduled)}")

    monkeypatch.setattr("app.tasks.feed_tasks.generate_item_ai_enrichment_task.delay", _fake_delay)

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 10},
    )
    db_session.commit()

    result = reprocess_recent_ai_items.run(7, 10, task_run_id=str(parent_run.id))

    assert result["queued"] == 2
    assert scheduled == [str(recent_published.id), str(undated_recent_seen.id)]
    assert str(old_published_recent_seen.id) not in scheduled

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert refreshed_parent is not None
    assert refreshed_parent.target_count == 2
    assert refreshed_parent.metadata_json["date_basis"] == "published_at_or_first_seen_at"
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
    assert refreshed_child.error == "unexpected_error"

    assert refreshed_parent is not None
    assert refreshed_parent.processed_count == 1
    assert refreshed_parent.error_count == 1
    assert refreshed_parent.status == "error"


def test_generate_item_ai_enrichment_task_finishes_canceled_runs_before_work(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_item_ai_enrichment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("task should not continue after cancellation")),
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
        source_guid="canceled-before-run",
        url="https://example.com/articles/canceled-before-run",
        canonical_url="https://example.com/articles/canceled-before-run",
        title="Canceled before worker body",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="canceled-before-run",
        content_hash="1" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.flush()

    child_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    child_run.reason = "cancel_requested"
    child_run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
    db_session.add(child_run)
    db_session.commit()

    result = generate_item_ai_enrichment_task.run(str(child_run.item_id), force=True, task_run_id=str(child_run.id))

    db_session.expire_all()
    refreshed_child = db_session.scalar(select(AITaskRun).where(AITaskRun.id == child_run.id))

    assert result == {"status": "skipped", "reason": "canceled", "item_id": str(child_run.item_id)}
    assert refreshed_child is not None
    assert refreshed_child.status == "skipped"
    assert refreshed_child.reason == "canceled"
    assert refreshed_child.metadata_json["cancel_observed_at"]


def test_generate_item_ai_enrichment_task_skips_already_terminal_runs_before_work(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.run_item_ai_enrichment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("task should not continue after terminalization")),
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
        source_guid="terminal-before-run",
        url="https://example.com/articles/terminal-before-run",
        canonical_url="https://example.com/articles/terminal-before-run",
        title="Terminalized before worker body",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="terminal-before-run",
        content_hash="2" * 64,
        status="content_fetched",
    )
    db_session.add_all([feed, item])
    db_session.flush()

    child_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    child_run.status = "error"
    child_run.reason = "stale_task_lost"
    child_run.error = "Task no longer appears in Celery and did not report completion"
    child_run.finished_at = datetime.now(timezone.utc)
    db_session.add(child_run)
    db_session.commit()

    result = generate_item_ai_enrichment_task.run(str(child_run.item_id), force=True, task_run_id=str(child_run.id))

    db_session.expire_all()
    refreshed_child = db_session.scalar(select(AITaskRun).where(AITaskRun.id == child_run.id))

    assert result == {"status": "skipped", "reason": "stale_task_lost", "item_id": str(child_run.item_id)}
    assert refreshed_child is not None
    assert refreshed_child.status == "error"
    assert refreshed_child.reason == "stale_task_lost"


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


def test_reprocess_recent_ai_items_can_target_old_published_items_explicitly(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
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
        source_guid="explicit-old-item",
        url="https://example.com/articles/explicit-old-item",
        canonical_url="https://example.com/articles/explicit-old-item",
        title="Explicit old article",
        summary="Summary",
        published_at=now - timedelta(days=30),
        first_seen_at=now - timedelta(days=30),
        dedupe_key="explicit-old-item",
        content_hash="e" * 64,
        status="content_fetched",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Researchers observed Fortinet exploitation in the wild.",
        extraction_method="readable",
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(item)
    db_session.flush()
    db_session.add(article)
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
        metadata={"days": None, "limit": 1},
    )
    db_session.commit()

    result = reprocess_recent_ai_items.run(
        None,
        1,
        None,
        None,
        None,
        [str(item.id)],
        task_run_id=str(parent_run.id),
    )

    assert result["queued"] == 1
    assert len(scheduled) == 1
    assert scheduled[0][0] == str(item.id)
    assert scheduled[0][1] is True
    assert scheduled[0][2]
    get_settings.cache_clear()


def test_reprocess_recent_ai_items_caps_explicit_item_ids_to_effective_limit(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("DISPATCH_AI_REPROCESS_BATCH_SIZE", "2")
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
            source_guid=f"capped-specific-{index}",
            url=f"https://example.com/articles/capped-specific-{index}",
            canonical_url=f"https://example.com/articles/capped-specific-{index}",
            title=f"Capped targeted article {index}",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
            dedupe_key=f"capped-specific-{index}",
            content_hash=str(index + 7) * 64,
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
        [str(item_ids[2]), str(item_ids[0]), str(item_ids[1])],
        task_run_id=str(parent_run.id),
    )

    assert result["queued"] == 2
    assert [scheduled_item_id for scheduled_item_id, _force, _task_run_id in scheduled] == [
        str(item_ids[2]),
        str(item_ids[0]),
    ]

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert refreshed_parent is not None
    assert refreshed_parent.target_count == 2
    assert refreshed_parent.metadata_json["truncated_item_count"] == 1
    get_settings.cache_clear()


def test_reprocess_recent_ai_items_stops_queueing_after_cancel(db_session, monkeypatch):
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
            source_guid=f"cancel-reprocess-{index}",
            url=f"https://example.com/articles/cancel-reprocess-{index}",
            canonical_url=f"https://example.com/articles/cancel-reprocess-{index}",
            title=f"Cancelable article {index}",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
            dedupe_key=f"cancel-reprocess-{index}",
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

    queued_item_ids: list[str] = []
    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": None, "limit": 100},
    )
    db_session.commit()

    def _fake_safe_queue(*, item_id, **_kwargs):
        queued_item_ids.append(str(item_id))
        if len(queued_item_ids) == 1:
            parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
            assert parent is not None
            parent.reason = "cancel_requested"
            parent.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
            db_session.add(parent)
            db_session.commit()
        return True

    monkeypatch.setattr("app.tasks.feed_tasks._safe_queue_item_ai_enrichment_run", _fake_safe_queue)

    result = reprocess_recent_ai_items.run(
        None,
        100,
        None,
        None,
        None,
        [str(item_id) for item_id in item_ids],
        task_run_id=str(parent_run.id),
    )

    assert result["reason"] == "canceled"
    assert queued_item_ids == [str(item_ids[0])]
    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))
    assert refreshed_parent is not None
    assert refreshed_parent.status == "skipped"
    assert refreshed_parent.reason == "canceled"


def test_reprocess_recent_ai_items_skips_already_terminal_parent_runs_before_queueing(db_session, monkeypatch):
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks._safe_queue_item_ai_enrichment_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("queueing should not continue after terminalization")),
    )

    parent_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPROCESS,
        trigger_source=AI_TRIGGER_MANUAL,
        metadata={"days": 7, "limit": 5},
    )
    parent_run.status = "error"
    parent_run.reason = "stale_task_lost"
    parent_run.error = "Task no longer appears in Celery and did not report completion"
    parent_run.finished_at = datetime.now(timezone.utc)
    db_session.add(parent_run)
    db_session.commit()

    result = reprocess_recent_ai_items.run(7, 5, task_run_id=str(parent_run.id))

    db_session.expire_all()
    refreshed_parent = db_session.scalar(select(AITaskRun).where(AITaskRun.id == parent_run.id))

    assert result == {"queued": 0, "reason": "stale_task_lost"}
    assert refreshed_parent is not None
    assert refreshed_parent.status == "error"
    assert refreshed_parent.reason == "stale_task_lost"
    get_settings.cache_clear()


def test_reconcile_ai_task_runs_repairs_stale_runs_without_ops_page_access(db_session, monkeypatch):
    item = Item(
        id=uuid.uuid4(),
        feed_id=uuid.uuid4(),
        source_guid="stale-reconcile-task",
        url="https://example.com/articles/stale-reconcile-task",
        canonical_url="https://example.com/articles/stale-reconcile-task",
        title="Stale reconcile target",
        dedupe_key="stale-reconcile-task",
        content_hash="a" * 64,
        status="content_fetched",
    )
    feed = Feed(
        id=item.feed_id,
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add_all([feed, item])
    db_session.commit()

    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        item_id=item.id,
    )
    run.status = "running"
    run.celery_task_id = "stale-task-id"
    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    run.started_at = stale_time
    run.queued_at = stale_time
    run.created_at = stale_time
    run.updated_at = stale_time
    db_session.add(run)
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.services.ai_ops._load_live_task_snapshot", lambda: (True, [], [], [], []))

    result = reconcile_ai_task_runs.run()

    db_session.expire_all()
    refreshed_run = db_session.scalar(select(AITaskRun).where(AITaskRun.id == run.id))
    assert result["reconciled"] == 1
    assert refreshed_run is not None
    assert refreshed_run.status == "error"
    assert refreshed_run.reason == "stale_task_lost"


def test_process_reserved_notification_deliveries_schedules_retryable_failures(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "notification_webhook_allow_admin_unrestricted", True)
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
        name="Retrying webhook",
        url_template="https://hooks.example.com/retry",
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
        delivery_state="pending",
        attempt_count=0,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=10,
        rendered_url="https://hooks.example.com/retry",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview=None,
        error=None,
        attempted_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add_all([webhook, delivery])
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notification_webhooks._send_rendered_notification_request",
        lambda _rendered: NotificationWebhookTestResponse(
            success=False,
            status_code=503,
            duration_ms=25,
            rendered_url="https://hooks.example.com/retry",
            rendered_method="POST",
            rendered_headers=[],
            rendered_query_params=[],
            rendered_body=None,
            response_body_preview="busy",
            error="HTTP 503",
        ),
    )

    captured: dict[str, object] = {}

    def _fake_enqueue(delivery_ids: list[uuid.UUID], *, countdown: int | None = None):
        captured["delivery_ids"] = delivery_ids
        captured["countdown"] = countdown
        return True

    monkeypatch.setattr("app.tasks.feed_tasks.enqueue_notification_webhook_delivery_processing", _fake_enqueue)

    delivered, failed = _process_reserved_notification_deliveries(db_session, [delivery.id])

    retry_deliveries = db_session.scalars(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.source_delivery_id == delivery.id)
        .order_by(NotificationWebhookDelivery.attempted_at.asc())
    ).all()

    assert delivered == 0
    assert failed == 1
    assert len(retry_deliveries) == 1
    assert retry_deliveries[0].delivery_kind == "retry"
    assert retry_deliveries[0].delivery_state == "pending"
    assert captured["delivery_ids"] == [retry_deliveries[0].id]
    assert captured["countdown"] == max(1, int(get_settings().notification_delivery_retry_backoff_seconds))


def test_process_reserved_notification_deliveries_skips_missing_rows(db_session, monkeypatch):
    missing_delivery_id = uuid.uuid4()
    existing_delivery_id = uuid.uuid4()

    def _fake_process(_db, *, delivery_id: uuid.UUID):
        if delivery_id == missing_delivery_id:
            raise ValueError("Webhook delivery not found")
        return SimpleNamespace(
            claimed=True,
            result=SimpleNamespace(success=True, status_code=204, error=None),
            delivery=SimpleNamespace(
                id=delivery_id,
                webhook_id=uuid.uuid4(),
                event_type_snapshot="rss_item_new",
            ),
        )

    monkeypatch.setattr("app.tasks.feed_tasks.process_notification_webhook_delivery", _fake_process)

    delivered, failed = _process_reserved_notification_deliveries(
        db_session,
        [missing_delivery_id, existing_delivery_id],
    )

    assert delivered == 1
    assert failed == 0


def test_extract_item_iocs_skips_stale_article_after_refetch(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="IOC Feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="ioc-stale",
        url="https://example.com/articles/ioc-stale",
        canonical_url="https://example.com/articles/ioc-stale",
        title="Indicators changed after refetch",
        summary="The stale extractor should back off.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ioc-stale",
        content_hash="4" * 64,
        status="content_fetched",
        ioc_extraction_state="completed",
    )
    article = Article(
        item_id=item.id,
        final_url=item.url,
        http_status=200,
        text="Old article text with 1.1.1.1.",
        extraction_method="readable",
        retrieved_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    existing_ioc = IOC(
        id=uuid.uuid4(),
        type="domain",
        value_raw="fresh.example",
        value_norm="fresh.example",
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    existing_link = ItemIOC(
        item_id=item.id,
        ioc_id=existing_ioc.id,
        source_section="article",
        occurrences=1,
        confidence=0.95,
    )
    db_session.add_all([feed, item])
    db_session.flush()
    db_session.add_all([article, existing_ioc, existing_link])
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks.sync_item_algorithm_tags",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale IOC extraction should not retag items")),
    )

    def _extract_iocs(**_kwargs):
        current_article = db_session.scalar(select(Article).where(Article.item_id == item.id))
        assert current_article is not None
        current_article.text = "New article text with 2.2.2.2."
        current_article.retrieved_at = datetime.now(timezone.utc)
        db_session.add(current_article)
        db_session.flush()
        return [
            SimpleNamespace(
                type="ipv4",
                value_raw="1.1.1.1",
                value_norm="1.1.1.1",
                source_section="article",
                confidence=1.0,
            )
        ]

    monkeypatch.setattr("app.tasks.feed_tasks.extract_iocs", _extract_iocs)

    result = extract_item_iocs.run(str(item.id))

    db_session.expire_all()
    refreshed_item = db_session.scalar(select(Item).where(Item.id == item.id))
    refreshed_links = db_session.scalars(select(ItemIOC).where(ItemIOC.item_id == item.id)).all()
    assert result == {"status": "skipped", "reason": "article_refetched", "item_id": str(item.id)}
    assert refreshed_item is not None
    assert refreshed_item.ioc_extraction_state == "completed"
    assert len(refreshed_links) == 1
    assert refreshed_links[0].ioc_id == existing_ioc.id


def test_extract_item_iocs_skips_when_item_lock_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    item_id = uuid.uuid4()

    @contextmanager
    def _db_session_override():
        yield object()

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr(
        "app.tasks.feed_tasks._claim_item_article_processing_target",
        lambda _db, *, item_id: (None, "already_running"),
    )
    monkeypatch.setattr(
        "app.tasks.feed_tasks.extract_iocs",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("locked items should not run IOC extraction")),
    )

    result = extract_item_iocs.run(str(item_id))

    assert result == {"status": "skipped", "reason": "already_running", "item_id": str(item_id)}


def test_extract_item_iocs_marks_empty_results_terminal_for_dispatch(db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="IOC Feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    completed_empty_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="ioc-empty",
        url="https://example.com/articles/ioc-empty",
        canonical_url="https://example.com/articles/ioc-empty",
        title="No indicators here",
        summary="Still nothing actionable",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ioc-empty",
        content_hash="1" * 64,
        status="content_fetched",
    )
    pending_item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="ioc-pending",
        url="https://example.com/articles/ioc-pending",
        canonical_url="https://example.com/articles/ioc-pending",
        title="Queued separately",
        summary="Pending IOC extraction",
        published_at=datetime.now(timezone.utc),
        dedupe_key="ioc-pending",
        content_hash="2" * 64,
        status="content_fetched",
    )
    completed_article = Article(
        item_id=completed_empty_item.id,
        final_url=completed_empty_item.url,
        http_status=200,
        text="No observables are present in this article.",
        extraction_method="readable",
    )
    pending_article = Article(
        item_id=pending_item.id,
        final_url=pending_item.url,
        http_status=200,
        text="Still waiting for the queued extraction task.",
        extraction_method="readable",
    )
    db_session.add_all([feed, completed_empty_item, pending_item])
    db_session.flush()
    db_session.add_all([completed_article, pending_article])
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.feed_tasks.extract_iocs", lambda **_kwargs: [])
    monkeypatch.setattr("app.tasks.feed_tasks.sync_item_algorithm_tags", lambda *_args, **_kwargs: None)

    result = extract_item_iocs.run(str(completed_empty_item.id))

    db_session.expire_all()
    refreshed_item = db_session.scalar(select(Item).where(Item.id == completed_empty_item.id))
    assert result == {"status": "ok", "item_id": str(completed_empty_item.id), "ioc_count": 0}
    assert refreshed_item is not None
    assert refreshed_item.ioc_extraction_state == "completed_empty"

    queued_item_ids: list[str] = []
    monkeypatch.setattr("app.tasks.feed_tasks.extract_item_iocs.delay", lambda item_id: queued_item_ids.append(item_id))

    dispatch_result = dispatch_items_missing_iocs.run()

    assert dispatch_result == {"queued": 1}
    assert queued_item_ids == [str(pending_item.id)]


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
