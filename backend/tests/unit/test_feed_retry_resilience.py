import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.feed import Feed
from app.services.feed_fetch_ownership import claim_feed_fetch
from app.tasks import feed_tasks
from app.tasks.article_fetch_tasks import _retryable_failure
from app.tasks.feed_fetch_tasks import (
    _recover_coordination_failure,
    _retry_feed_exception,
    run_fetch_feed,
)
from app.tasks.feed_task_coordination import (
    CoordinationUnavailableError,
    LeaseOwnershipLostError,
)


class _ExhaustedRetryTask:
    request = SimpleNamespace(retries=3)

    def retry(self, **_kwargs):
        raise AssertionError("an exhausted task must not request another retry")


class _UnexpectedRetryTask:
    request = SimpleNamespace(retries=0)

    def retry(self, **_kwargs):
        raise AssertionError("confirmed ownership loss must not be retried")


def test_feed_lock_ownership_loss_skips_without_retry(monkeypatch):
    @contextmanager
    def lost_feed_lock(_feed_id: str, ttl_seconds: int = 900):
        _ = ttl_seconds
        raise LeaseOwnershipLostError("coordination lease ownership was lost")
        yield  # pragma: no cover

    monkeypatch.setattr(feed_tasks, "feed_lock", lost_feed_lock)

    result = run_fetch_feed(
        _UnexpectedRetryTask(),
        str(uuid.uuid4()),
        runtime=feed_tasks,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "fetch_ownership_lost"


def test_coordination_retry_exhaustion_rolls_back_and_takes_fresh_db_claim(
    db_session,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Coordination recovery feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()
    stale_claim = claim_feed_fetch(db_session, feed=feed)
    db_session.commit()

    feed.last_error = "uncommitted-stale-state"
    result = _retry_feed_exception(
        _ExhaustedRetryTask(),
        db_session,
        feed,
        str(feed.id),
        feed.id,
        feed.url_digest,
        CoordinationUnavailableError("redis unavailable"),
        stale_claim,
        object(),
        coordination=True,
        runtime=feed_tasks,
    )

    assert result == {
        "status": "error",
        "feed_id": str(feed.id),
        "reason": "coordination_unavailable",
    }
    db_session.expire_all()
    stored_feed = db_session.get(Feed, feed.id)
    assert stored_feed is not None
    assert stored_feed.fetch_fence == stale_claim.fence + 1
    assert stored_feed.last_error == "coordination_unavailable"
    assert stored_feed.dispatch_backoff_until is not None
    assert stored_feed.next_fetch_at == stored_feed.dispatch_backoff_until


def test_outer_coordination_retry_exhaustion_reschedules_without_retry(
    db_session,
    monkeypatch,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Outer coordination recovery feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()

    @contextmanager
    def test_session():
        yield db_session

    monkeypatch.setattr(feed_tasks, "db_session", test_session)
    result = _recover_coordination_failure(
        _ExhaustedRetryTask(),
        str(feed.id),
        CoordinationUnavailableError("redis unavailable"),
        runtime=feed_tasks,
    )

    assert result["reason"] == "coordination_unavailable"
    db_session.expire_all()
    stored_feed = db_session.get(Feed, feed.id)
    assert stored_feed.dispatch_backoff_until is not None
    assert stored_feed.next_fetch_at == stored_feed.dispatch_backoff_until


def test_article_retry_exhaustion_returns_storable_error_without_retry():
    result = _retryable_failure(
        _ExhaustedRetryTask(),
        "item-1",
        "https://example.com/article",
        TimeoutError("timed out"),
        False,
        ["https://example.com/article"],
        0,
        runtime=feed_tasks,
    )

    assert result.error == "network_or_rate_limit_error"


def test_domain_slot_lease_loss_clears_feed_dispatch_claim_with_short_backoff(
    db_session,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name="Domain lease recovery feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        dispatch_claimed_at=now,
    )
    db_session.add(feed)
    db_session.commit()
    feed_lease = object()
    domain_lease = object()

    @contextmanager
    def test_session():
        yield db_session

    @contextmanager
    def test_feed_lock(_feed_id: str):
        yield feed_lease

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        url = "https://example.com/feed.xml"

        def close(self):
            pass

    def ensure_owned(lease):
        if lease is domain_lease:
            raise LeaseOwnershipLostError("domain slot ownership was lost")

    monkeypatch.setattr(feed_tasks, "db_session", test_session)
    monkeypatch.setattr(feed_tasks, "feed_lock", test_feed_lock)
    monkeypatch.setattr(
        feed_tasks,
        "build_safe_http_client",
        lambda **_kwargs: Client(),
    )
    monkeypatch.setattr(
        feed_tasks,
        "safe_stream_with_redirects",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        feed_tasks,
        "safe_fetch_request_guard",
        lambda _response: domain_lease,
    )
    monkeypatch.setattr(feed_tasks, "ensure_lease_owned", ensure_owned)

    result = run_fetch_feed(
        _UnexpectedRetryTask(),
        str(feed.id),
        force=True,
        runtime=feed_tasks,
    )

    assert result == {
        "status": "error",
        "reason": "coordination_unavailable",
        "feed_id": str(feed.id),
    }
    db_session.expire_all()
    stored_feed = db_session.get(Feed, feed.id)
    assert stored_feed.dispatch_claimed_at is None
    assert stored_feed.dispatch_backoff_until is not None
    assert now < stored_feed.dispatch_backoff_until <= now + timedelta(minutes=2)
    assert stored_feed.next_fetch_at == stored_feed.dispatch_backoff_until


def test_confirmed_coordination_ownership_loss_rolls_back_without_retry(
    db_session,
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Lost ownership feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()
    claim = claim_feed_fetch(db_session, feed=feed)
    db_session.commit()

    feed.last_error = "must-not-commit"
    result = _retry_feed_exception(
        _UnexpectedRetryTask(),
        db_session,
        feed,
        str(feed.id),
        feed.id,
        feed.url_digest,
        LeaseOwnershipLostError("coordination lease ownership was lost"),
        claim,
        object(),
        coordination=True,
        runtime=feed_tasks,
    )

    assert result == {
        "status": "skipped",
        "reason": "fetch_ownership_lost",
        "feed_id": str(feed.id),
    }
    db_session.expire_all()
    stored_feed = db_session.get(Feed, feed.id)
    assert stored_feed is not None
    assert stored_feed.fetch_fence == claim.fence
    assert stored_feed.last_error is None
