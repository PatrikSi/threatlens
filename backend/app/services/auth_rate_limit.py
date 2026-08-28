from __future__ import annotations

import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass

import redis

from app.core.config import get_settings
from app.core.redis_client import redis_client_from_url

settings = get_settings()
redis_client = redis_client_from_url(
    settings.redis_url, decode_responses=True, settings=settings
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginThrottleState:
    blocked: bool
    retry_after_seconds: int | None = None
    backend_available: bool = True
    failure_version: str | None = None


_EMERGENCY_MAX_ENTRIES = 10_000
_ACCOUNT_SPRAY_IP_THRESHOLD = 3
_LOGIN_THROTTLE_NAMESPACE = "login"
_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE = "password_verify"
_MFA_ACTION_THROTTLE_NAMESPACE = "mfa_action"
_SELF_REGISTRATION_THROTTLE_NAMESPACE = "self_register"
_OIDC_CALLBACK_THROTTLE_NAMESPACE = "oidc_callback"
_emergency_lock = threading.Lock()
_emergency_failures: dict[str, tuple[int, float]] = {}
_emergency_locks: dict[str, float] = {}
_emergency_account_ip_sets: dict[str, tuple[set[str], float]] = {}
_emergency_versions: dict[str, tuple[str, float]] = {}

_RECORD_FAILURE_LUA = """
-- threatlens-auth-record-v1
local email_ip_count = redis.call('INCR', KEYS[1])
if email_ip_count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
local ip_count = redis.call('INCR', KEYS[2])
if ip_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[2]) end
local account_count = redis.call('INCR', KEYS[3])
if account_count == 1 then redis.call('EXPIRE', KEYS[3], ARGV[2]) end
redis.call('SADD', KEYS[4], ARGV[1])
if redis.call('TTL', KEYS[4]) <= 0 then redis.call('EXPIRE', KEYS[4], ARGV[2]) end
local distinct_ips = redis.call('SCARD', KEYS[4])
redis.call('SET', KEYS[8], ARGV[8], 'EX', ARGV[2])
if email_ip_count >= tonumber(ARGV[3]) then redis.call('SET', KEYS[5], '1', 'EX', ARGV[6], 'NX') end
if ip_count >= tonumber(ARGV[4]) then redis.call('SET', KEYS[6], '1', 'EX', ARGV[6], 'NX') end
if account_count >= tonumber(ARGV[5]) and distinct_ips >= tonumber(ARGV[7]) then
  redis.call('SET', KEYS[7], '1', 'EX', ARGV[6], 'NX')
end
return {email_ip_count, ip_count, account_count, distinct_ips}
"""

_CLEAR_FAILURES_LUA = """
-- threatlens-auth-clear-v1
local current = redis.call('GET', KEYS[6]) or ''
if ARGV[1] ~= '' and current ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
return 1
"""


def check_login_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def check_password_verification_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(
        email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE
    )


def check_mfa_action_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(email, ip, namespace=_MFA_ACTION_THROTTLE_NAMESPACE)


def check_self_registration_throttle(email: str, ip: str) -> LoginThrottleState:
    return _check_throttle(email, ip, namespace=_SELF_REGISTRATION_THROTTLE_NAMESPACE)


def check_oidc_callback_throttle(ip: str) -> LoginThrottleState:
    return _check_throttle(
        f"callback:{_normalize_ip(ip)}",
        ip,
        namespace=_OIDC_CALLBACK_THROTTLE_NAMESPACE,
    )


def _check_throttle(email: str, ip: str, *, namespace: str) -> LoginThrottleState:
    keys = _lock_keys(email, ip, namespace=namespace)
    try:
        raw_version = redis_client.get(_account_version_key(email, namespace=namespace))
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

    retry_after = max(
        (ttl for ttl in ttl_values if isinstance(ttl, int) and ttl > 0), default=0
    )
    if retry_after > 0:
        return LoginThrottleState(
            blocked=True,
            retry_after_seconds=retry_after,
            failure_version=str(raw_version) if raw_version else None,
        )
    return LoginThrottleState(
        blocked=False,
        failure_version=str(raw_version) if raw_version else None,
    )


def record_login_failure(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def record_password_verification_failure(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE)


def record_mfa_action_failure(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_MFA_ACTION_THROTTLE_NAMESPACE)


def record_self_registration_attempt(email: str, ip: str) -> None:
    _record_failure(email, ip, namespace=_SELF_REGISTRATION_THROTTLE_NAMESPACE)


def record_invalid_oidc_callback(ip: str) -> None:
    _record_failure(
        f"callback:{_normalize_ip(ip)}",
        ip,
        namespace=_OIDC_CALLBACK_THROTTLE_NAMESPACE,
    )


def _record_failure(email: str, ip: str, *, namespace: str) -> None:
    email_ip_failure_key = _email_ip_failure_key(email, ip, namespace=namespace)
    ip_failure_key = _ip_failure_key(ip, namespace=namespace)
    account_failure_key = _account_failure_key(email, namespace=namespace)

    try:
        redis_client.eval(
            _RECORD_FAILURE_LUA,
            8,
            email_ip_failure_key,
            ip_failure_key,
            account_failure_key,
            _account_ip_set_key(email, namespace=namespace),
            _email_ip_lock_key(email, ip, namespace=namespace),
            _ip_lock_key(ip, namespace=namespace),
            _account_lock_key(email, namespace=namespace),
            _account_version_key(email, namespace=namespace),
            _normalize_ip(ip),
            _failure_window_seconds(),
            _max_attempts(),
            _ip_max_attempts(),
            _account_failure_threshold(),
            _lockout_seconds(),
            _ACCOUNT_SPRAY_IP_THRESHOLD,
            secrets.token_hex(16),
        )
    except redis.RedisError as exc:
        logger.warning(
            "auth_failure_not_recorded namespace=%s email=%s ip=%s error=%s",
            namespace,
            _normalize_email(email),
            _normalize_ip(ip),
            exc,
        )
        _emergency_record_failure(email, ip, namespace=namespace)


def clear_login_failures(
    email: str, ip: str, *, observed_failure_version: str | None = None
) -> None:
    _clear_failures(
        email,
        ip,
        namespace=_LOGIN_THROTTLE_NAMESPACE,
        observed_failure_version=observed_failure_version,
    )


def clear_password_verification_failures(
    email: str, ip: str, *, observed_failure_version: str | None = None
) -> None:
    _clear_failures(
        email,
        ip,
        namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE,
        observed_failure_version=observed_failure_version,
    )


def clear_mfa_action_failures(
    email: str, ip: str, *, observed_failure_version: str | None = None
) -> None:
    _clear_failures(
        email,
        ip,
        namespace=_MFA_ACTION_THROTTLE_NAMESPACE,
        observed_failure_version=observed_failure_version,
    )


def _clear_failures(
    email: str,
    ip: str,
    *,
    namespace: str,
    observed_failure_version: str | None,
) -> None:
    keys = [
        _email_ip_failure_key(email, ip, namespace=namespace),
        _account_failure_key(email, namespace=namespace),
        _email_ip_lock_key(email, ip, namespace=namespace),
        _account_lock_key(email, namespace=namespace),
        _account_ip_set_key(email, namespace=namespace),
        _account_version_key(email, namespace=namespace),
    ]
    try:
        expected_version = observed_failure_version
        if expected_version is None:
            expected_version = str(redis_client.get(keys[-1]) or "")
        redis_client.eval(_CLEAR_FAILURES_LUA, len(keys), *keys, expected_version)
    except redis.RedisError as exc:
        logger.warning(
            "auth_failures_not_cleared namespace=%s email=%s ip=%s error=%s",
            namespace,
            _normalize_email(email),
            _normalize_ip(ip),
            exc,
        )
    finally:
        _emergency_clear_failures(
            email,
            ip,
            namespace=namespace,
            observed_failure_version=observed_failure_version,
        )


def _emergency_check_throttle(
    email: str, ip: str, *, namespace: str
) -> LoginThrottleState:
    keys = _lock_keys(email, ip, namespace=namespace)
    now = time.monotonic()
    retry_after_seconds = 0

    with _emergency_lock:
        _emergency_cleanup(now)
        version, _started_at = _emergency_versions.get(
            _account_version_key(email, namespace=namespace), ("", now)
        )
        for key in keys:
            lock_until = _emergency_locks.get(key)
            if lock_until is None:
                continue
            if lock_until <= now:
                _emergency_locks.pop(key, None)
                continue
            retry_after_seconds = max(
                retry_after_seconds, int(math.ceil(lock_until - now))
            )
    if retry_after_seconds > 0:
        return LoginThrottleState(
            blocked=True,
            retry_after_seconds=retry_after_seconds,
            backend_available=False,
            failure_version=version,
        )
    return LoginThrottleState(
        blocked=False, backend_available=False, failure_version=version
    )


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

        distinct_ip_count = _emergency_record_account_ip_failure(
            email, ip, now, namespace=namespace
        )
        version_key = _account_version_key(email, namespace=namespace)
        _emergency_versions[version_key] = (secrets.token_hex(16), now)

        if counts[email_ip_failure_key] >= max_attempts:
            lock_until = now + lockout_seconds
            existing_lock_until = _emergency_locks.get(
                _email_ip_lock_key(email, ip, namespace=namespace), 0.0
            )
            _emergency_locks[_email_ip_lock_key(email, ip, namespace=namespace)] = max(
                existing_lock_until, lock_until
            )
        if counts[ip_failure_key] >= ip_max_attempts:
            lock_until = now + lockout_seconds
            existing_lock_until = _emergency_locks.get(
                _ip_lock_key(ip, namespace=namespace), 0.0
            )
            _emergency_locks[_ip_lock_key(ip, namespace=namespace)] = max(
                existing_lock_until, lock_until
            )
        if _should_lock_account(counts[account_failure_key], distinct_ip_count):
            lock_until = now + lockout_seconds
            existing_lock_until = _emergency_locks.get(
                _account_lock_key(email, namespace=namespace), 0.0
            )
            _emergency_locks[_account_lock_key(email, namespace=namespace)] = max(
                existing_lock_until, lock_until
            )
        _emergency_trim_to_limit(_emergency_failures)
        _emergency_trim_to_limit(_emergency_locks)
        _emergency_trim_to_limit(_emergency_account_ip_sets)
        _emergency_trim_to_limit(_emergency_versions)


def _emergency_clear_login_failures(email: str, ip: str) -> None:
    _emergency_clear_failures(email, ip, namespace=_LOGIN_THROTTLE_NAMESPACE)


def _emergency_clear_password_verification_failures(email: str, ip: str) -> None:
    _emergency_clear_failures(
        email, ip, namespace=_PASSWORD_VERIFICATION_THROTTLE_NAMESPACE
    )


def _emergency_clear_mfa_action_failures(email: str, ip: str) -> None:
    _emergency_clear_failures(email, ip, namespace=_MFA_ACTION_THROTTLE_NAMESPACE)


def _emergency_clear_self_registration_attempts(email: str, ip: str) -> None:
    _emergency_clear_failures(
        email, ip, namespace=_SELF_REGISTRATION_THROTTLE_NAMESPACE
    )


def _emergency_clear_failures(
    email: str,
    ip: str,
    *,
    namespace: str,
    observed_failure_version: str | None = None,
) -> None:
    keys = [
        _email_ip_failure_key(email, ip, namespace=namespace),
        _account_failure_key(email, namespace=namespace),
        _email_ip_lock_key(email, ip, namespace=namespace),
        _account_lock_key(email, namespace=namespace),
        _account_ip_set_key(email, namespace=namespace),
    ]
    with _emergency_lock:
        current_version, _started_at = _emergency_versions.get(
            _account_version_key(email, namespace=namespace), ("", time.monotonic())
        )
        if (
            observed_failure_version is not None
            and current_version != observed_failure_version
        ):
            return
        for key in keys:
            _emergency_failures.pop(key, None)
            _emergency_locks.pop(key, None)
            _emergency_account_ip_sets.pop(key, None)


def _emergency_cleanup(now: float) -> None:
    window_seconds = _failure_window_seconds()
    stale_failure_keys = [
        key
        for key, (_count, started_at) in _emergency_failures.items()
        if now - started_at >= window_seconds
    ]
    for key in stale_failure_keys:
        _emergency_failures.pop(key, None)

    stale_account_ip_keys = [
        key
        for key, (_ips, started_at) in _emergency_account_ip_sets.items()
        if now - started_at >= window_seconds
    ]
    for key in stale_account_ip_keys:
        _emergency_account_ip_sets.pop(key, None)

    stale_version_keys = [
        key
        for key, (_version, started_at) in _emergency_versions.items()
        if now - started_at >= window_seconds
    ]
    for key in stale_version_keys:
        _emergency_versions.pop(key, None)

    stale_lock_keys = [
        key for key, lock_until in _emergency_locks.items() if lock_until <= now
    ]
    for key in stale_lock_keys:
        _emergency_locks.pop(key, None)


def _emergency_trim_to_limit(store: dict[str, object]) -> None:
    overflow = len(store) - _EMERGENCY_MAX_ENTRIES
    if overflow <= 0:
        return
    for key in list(store.keys())[:overflow]:
        store.pop(key, None)


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
    return (
        account_failures >= _account_failure_threshold()
        and distinct_ip_count >= _ACCOUNT_SPRAY_IP_THRESHOLD
    )


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


def _emergency_record_account_ip_failure(
    email: str, ip: str, now: float, *, namespace: str
) -> int:
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


def _account_version_key(email: str, *, namespace: str) -> str:
    return f"threatlens:auth:{namespace}:version:email:{_normalize_email(email)}"
