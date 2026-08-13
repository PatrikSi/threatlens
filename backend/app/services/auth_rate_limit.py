from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

import redis

from app.core.config import get_settings
from app.core.redis_client import redis_client_from_url

settings = get_settings()
redis_client = redis_client_from_url(settings.redis_url, decode_responses=True, settings=settings)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginThrottleState:
    blocked: bool
    retry_after_seconds: int | None = None
    backend_available: bool = True


_EMERGENCY_MAX_ENTRIES = 10_000
_ACCOUNT_SPRAY_IP_THRESHOLD = 3
_LOGIN_THROTTLE_NAMESPACE = "login"
_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE = "password_verify"
_SELF_REGISTRATION_THROTTLE_NAMESPACE = "self_register"
_emergency_lock = threading.Lock()
_emergency_failures: dict[str, tuple[int, float]] = {}
_emergency_locks: dict[str, float] = {}
_emergency_account_ip_sets: dict[str, tuple[set[str], float]] = {}
_pending_redis_clears: set[tuple[str, ...]] = set()


def check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def check_password_verification_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE)


def check_self_registration_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(email, ip, namespace=_SELF_REGISTRATION_THROTTLE_NAMESPACE)


def _check_throttle(email: str, ip: str, *, namespace: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip, namespace=namespace)
    try:
        _flush_pending_redis_clears()
        ttl_values = [redis_client.ttl(key) for key in keys]
    except redis.RedisError as exc:
        logger.warning(
            "auth_throttle_check_unavailable namespace=%s email=%s ip=%s error=%s",
            namespace,
            _normalize_email(email),
            _normalize_ip(ip),
            exc,
        )
        return _emergency_check_throttle(email, ip, namespace=namespace)

    retry_after = max((ttl for ttl in ttl_values if isinstance(ttl, int) and ttl > 0), default=0)
    if retry_after > 0:
        return LoginThrottleState(blocked=True, retry_after_seconds=retry_after)
    return LoginThrottleState(blocked=False)


def record_login_failure(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def record_password_verification_failure(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE)


def record_self_registration_attempt(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_SELF_REGISTRATION_THROTTLE_NAMESPACE)


def _record_failure(email: str, ip: str, *, namespace: str) -> None:
    failure_keys = _failure_keys(email, ip, namespace=namespace)
    email_ip_failure_key = _email_ip_failure_key(email, ip, namespace=namespace)
    ip_failure_key = _ip_failure_key(ip, namespace=namespace)
    account_failure_key = _account_failure_key(email, namespace=namespace)

    try:
        _flush_pending_redis_clears()
        counts: dict[str, int] = {}
        for key in failure_keys:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, _failure_window_seconds())
            counts[key] = count

        distinct_ip_count = _record_account_ip_failure(email, ip, namespace=namespace)
        if counts[email_ip_failure_key] >= _max_attempts():
            redis_client.set(_email_ip_lock_key(email, ip, namespace=namespace), "1", ex=_lockout_seconds(), nx=True)
        if counts[ip_failure_key] >= _ip_max_attempts():
            redis_client.set(_ip_lock_key(ip, namespace=namespace), "1", ex=_lockout_seconds(), nx=True)
        if _should_lock_account(counts[account_failure_key], distinct_ip_count):
            redis_client.set(_account_lock_key(email, namespace=namespace), "1", ex=_lockout_seconds(), nx=True)
    except redis.RedisError as exc:
        logger.warning(
            "auth_failure_not_recorded namespace=%s email=%s ip=%s error=%s",
            namespace,
            _normalize_email(email),
            _normalize_ip(ip),
            exc,
        )
        _emergency_record_failure(email, ip, namespace=namespace)


def clear_login_failures(email: str, ip: str) -> None:
    _clear_failures(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def clear_password_verification_failures(email: str, ip: str) -> None:
    _clear_failures(email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE)


def _clear_failures(email: str, ip: str, *, namespace: str) -> None:
    keys = [
        *_failure_keys(email, ip, namespace=namespace),
        *_lock_keys(email, ip, namespace=namespace),
        _account_ip_set_key(email, namespace=namespace),
    ]
    try:
        _flush_pending_redis_clears()
        redis_client.delete(*keys)
        _forget_pending_redis_clear(keys)
    except redis.RedisError as exc:
        logger.warning(
            "auth_failures_not_cleared namespace=%s email=%s ip=%s error=%s",
            namespace,
            _normalize_email(email),
            _normalize_ip(ip),
            exc,
        )
        _remember_pending_redis_clear(keys)
    finally:
        _emergency_clear_failures(email, ip, namespace=namespace)


def _emergency_check_throttle(email: str, ip: str, *, namespace: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip, namespace=namespace)
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


def _emergency_record_failure(email: str, ip: str, *, namespace: str) -> None:
    failure_keys = _failure_keys(email, ip, namespace=namespace)
    now = time.monotonic()
    window_seconds = _failure_window_seconds()
    lockout_seconds = _lockout_seconds()
    max_attempts = _max_attempts()
    ip_max_attempts = _ip_max_attempts()
    email_ip_failure_key = _email_ip_failure_key(email, ip, namespace=namespace)
    ip_failure_key = _ip_failure_key(ip, namespace=namespace)
    account_failure_key = _account_failure_key(email, namespace=namespace)

    with _emergency_lock:
        _emergency_cleanup(now)
        counts: dict[str, int] = {}
        for key in failure_keys:
            count, started_at = _emergency_failures.get(key, (0, now))
            if now - started_at >= window_seconds:
                count = 0
                started_at = now
            count += 1
            _emergency_failures[key] = (count, started_at)
            counts[key] = count

        distinct_ip_count = _emergency_record_account_ip_failure(email, ip, now, namespace=namespace)

        if counts[email_ip_failure_key] >= max_attempts:
            lock_until = now + lockout_seconds
            existing_lock_until = _emergency_locks.get(_email_ip_lock_key(email, ip, namespace=namespace), 0.0)
            _emergency_locks[_email_ip_lock_key(email, ip, namespace=namespace)] = max(existing_lock_until, lock_until)
        if counts[ip_failure_key] >= ip_max_attempts:
            lock_until = now + lockout_seconds
            existing_lock_until = _emergency_locks.get(_ip_lock_key(ip, namespace=namespace), 0.0)
            _emergency_locks[_ip_lock_key(ip, namespace=namespace)] = max(existing_lock_until, lock_until)
        if _should_lock_account(counts[account_failure_key], distinct_ip_count):
            lock_until = now + lockout_seconds
            existing_lock_until = _emergency_locks.get(_account_lock_key(email, namespace=namespace), 0.0)
            _emergency_locks[_account_lock_key(email, namespace=namespace)] = max(existing_lock_until, lock_until)
        _emergency_trim_to_limit(_emergency_failures)
        _emergency_trim_to_limit(_emergency_locks)
        _emergency_trim_to_limit(_emergency_account_ip_sets)


def _emergency_clear_login_failures(email: str, ip: str) -> None:
    _emergency_clear_failures(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def _emergency_clear_password_verification_failures(email: str, ip: str) -> None:
    _emergency_clear_failures(email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE)


def _emergency_clear_self_registration_attempts(email: str, ip: str) -> None:
    _emergency_clear_failures(email, ip, namespace=_SELF_REGISTRATION_THROTTLE_NAMESPACE)


def _emergency_clear_failures(email: str, ip: str, *, namespace: str) -> None:
    keys = [
        *_failure_keys(email, ip, namespace=namespace),
        *_lock_keys(email, ip, namespace=namespace),
        _account_ip_set_key(email, namespace=namespace),
    ]
    with _emergency_lock:
        for key in keys:
            _emergency_failures.pop(key, None)
            _emergency_locks.pop(key, None)
            _emergency_account_ip_sets.pop(key, None)


def _emergency_cleanup(now: float) -> None:
    window_seconds = _failure_window_seconds()
    stale_failure_keys = [key for key, (_count, started_at) in _emergency_failures.items() if now - started_at >= window_seconds]
    for key in stale_failure_keys:
        _emergency_failures.pop(key, None)

    stale_account_ip_keys = [
        key for key, (_ips, started_at) in _emergency_account_ip_sets.items() if now - started_at >= window_seconds
    ]
    for key in stale_account_ip_keys:
        _emergency_account_ip_sets.pop(key, None)

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


def _failure_keys(email: str, ip: str, *, namespace: str) -> list[str]:
    return [
        _email_ip_failure_key(email, ip, namespace=namespace),
        _ip_failure_key(ip, namespace=namespace),
        _account_failure_key(email, namespace=namespace),
    ]


def _lock_keys(email: str, ip: str, *, namespace: str) -> list[str]:
    return [
        _email_ip_lock_key(email, ip, namespace=namespace),
        _ip_lock_key(ip, namespace=namespace),
        _account_lock_key(email, namespace=namespace),
    ]


def _normalized_email_ip_bucket(email: str, ip: str) -> str:
    return f"{_normalize_email(email)}:{_normalize_ip(ip)}"


def _max_attempts() -> int:
    return max(1, int(settings.auth_login_max_attempts))


def _ip_max_attempts() -> int:
    return max(_max_attempts(), int(settings.auth_login_ip_max_attempts))


def _failure_window_seconds() -> int:
    return max(1, int(settings.auth_login_window_seconds))


def _lockout_seconds() -> int:
    return max(1, int(settings.auth_login_lockout_seconds))


def _account_failure_threshold() -> int:
    return _max_attempts() * _ACCOUNT_SPRAY_IP_THRESHOLD


def _should_lock_account(account_failures: int, distinct_ip_count: int) -> bool:
    return account_failures >= _account_failure_threshold() and distinct_ip_count >= _ACCOUNT_SPRAY_IP_THRESHOLD


def _record_account_ip_failure(email: str, ip: str, *, namespace: str) -> int:
    sadd = getattr(redis_client, "sadd", None)
    scard = getattr(redis_client, "scard", None)
    if not callable(sadd) or not callable(scard):
        return 1

    key = _account_ip_set_key(email, namespace=namespace)
    sadd(key, _normalize_ip(ip))
    if redis_client.ttl(key) <= 0:
        redis_client.expire(key, _failure_window_seconds())
    return int(scard(key))


def _emergency_record_account_ip_failure(email: str, ip: str, now: float, *, namespace: str) -> int:
    key = _account_ip_set_key(email, namespace=namespace)
    current_ips, started_at = _emergency_account_ip_sets.get(key, (set(), now))
    if now - started_at >= _failure_window_seconds():
        current_ips = set()
        started_at = now
    current_ips.add(_normalize_ip(ip))
    _emergency_account_ip_sets[key] = (current_ips, started_at)
    return len(current_ips)


def _email_ip_failure_key(email: str, ip: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:fail:email_ip:{_normalized_email_ip_bucket(email, ip)}"


def _ip_failure_key(ip: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:fail:ip:{_normalize_ip(ip)}"


def _account_failure_key(email: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:fail:email:{_normalize_email(email)}"


def _email_ip_lock_key(email: str, ip: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:lock:email_ip:{_normalized_email_ip_bucket(email, ip)}"


def _ip_lock_key(ip: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:lock:ip:{_normalize_ip(ip)}"


def _account_lock_key(email: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:lock:email:{_normalize_email(email)}"


def _account_ip_set_key(email: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:fail:email_ips:{_normalize_email(email)}"
