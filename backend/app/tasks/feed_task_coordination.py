from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import contextmanager

import redis

from app.core.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

DOMAIN_SLOT_TTL_SECONDS = 30
DOMAIN_SLOT_WAIT_INTERVAL_SECONDS = 0.2
TAGGING_REAPPLY_LOCK_KEY = "threatlens:tagging:reapply:lock"
LEASE_HEARTBEAT_SUFFIX = ":heartbeat"


class CoordinationUnavailableError(RuntimeError):
    pass


def _lease_renewal_interval_seconds(ttl_seconds: int) -> float:
    ttl_seconds = max(1, int(ttl_seconds))
    return max(0.5, min(15.0, ttl_seconds / 3.0))


def _lease_heartbeat_key(key: str) -> str:
    return f"{key}{LEASE_HEARTBEAT_SUFFIX}"


def _lease_heartbeat_value(token: str, *, at: float | None = None) -> str:
    heartbeat_at = time.time() if at is None else float(at)
    return f"{token}|{heartbeat_at:.6f}"


def _parse_lease_heartbeat(raw_value: str | None) -> tuple[str, float] | None:
    if not raw_value:
        return None

    token, separator, raw_timestamp = raw_value.partition("|")
    if not separator or not token:
        return None
    try:
        return token, float(raw_timestamp)
    except ValueError:
        return None


def _lease_takeover_stale_after_seconds(ttl_seconds: int) -> float:
    return float(max(1, int(ttl_seconds)))


def _lease_remaining_ttl_ms(key: str) -> int | None:
    get_pttl = getattr(redis_client, "pttl", None)
    if callable(get_pttl):
        return int(get_pttl(key))

    get_ttl = getattr(redis_client, "ttl", None)
    if callable(get_ttl):
        ttl_seconds = get_ttl(key)
        if ttl_seconds is None:
            return None
        ttl_seconds = int(ttl_seconds)
        return ttl_seconds * 1000 if ttl_seconds >= 0 else ttl_seconds

    return None


def _lease_heartbeat_is_stale(raw_value: str | None, *, ttl_seconds: int, now: float | None = None) -> bool:
    parsed = _parse_lease_heartbeat(raw_value)
    if parsed is None:
        return False

    _token, heartbeat_at = parsed
    observed_at = time.time() if now is None else float(now)
    return observed_at - heartbeat_at >= _lease_takeover_stale_after_seconds(ttl_seconds)


def _write_lease_heartbeat(key: str, ttl_seconds: int, token: str, *, at: float | None = None) -> None:
    redis_client.set(_lease_heartbeat_key(key), _lease_heartbeat_value(token, at=at), ex=ttl_seconds)


def _try_take_stale_lease(key: str, ttl_seconds: int, token: str, *, error_message: str) -> bool:
    heartbeat_key = _lease_heartbeat_key(key)

    try:
        observed_token = redis_client.get(key)
        if not observed_token:
            acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
            if acquired:
                try:
                    _write_lease_heartbeat(key, ttl_seconds, token)
                except redis.RedisError:
                    pass
            return acquired

        remaining_ttl_ms = _lease_remaining_ttl_ms(key)
        if remaining_ttl_ms == -2:
            acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
            if acquired:
                try:
                    _write_lease_heartbeat(key, ttl_seconds, token)
                except redis.RedisError:
                    pass
            return acquired

        if remaining_ttl_ms is None or remaining_ttl_ms >= 0:
            return False

        observed_heartbeat = redis_client.get(heartbeat_key)
        if not _lease_heartbeat_is_stale(observed_heartbeat, ttl_seconds=ttl_seconds):
            return False

        new_heartbeat = _lease_heartbeat_value(token)
        replaced = redis_client.eval(
            (
                "local current = redis.call('get', KEYS[1]) "
                "local current_hb = redis.call('get', KEYS[2]) "
                "if current ~= ARGV[1] then return 0 end "
                "if redis.call('pttl', KEYS[1]) ~= -1 then return 0 end "
                "if ARGV[2] == '__missing__' then "
                "  if current_hb then return 0 end "
                "else "
                "  if current_hb ~= ARGV[2] then return 0 end "
                "end "
                "redis.call('set', KEYS[1], ARGV[3], 'EX', ARGV[5]) "
                "redis.call('set', KEYS[2], ARGV[4], 'EX', ARGV[5]) "
                "return 1"
            ),
            2,
            key,
            heartbeat_key,
            observed_token,
            observed_heartbeat if observed_heartbeat is not None else "__missing__",
            token,
            new_heartbeat,
            ttl_seconds,
        )
        return bool(replaced)
    except redis.RedisError as exc:
        raise CoordinationUnavailableError(error_message) from exc


@contextmanager
def _redis_lease_heartbeat(key: str, ttl_seconds: int, token: str | None = None):
    stop_event = threading.Event()
    renew_interval_seconds = _lease_renewal_interval_seconds(ttl_seconds)

    def _renew() -> None:
        while not stop_event.wait(renew_interval_seconds):
            try:
                if token is not None:
                    current_token = None
                    get_redis_value = getattr(redis_client, "get", None)
                    if callable(get_redis_value):
                        current_token = get_redis_value(key)
                    if current_token != token:
                        return
                redis_client.expire(key, ttl_seconds)
                if token is not None:
                    _write_lease_heartbeat(key, ttl_seconds, token)
            except redis.RedisError:
                continue

    _renewal_thread = threading.Thread(
        target=_renew,
        name=f"threatlens-lease-renewal:{key}",
        daemon=True,
    )
    _renewal_thread.start()
    try:
        if token is not None:
            try:
                _write_lease_heartbeat(key, ttl_seconds, token)
            except redis.RedisError:
                pass
        yield
    finally:
        stop_event.set()
        _renewal_thread.join(timeout=0.1)


def _domain_slot_key(domain: str, slot_number: int) -> str:
    return f"threatlens:domain:{domain}:slot:{slot_number}"


def _best_effort_release_lease(key: str, token: str) -> None:
    try:
        redis_client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
        redis_client.delete(_lease_heartbeat_key(key))
    except redis.RedisError:
        pass


@contextmanager
def domain_slot(domain: str, max_wait_seconds: int = 30):
    if not domain:
        yield
        return

    concurrency_limit = max(1, int(getattr(settings, "per_domain_concurrency", 1) or 1))
    deadline = time.monotonic() + max_wait_seconds
    token = secrets.token_hex(16)
    acquired_key: str | None = None

    while time.monotonic() < deadline and acquired_key is None:
        for slot_number in range(1, concurrency_limit + 1):
            key = _domain_slot_key(domain, slot_number)
            try:
                acquired = bool(redis_client.set(key, token, nx=True, ex=DOMAIN_SLOT_TTL_SECONDS))
                if not acquired:
                    acquired = _try_take_stale_lease(
                        key,
                        DOMAIN_SLOT_TTL_SECONDS,
                        token,
                        error_message="domain slot unavailable",
                    )
            except redis.RedisError as exc:
                raise CoordinationUnavailableError("domain slot unavailable") from exc

            if acquired:
                acquired_key = key
                break

        if acquired_key is None:
            time.sleep(DOMAIN_SLOT_WAIT_INTERVAL_SECONDS)

    if acquired_key is None:
        raise TimeoutError(f"domain slot timeout for {domain}")

    try:
        with _redis_lease_heartbeat(acquired_key, DOMAIN_SLOT_TTL_SECONDS, token):
            yield
    finally:
        _best_effort_release_lease(acquired_key, token)


@contextmanager
def feed_lock(feed_id: str, ttl_seconds: int = 900):
    key = f"threatlens:feed:lock:{feed_id}"
    token = secrets.token_hex(16)

    acquired = False
    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
        if not acquired:
            acquired = _try_take_stale_lease(key, ttl_seconds, token, error_message="feed lock unavailable")
    except redis.RedisError as exc:
        raise CoordinationUnavailableError("feed lock unavailable") from exc

    if not acquired:
        yield False
        return

    try:
        with _redis_lease_heartbeat(key, ttl_seconds, token):
            yield True
    finally:
        try:
            redis_client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                token,
            )
            redis_client.delete(_lease_heartbeat_key(key))
        except redis.RedisError:
            pass


@contextmanager
def daily_ai_brief_lock(ttl_seconds: int = 900):
    key = "threatlens:ai:daily_brief:lock"
    token = secrets.token_hex(16)

    acquired = False
    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
        if not acquired:
            acquired = _try_take_stale_lease(
                key,
                ttl_seconds,
                token,
                error_message="daily brief lock unavailable",
            )
    except redis.RedisError as exc:
        raise CoordinationUnavailableError("daily brief lock unavailable") from exc

    if not acquired:
        yield False
        return

    try:
        with _redis_lease_heartbeat(key, ttl_seconds, token):
            yield True
    finally:
        try:
            redis_client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                token,
            )
            redis_client.delete(_lease_heartbeat_key(key))
        except redis.RedisError:
            pass


def claim_tagging_reapply_dispatch(ttl_seconds: int = 900) -> str | None:
    key = TAGGING_REAPPLY_LOCK_KEY
    token = secrets.token_hex(16)

    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
        if not acquired:
            acquired = _try_take_stale_lease(
                key,
                ttl_seconds,
                token,
                error_message="tagging reapply lock unavailable",
            )
    except redis.RedisError as exc:
        raise CoordinationUnavailableError("tagging reapply lock unavailable") from exc
    return token if acquired else None


def release_tagging_reapply_dispatch(token: str) -> None:
    try:
        redis_client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            TAGGING_REAPPLY_LOCK_KEY,
            token,
        )
        redis_client.delete(_lease_heartbeat_key(TAGGING_REAPPLY_LOCK_KEY))
    except redis.RedisError:
        return


@contextmanager
def tagging_reapply_lock(ttl_seconds: int = 900, token: str | None = None):
    key = TAGGING_REAPPLY_LOCK_KEY
    resolved_token = token or secrets.token_hex(16)

    acquired = False
    try:
        if token and redis_client.get(key) == token:
            acquired = True
        else:
            acquired = bool(redis_client.set(key, resolved_token, nx=True, ex=ttl_seconds))
        if not acquired:
            acquired = _try_take_stale_lease(
                key,
                ttl_seconds,
                resolved_token,
                error_message="tagging reapply lock unavailable",
            )
    except redis.RedisError as exc:
        raise CoordinationUnavailableError("tagging reapply lock unavailable") from exc

    if not acquired:
        yield False
        return

    try:
        with _redis_lease_heartbeat(key, ttl_seconds, resolved_token):
            yield True
    finally:
        try:
            redis_client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                resolved_token,
            )
            redis_client.delete(_lease_heartbeat_key(key))
        except redis.RedisError:
            pass
