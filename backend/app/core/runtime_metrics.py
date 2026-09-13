"""Bounded operational counters shared across API and worker processes.

Only fixed event names and minute buckets are stored. No user, destination,
query, exception text or item identifier becomes a metric label.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
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
_COLLECTION_TIMEOUT_SECONDS = 0.2
_delivery_slots = threading.BoundedSemaphore(2)
_collection_slots = threading.BoundedSemaphore(1)


@lru_cache(maxsize=4)
def _client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url, socket_connect_timeout=0.1, socket_timeout=0.1,
        decode_responses=True, retry_on_timeout=False, max_connections=2,
    )


def _reset_metric_io_after_fork() -> None:
    global _delivery_slots, _collection_slots
    _delivery_slots = threading.BoundedSemaphore(2)
    _collection_slots = threading.BoundedSemaphore(1)
    _client.cache_clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_metric_io_after_fork)


def _start_metric_io(
    operation: Callable[[], None], slots: threading.BoundedSemaphore
) -> bool:
    # Redis socket timeouts do not cover system DNS. Keep that work outside the
    # caller, with fixed concurrency and no queued work if resolvers get stuck.
    if not slots.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            operation()
        except Exception:
            # Best-effort telemetry cannot replace the caller's result/error.
            pass
        finally:
            slots.release()

    try:
        threading.Thread(target=run, name="runtime-metrics", daemon=True).start()
    except Exception:
        slots.release()
        return False
    return True


def _write_runtime_event(event: str, key: str, redis_url: str) -> None:
    with _client(redis_url).pipeline(transaction=True) as pipe:
        pipe.hincrby(key, event)
        pipe.expire(key, 3600)
        pipe.execute()


def record_runtime_event(event: str) -> None:
    if event not in RUNTIME_EVENTS:
        raise ValueError("Unknown runtime metric")
    try:
        key = f"{_PREFIX}{int(time.time()) // 60}"
        redis_url = get_settings().redis_url
    except Exception:
        return
    _start_metric_io(
        lambda: _write_runtime_event(event, key, redis_url), _delivery_slots
    )


def _read_runtime_events(minute: int, redis_url: str) -> dict[str, int]:
    with _client(redis_url).pipeline(transaction=False) as pipe:
        for bucket in range(minute - METRIC_WINDOW_MINUTES + 1, minute + 1):
            pipe.hmget(f"{_PREFIX}{bucket}", sorted(RUNTIME_EVENTS))
        rows = pipe.execute()
    fields = {}
    for index, event in enumerate(sorted(RUNTIME_EVENTS)):
        values = [int(row[index] or 0) for row in rows]
        fields[f"{event}_last_15m"] = min(
            2_000_000_000, sum(max(0, value) for value in values)
        )
    return fields


def collect_runtime_events(*, now: float | None = None) -> dict[str, int | None]:
    unknown = {f"{event}_last_15m": None for event in RUNTIME_EVENTS}
    try:
        minute = int(time.time() if now is None else now) // 60
        redis_url = get_settings().redis_url
    except Exception:
        return unknown
    finished = threading.Event()
    result: list[dict[str, int]] = []

    def collect() -> None:
        try:
            result.append(_read_runtime_events(minute, redis_url))
        finally:
            finished.set()

    if not _start_metric_io(collect, _collection_slots):
        return unknown
    if not finished.wait(_COLLECTION_TIMEOUT_SECONDS):
        return unknown
    return result[0] if result else unknown
