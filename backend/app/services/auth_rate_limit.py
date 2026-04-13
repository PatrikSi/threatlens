from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

import redis

from app.core.config import get_settings

settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginThrottleState:
    blocked: bool
    retry_after_seconds: int | None = None
    backend_available: bool = True


_EMERGENCY_MAX_ENTRIES = 10_000
_emergency_lock = threading.Lock()
_emergency_failures: dict[str, tuple[int, float]] = {}
_emergency_locks: dict[str, float] = {}
_pending_redis_clears: set[tuple[str, ...]] = set()


def check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip)
    try:
        _flush_pending_redis_clears()
        ttl_values = [redis_client.ttl(key) for key in keys]
    except redis.RedisError as exc:
        logger.warning(
            "login_throttle_check_unavailable email=%s ip=%s error=%s",
            _normalize_email(email),
            _normalize_ip(ip),
            exc,
        )
        return _emergency_check_login_throttle(email, ip)

    retry_after = max((ttl for ttl in ttl_values if isinstance(ttl, int) and ttl > 0), default=0)
    if retry_after > 0:
        return LoginThrottleState(blocked=True, retry_after_seconds=retry_after)
    return LoginThrottleState(blocked=False)


def record_login_failure(email: str, ip: str) -> None:
    failure_keys = _failure_keys(email, ip)
    lock_keys = _lock_keys(email, ip)

    try:
        _flush_pending_redis_clears()
        counts: list[int] = []
        for key in failure_keys:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, settings.auth_login_window_seconds)
            counts.append(count)

        if any(count >= settings.auth_login_max_attempts for count in counts):
            for key in lock_keys:
                redis_client.set(key, "1", ex=settings.auth_login_lockout_seconds, nx=True)
    except redis.RedisError as exc:
        logger.warning("login_failure_not_recorded email=%s ip=%s error=%s", _normalize_email(email), _normalize_ip(ip), exc)
        _emergency_record_login_failure(email, ip)


def clear_login_failures(email: str, ip: str) -> None:
    keys = [*_failure_keys(email, ip), *_lock_keys(email, ip)]
    try:
        _flush_pending_redis_clears()
        redis_client.delete(*keys)
        _forget_pending_redis_clear(keys)
    except redis.RedisError as exc:
        logger.warning("login_failures_not_cleared email=%s ip=%s error=%s", _normalize_email(email), _normalize_ip(ip), exc)
        _remember_pending_redis_clear(keys)
    finally:
        _emergency_clear_login_failures(email, ip)


def _emergency_check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip)
    now = time.monotonic()
    retry_after_seconds = 0

    with _emergency_lock:
        _emergency_cleanup(now)
        for key in keys:
            lock_until = _emergency_locks.get(key)
            if lock_until is None:
                continue
            if lock_until <= now:
                _emergency_locks.pop(key, None)
                continue
            retry_after_seconds = max(retry_after_seconds, int(math.ceil(lock_until - now)))

    if retry_after_seconds > 0:
        return LoginThrottleState(blocked=True, retry_after_seconds=retry_after_seconds, backend_available=False)
    return LoginThrottleState(blocked=False, backend_available=False)


def _emergency_record_login_failure(email: str, ip: str) -> None:
    failure_keys = _failure_keys(email, ip)
    lock_keys = _lock_keys(email, ip)
    now = time.monotonic()
    window_seconds = max(1, int(settings.auth_login_window_seconds))
    lockout_seconds = max(1, int(settings.auth_login_lockout_seconds))
    max_attempts = max(1, int(settings.auth_login_max_attempts))

    with _emergency_lock:
        _emergency_cleanup(now)
        counts: list[int] = []
        for key in failure_keys:
            count, started_at = _emergency_failures.get(key, (0, now))
            if now - started_at >= window_seconds:
                count = 0
                started_at = now
            count += 1
            _emergency_failures[key] = (count, started_at)
            counts.append(count)

        if any(count >= max_attempts for count in counts):
            lock_until = now + lockout_seconds
            for key in lock_keys:
                existing_lock_until = _emergency_locks.get(key, 0.0)
                _emergency_locks[key] = max(existing_lock_until, lock_until)
        _emergency_trim_to_limit(_emergency_failures)
        _emergency_trim_to_limit(_emergency_locks)


def _emergency_clear_login_failures(email: str, ip: str) -> None:
    keys = [*_failure_keys(email, ip), *_lock_keys(email, ip)]
    with _emergency_lock:
        for key in keys:
            _emergency_failures.pop(key, None)
            _emergency_locks.pop(key, None)


def _emergency_cleanup(now: float) -> None:
    window_seconds = max(1, int(settings.auth_login_window_seconds))
    stale_failure_keys = [key for key, (_count, started_at) in _emergency_failures.items() if now - started_at >= window_seconds]
    for key in stale_failure_keys:
        _emergency_failures.pop(key, None)

    stale_lock_keys = [key for key, lock_until in _emergency_locks.items() if lock_until <= now]
    for key in stale_lock_keys:
        _emergency_locks.pop(key, None)


def _emergency_trim_to_limit(store: dict[str, object]) -> None:
    overflow = len(store) - _EMERGENCY_MAX_ENTRIES
    if overflow <= 0:
        return
    for key in list(store.keys())[:overflow]:
        store.pop(key, None)


def _flush_pending_redis_clears() -> None:
    with _emergency_lock:
        pending = list(_pending_redis_clears)

    if not pending:
        return

    cleared: list[tuple[str, ...]] = []
    for keys in pending:
        redis_client.delete(*keys)
        cleared.append(keys)

    with _emergency_lock:
        for keys in cleared:
            _pending_redis_clears.discard(keys)


def _remember_pending_redis_clear(keys: list[str]) -> None:
    with _emergency_lock:
        _pending_redis_clears.add(tuple(keys))
        overflow = len(_pending_redis_clears) - _EMERGENCY_MAX_ENTRIES
        if overflow <= 0:
            return
        for pending_keys in list(_pending_redis_clears)[:overflow]:
            _pending_redis_clears.discard(pending_keys)


def _forget_pending_redis_clear(keys: list[str]) -> None:
    with _emergency_lock:
        _pending_redis_clears.discard(tuple(keys))


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower() or "unknown"


def _normalize_ip(ip: str) -> str:
    return (ip or "").strip() or "unknown"


def _failure_keys(email: str, ip: str) -> list[str]:
    normalized_email = _normalize_email(email)
    normalized_ip = _normalize_ip(ip)
    return [
        f"threatlens:auth:fail:email:{normalized_email}",
        f"threatlens:auth:fail:ip:{normalized_ip}",
    ]


def _lock_keys(email: str, ip: str) -> list[str]:
    normalized_email = _normalize_email(email)
    normalized_ip = _normalize_ip(ip)
    return [
        f"threatlens:auth:lock:email:{normalized_email}",
        f"threatlens:auth:lock:ip:{normalized_ip}",
    ]
