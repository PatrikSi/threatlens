from __future__ import annotations

from dataclasses import dataclass

import redis

from app.core.config import get_settings

settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


@dataclass(frozen=True)
class LoginThrottleState:
    blocked: bool
    retry_after_seconds: int | None = None


def check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip)
    try:
        ttl_values = [redis_client.ttl(key) for key in keys]
    except redis.RedisError:
        return LoginThrottleState(blocked=False)

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
        return


def clear_login_failures(email: str, ip: str) -> None:
    keys = [*_failure_keys(email, ip), *_lock_keys(email, ip)]
    try:
        redis_client.delete(*keys)
    except redis.RedisError:
        return


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
