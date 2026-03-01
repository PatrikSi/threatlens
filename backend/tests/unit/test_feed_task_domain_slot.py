import pytest
import redis

from app.tasks import feed_tasks


class _UnavailableRedis:
    def incr(self, _key: str):
        raise redis.RedisError("redis unavailable")

    def expire(self, _key: str, _ttl: int):
        return None

    def decr(self, _key: str):
        return 0


class _SaturatedRedis:
    def incr(self, _key: str):
        return feed_tasks.settings.per_domain_concurrency + 1

    def expire(self, _key: str, _ttl: int):
        return None

    def decr(self, _key: str):
        return feed_tasks.settings.per_domain_concurrency


def test_domain_slot_degrades_gracefully_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_tasks, "redis_client", _UnavailableRedis())

    with feed_tasks.domain_slot("example.com"):
        pass


def test_domain_slot_still_times_out_under_sustained_contention(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(feed_tasks, "redis_client", _SaturatedRedis())
    monkeypatch.setattr(feed_tasks.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="domain slot timeout"):
        with feed_tasks.domain_slot("example.com", max_wait_seconds=0.01):
            pass
