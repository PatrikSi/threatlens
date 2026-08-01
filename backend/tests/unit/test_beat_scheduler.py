from datetime import datetime, timezone

import redis

from app.tasks import beat_scheduler


class _RedisClient:
    def __init__(self):
        self.writes = []

    def set(self, key, value, *, ex):
        self.writes.append((key, value, ex))


def test_write_scheduler_heartbeat_records_timestamp_with_ttl():
    client = _RedisClient()
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    ok = beat_scheduler.write_scheduler_heartbeat(
        client,
        heartbeat_key="beat:scheduler",
        ttl_seconds=180,
        now=now,
    )

    assert ok is True
    assert client.writes == [("beat:scheduler", now.isoformat(), 180)]


def test_write_scheduler_heartbeat_tolerates_redis_failure():
    class _UnavailableRedis:
        def set(self, *_args, **_kwargs):
            raise redis.RedisError("unavailable")

    assert (
        beat_scheduler.write_scheduler_heartbeat(
            _UnavailableRedis(),
            heartbeat_key="beat:scheduler",
            ttl_seconds=180,
        )
        is False
    )


def test_watchdog_scheduler_writes_heartbeat_after_successful_tick(monkeypatch):
    client = _RedisClient()
    scheduler = object.__new__(beat_scheduler.WatchdogPersistentScheduler)
    scheduler._heartbeat_client = client
    scheduler._heartbeat_key = "beat:scheduler"
    scheduler._heartbeat_ttl_seconds = 180
    monkeypatch.setattr(beat_scheduler.PersistentScheduler, "tick", lambda _self, *_args, **_kwargs: 7.5)

    next_interval = scheduler.tick()

    assert next_interval == 7.5
    assert len(client.writes) == 1
