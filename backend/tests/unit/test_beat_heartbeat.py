from datetime import datetime, timedelta, timezone

from app.services import beat_heartbeat


def test_parse_beat_heartbeat_accepts_fresh_timestamp():
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    snapshot = beat_heartbeat.parse_beat_heartbeat(
        (now - timedelta(seconds=15)).isoformat(),
        stale_after_seconds=180,
        now=now,
    )

    assert snapshot.ok is True
    assert snapshot.age_seconds == 15
    assert snapshot.reason == "healthy"


def test_parse_beat_heartbeat_reports_missing_invalid_stale_and_future_values():
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    missing = beat_heartbeat.parse_beat_heartbeat(None, stale_after_seconds=180, now=now)
    invalid = beat_heartbeat.parse_beat_heartbeat("not-a-date", stale_after_seconds=180, now=now)
    stale = beat_heartbeat.parse_beat_heartbeat(
        (now - timedelta(seconds=181)).isoformat(),
        stale_after_seconds=180,
        now=now,
    )
    future = beat_heartbeat.parse_beat_heartbeat(
        (now + timedelta(seconds=6)).isoformat(),
        stale_after_seconds=180,
        now=now,
    )

    assert (missing.ok, missing.reason) == (False, "missing")
    assert (invalid.ok, invalid.reason) == (False, "invalid")
    assert (stale.ok, stale.age_seconds, stale.reason) == (False, 181, "stale")
    assert (future.ok, future.age_seconds, future.reason) == (False, None, "future")


def test_parse_beat_heartbeat_tolerates_small_clock_skew_and_naive_timestamp():
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    skewed = beat_heartbeat.parse_beat_heartbeat(
        (now + timedelta(seconds=5)).isoformat(),
        stale_after_seconds=180,
        now=now,
    )
    naive = beat_heartbeat.parse_beat_heartbeat(
        now.replace(tzinfo=None).isoformat(),
        stale_after_seconds=180,
        now=now,
    )

    assert (skewed.ok, skewed.age_seconds) == (True, 0)
    assert (naive.ok, naive.age_seconds) == (True, 0)


def test_read_beat_heartbeat_reports_redis_failure(monkeypatch):
    def fail_to_connect(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(beat_heartbeat.redis.Redis, "from_url", fail_to_connect)

    snapshot = beat_heartbeat.read_beat_heartbeat(
        redis_url="redis://redis:6379/0",
        heartbeat_key="beat",
        stale_after_seconds=180,
    )

    assert snapshot == beat_heartbeat.BeatHeartbeatSnapshot(False, None, None, "redis_unavailable")
