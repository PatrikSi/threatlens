from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time

import redis

from app.core.config import get_settings

settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
_fallback_lock = threading.Lock()
_fallback_failures: dict[str, tuple[int, float]] = {}
_fallback_locks: dict[str, float] = {}
_FALLBACK_MAX_ENTRIES = 10_000


@dataclass(frozen=True)
class LoginThrottleState:
    blocked: bool
    retry_after_seconds: int | None = None


def check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip)
    try:
        ttl_values = [redis_client.ttl(key) for key in keys]
    except redis.RedisError:
        return _fallback_check_login_throttle(email, ip)

    retry_after = max((ttl for ttl in ttl_values if isinstance(ttl, int) and ttl > 0), default=0)
    if retry_after > 0:
        return LoginThrottleState(blocked=True, retry_after_seconds=retry_after)
    return LoginThrottleState(blocked=False)


def record_login_failure(email: str, ip: str) -> None:
    failure_keys = _failure_keys(email, ip)
    lock_keys = _lock_keys(email, ip)

    try:
        counts: list[int] = []
        for key in failure_keys:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, settings.auth_login_window_seconds)
            counts.append(count)

        if any(count >= settings.auth_login_max_attempts for count in counts):
            for key in lock_keys:
                redis_client.set(key, "1", ex=settings.auth_login_lockout_seconds, nx=True)
    except redis.RedisError:
        _fallback_record_login_failure(email, ip)
        return


def clear_login_failures(email: str, ip: str) -> None:
    keys = [*_failure_keys(email, ip), *_lock_keys(email, ip)]
    try:
        redis_client.delete(*keys)
    except redis.RedisError:
        pass
    _fallback_clear_login_failures(email, ip)


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


def _fallback_check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip)
    now = time.monotonic()
    retry_after_seconds = 0

    with _fallback_lock:
        _fallback_cleanup(now)
        for key in keys:
            lock_until = _fallback_locks.get(key)
            if lock_until is None:
                continue
            if lock_until <= now:
                _fallback_locks.pop(key, None)
                continue
            retry_after_seconds = max(retry_after_seconds, int(math.ceil(lock_until - now)))

    if retry_after_seconds > 0:
        return LoginThrottleState(blocked=True, retry_after_seconds=retry_after_seconds)
    return LoginThrottleState(blocked=False)


def _fallback_record_login_failure(email: str, ip: str) -> None:
    failure_keys = _failure_keys(email, ip)
    lock_keys = _lock_keys(email, ip)
    now = time.monotonic()
    window_seconds = max(1, int(settings.auth_login_window_seconds))
    lockout_seconds = max(1, int(settings.auth_login_lockout_seconds))
    max_attempts = max(1, int(settings.auth_login_max_attempts))

    with _fallback_lock:
        _fallback_cleanup(now)
        counts: list[int] = []
        for key in failure_keys:
            count, started_at = _fallback_failures.get(key, (0, now))
            if now - started_at >= window_seconds:
                count = 0
                started_at = now
            count += 1
            _fallback_failures[key] = (count, started_at)
            counts.append(count)

        if any(count >= max_attempts for count in counts):
            lock_until = now + lockout_seconds
            for key in lock_keys:
                existing_lock_until = _fallback_locks.get(key, 0.0)
                _fallback_locks[key] = max(existing_lock_until, lock_until)
        _fallback_trim_to_limit(_fallback_failures)
        _fallback_trim_to_limit(_fallback_locks)


def _fallback_clear_login_failures(email: str, ip: str) -> None:
    keys = [*_failure_keys(email, ip), *_lock_keys(email, ip)]
    with _fallback_lock:
        for key in keys:
            _fallback_failures.pop(key, None)
            _fallback_locks.pop(key, None)


def _fallback_cleanup(now: float) -> None:
    window_seconds = max(1, int(settings.auth_login_window_seconds))
    stale_failure_keys = [key for key, (_count, started_at) in _fallback_failures.items() if now - started_at >= window_seconds]
    for key in stale_failure_keys:
        _fallback_failures.pop(key, None)

    stale_lock_keys = [key for key, lock_until in _fallback_locks.items() if lock_until <= now]
    for key in stale_lock_keys:
        _fallback_locks.pop(key, None)


def _fallback_trim_to_limit(store: dict[str, object]) -> None:
    overflow = len(store) - _FALLBACK_MAX_ENTRIES
    if overflow <= 0:
        return
    for key in list(store.keys())[:overflow]:
        store.pop(key, None)
