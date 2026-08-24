import time
from contextlib import contextmanager

import pytest
import redis

from app.tasks import feed_task_coordination, feed_tasks
from app.tasks.article_fetch_tasks import _fetch_candidate
from app.tasks.feed_task_coordination import LeaseOwnershipLostError


class _UnavailableRedis:
    def incr(self, _key: str):
        raise redis.RedisError("redis unavailable")

    def expire(self, _key: str, _ttl: int):
        return None

    def set(self, _key: str, _value: str, nx: bool, ex: int):
        _ = (nx, ex)
        raise redis.RedisError("redis unavailable")

    def decr(self, _key: str):
        return 0

    def delete(self, _key: str):
        return 1


class _SaturatedRedis:
    def set(self, _key: str, _value: str, *, nx: bool = False, ex: int | None = None):
        _ = (nx, ex)
        return False

    def get(self, _key: str):
        return "other-worker-token"

    def ttl(self, _key: str):
        return feed_tasks.DOMAIN_SLOT_TTL_SECONDS


class _SetFailsRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set(self, _key: str, _value: str, *, nx: bool = False, ex: int | None = None):
        _ = (nx, ex)
        raise redis.RedisError("lease unavailable")


def test_domain_slot_raises_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_task_coordination, "redis_client", _UnavailableRedis())

    with pytest.raises(feed_tasks.CoordinationUnavailableError, match="domain slot unavailable"):
        with feed_tasks.domain_slot("example.com"):
            pass


def test_domain_slot_still_times_out_under_sustained_contention(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_task_coordination, "redis_client", _SaturatedRedis())
    monkeypatch.setattr(feed_task_coordination.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="domain slot timeout"):
        with feed_tasks.domain_slot("example.com", max_wait_seconds=0.01):
            pass


def test_domain_slot_raises_when_lease_write_fails(monkeypatch: pytest.MonkeyPatch):
    redis_client = _SetFailsRedis()
    monkeypatch.setattr(feed_task_coordination, "redis_client", redis_client)

    with pytest.raises(feed_tasks.CoordinationUnavailableError, match="domain slot unavailable"):
        with feed_tasks.domain_slot("example.com"):
            pass

    assert redis_client.values == {}


def test_feed_lock_allows_best_effort_progress_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_task_coordination, "redis_client", _UnavailableRedis())

    with pytest.raises(feed_tasks.CoordinationUnavailableError, match="feed lock unavailable"):
        with feed_tasks.feed_lock("feed-1") as acquired:
            assert acquired is True


def test_article_stream_aborts_when_domain_lease_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
):
    lease = object()
    ownership_checks = 0

    @contextmanager
    def domain_slot(_domain: str):
        yield lease

    def ensure_owned(current_lease):
        nonlocal ownership_checks
        assert current_lease is lease
        ownership_checks += 1
        if ownership_checks >= 3:
            raise LeaseOwnershipLostError(
                "coordination lease ownership was lost"
            )

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        url = "https://example.com/article"
        closed = False

        def iter_bytes(self):
            yield b"<html>"
            yield b"article</html>"

        def close(self):
            self.closed = True

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    response = Response()
    monkeypatch.setattr(feed_tasks, "domain_slot", domain_slot)
    monkeypatch.setattr(feed_tasks, "ensure_lease_owned", ensure_owned)
    monkeypatch.setattr(
        feed_tasks,
        "build_safe_http_client",
        lambda *args, **kwargs: Client(),
    )
    monkeypatch.setattr(
        feed_tasks,
        "safe_stream_with_redirects",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(LeaseOwnershipLostError):
        _fetch_candidate("https://example.com/article", runtime=feed_tasks)

    assert response.closed is True


def test_lease_scripts_are_atomic_with_real_redis(
    test_redis_url: str | None,
    monkeypatch: pytest.MonkeyPatch,
):
    if test_redis_url is None:
        pytest.skip("real Redis is unavailable")

    redis_client = redis.Redis.from_url(test_redis_url, decode_responses=True)
    redis_client.flushdb()
    monkeypatch.setattr(feed_task_coordination, "redis_client", redis_client)
    feed_key = "threatlens:feed:lock:lua-test"
    heartbeat_key = f"{feed_key}:heartbeat"

    try:
        with feed_tasks.feed_lock("lua-test", ttl_seconds=5) as lease:
            assert redis_client.get(feed_key) == lease.token
            assert redis_client.get(heartbeat_key).startswith(f"{lease.token}|")

            redis_client.set(feed_key, "replacement-token", ex=5)
            redis_client.set(
                heartbeat_key,
                f"replacement-token|{time.time():.6f}",
                ex=5,
            )
            with pytest.raises(LeaseOwnershipLostError):
                lease.ensure_owned()

        assert redis_client.get(feed_key) == "replacement-token"
        assert redis_client.get(heartbeat_key).startswith("replacement-token|")
    finally:
        redis_client.flushdb()
        redis_client.close()
