from dataclasses import dataclass
from datetime import datetime, timezone

import redis

_FUTURE_TOLERANCE_SECONDS = 5


@dataclass(frozen=True)
class BeatHeartbeatSnapshot:
    ok: bool
    heartbeat_at: str | None
    age_seconds: int | None
    reason: str


def read_beat_heartbeat(
    *,
    redis_url: str,
    heartbeat_key: str,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> BeatHeartbeatSnapshot:
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        heartbeat_raw = client.get(heartbeat_key)
    except Exception:
        return BeatHeartbeatSnapshot(False, None, None, "redis_unavailable")

    return parse_beat_heartbeat(
        heartbeat_raw,
        stale_after_seconds=stale_after_seconds,
        now=now,
    )


def parse_beat_heartbeat(
    heartbeat_raw: str | None,
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> BeatHeartbeatSnapshot:
    if not heartbeat_raw:
        return BeatHeartbeatSnapshot(False, None, None, "missing")

    try:
        heartbeat_at = datetime.fromisoformat(heartbeat_raw)
    except (TypeError, ValueError):
        return BeatHeartbeatSnapshot(False, heartbeat_raw, None, "invalid")

    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)

    delta_seconds = (reference_time - heartbeat_at).total_seconds()
    if delta_seconds < -_FUTURE_TOLERANCE_SECONDS:
        return BeatHeartbeatSnapshot(False, heartbeat_raw, None, "future")

    age_seconds = max(0, int(delta_seconds))
    if age_seconds > stale_after_seconds:
        return BeatHeartbeatSnapshot(False, heartbeat_raw, age_seconds, "stale")
    return BeatHeartbeatSnapshot(True, heartbeat_raw, age_seconds, "healthy")
