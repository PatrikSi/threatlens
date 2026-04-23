import pytest
import redis

from app.tasks import feed_tasks


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
    def incr(self, _key: str):
        return feed_tasks.settings.per_domain_concurrency + 1

    def expire(self, _key: str, _ttl: int):
        return None

    def decr(self, _key: str):
        return feed_tasks.settings.per_domain_concurrency


class _ExpireFailsAfterIncrementRedis:
    def __init__(self):
        self.values: dict[str, int] = {}

    def incr(self, key: str):
        current = int(self.values.get(key, 0)) + 1
        self.values[key] = current
        return current

    def expire(self, _key: str, _ttl: int):
        raise redis.RedisError("ttl unavailable")

    def decr(self, key: str):
        current = int(self.values.get(key, 0)) - 1
        if current <= 0:
            self.values.pop(key, None)
            return current
        self.values[key] = current
        return current

    def delete(self, key: str):
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0


def test_domain_slot_raises_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_tasks, "redis_client", _UnavailableRedis())

    with pytest.raises(feed_tasks.CoordinationUnavailableError, match="domain slot unavailable"):
        with feed_tasks.domain_slot("example.com"):
            pass


def test_domain_slot_still_times_out_under_sustained_contention(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_tasks, "redis_client", _SaturatedRedis())
    monkeypatch.setattr(feed_tasks.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="domain slot timeout"):
        with feed_tasks.domain_slot("example.com", max_wait_seconds=0.01):
            pass


def test_domain_slot_rolls_back_increment_when_ttl_application_fails(monkeypatch: pytest.MonkeyPatch):
    redis_client = _ExpireFailsAfterIncrementRedis()
    monkeypatch.setattr(feed_tasks, "redis_client", redis_client)

    with pytest.raises(feed_tasks.CoordinationUnavailableError, match="domain slot unavailable"):
        with feed_tasks.domain_slot("example.com"):
            pass

    assert redis_client.values == {}


def test_feed_lock_allows_best_effort_progress_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_tasks, "redis_client", _UnavailableRedis())

    with pytest.raises(feed_tasks.CoordinationUnavailableError, match="feed lock unavailable"):
        with feed_tasks.feed_lock("feed-1") as acquired:
            assert acquired is True
