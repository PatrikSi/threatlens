"""Bounded operational counters shared across API and worker processes.

Only fixed event names and minute buckets are stored. No user, destination,
query, exception text or item identifier becomes a metric label.
"""

from __future__ import annotations

import time
from functools import lru_cache

import redis

from app.core.config import get_settings


RUNTIME_EVENTS = frozenset({
    "database_lock_timeout", "database_statement_timeout", "database_deadline",
    "database_pool_timeout", "outbound_deadline", "export_transfer_deadline",
    "export_generation_deadline",
})
METRIC_WINDOW_MINUTES = 15
_PREFIX = "threatlens:runtime-metrics:v1:"


@lru_cache(maxsize=4)
def _client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url, socket_connect_timeout=0.1, socket_timeout=0.1,
        decode_responses=True, retry_on_timeout=False, max_connections=2,
    )


def record_runtime_event(event: str) -> None:
    if event not in RUNTIME_EVENTS:
        raise ValueError("Unknown runtime metric")
    try:
        key = f"{_PREFIX}{int(time.time()) // 60}"
        with _client(get_settings().redis_url).pipeline(transaction=True) as pipe:
            pipe.hincrby(key, event)
            pipe.expire(key, 3600)
            pipe.execute()
    except (redis.RedisError, OSError, ValueError):
        # Telemetry must not change whether an operation succeeds or can roll
        # back. Missing telemetry is exposed as unknown at collection time.
        pass


def collect_runtime_events(*, now: float | None = None) -> dict[str, int | None]:
    fields = {f"{event}_last_15m": None for event in RUNTIME_EVENTS}
    minute = int(time.time() if now is None else now) // 60
    try:
        with _client(get_settings().redis_url).pipeline(transaction=False) as pipe:
            for bucket in range(minute - METRIC_WINDOW_MINUTES + 1, minute + 1):
                pipe.hmget(f"{_PREFIX}{bucket}", sorted(RUNTIME_EVENTS))
            rows = pipe.execute()
        for index, event in enumerate(sorted(RUNTIME_EVENTS)):
            values = [int(row[index] or 0) for row in rows]
            fields[f"{event}_last_15m"] = min(2_000_000_000, sum(max(0, value) for value in values))
    except (redis.RedisError, OSError, ValueError, TypeError, IndexError):
        pass
    return fields
