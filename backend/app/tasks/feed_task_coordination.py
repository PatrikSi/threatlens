from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import redis

from app.core.config import get_settings
from app.core.redis_client import redis_client_from_url


logger = logging.getLogger(__name__)
settings = get_settings()
redis_client = redis_client_from_url(settings.redis_url, decode_responses=True, settings=settings)

DOMAIN_SLOT_TTL_SECONDS = 30
DOMAIN_SLOT_WAIT_INTERVAL_SECONDS = 0.2
TAGGING_REAPPLY_LOCK_KEY = "threatlens:tagging:reapply:lock"
LEASE_HEARTBEAT_SUFFIX = ":heartbeat"
LEASE_OBSERVATION_SUFFIX = ":observation"

_RENEW_LEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end "
    "redis.call('pexpire', KEYS[1], ARGV[3]) "
    "redis.call('set', KEYS[2], ARGV[2], 'PX', ARGV[3]) "
    "return 1"
)
_RELEASE_LEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end "
    "redis.call('del', KEYS[1]) "
    "redis.call('del', KEYS[2]) "
    "return 1"
)


class CoordinationUnavailableError(RuntimeError):
    pass


class LeaseOwnershipLostError(CoordinationUnavailableError):
    pass


class DomainSlotUnavailableError(CoordinationUnavailableError):
    pass


@dataclass
class RedisLeaseGuard:
    key: str
    token: str
    ttl_seconds: int
    _last_successful_renewal: float = field(default_factory=time.monotonic)
    _ownership_lost: threading.Event = field(default_factory=threading.Event)

    def __bool__(self) -> bool:
        return not self._ownership_lost.is_set()

    def ensure_owned(self) -> None:
        if self._ownership_lost.is_set():
            raise LeaseOwnershipLostError("coordination lease ownership was lost")
        try:
            renewed = redis_client.eval(
                _RENEW_LEASE_SCRIPT,
                2,
                self.key,
                _lease_heartbeat_key(self.key),
                self.token,
                _lease_heartbeat_value(self.token),
                max(1, int(self.ttl_seconds)) * 1000,
            )
        except redis.RedisError as exc:
            elapsed = time.monotonic() - self._last_successful_renewal
            fail_closed_after = max(0.5, self.ttl_seconds * 0.8)
            if elapsed >= fail_closed_after:
                logger.warning(
                    "coordination_lease_verification_failed key=%s elapsed_seconds=%.3f error_type=%s",
                    self.key,
                    elapsed,
                    type(exc).__name__,
                )
                raise CoordinationUnavailableError(
                    "coordination lease could not be verified before expiry"
                ) from exc
            logger.debug(
                "coordination_lease_verification_deferred key=%s elapsed_seconds=%.3f error_type=%s",
                self.key,
                elapsed,
                type(exc).__name__,
            )
            return
        if not renewed:
            self._ownership_lost.set()
            logger.warning("coordination_lease_ownership_lost key=%s", self.key)
            raise LeaseOwnershipLostError("coordination lease ownership was lost")
        self._last_successful_renewal = time.monotonic()

    def mark_lost(self) -> None:
        self._ownership_lost.set()


def _lease_renewal_interval_seconds(ttl_seconds: int) -> float:
    ttl_seconds = max(1, int(ttl_seconds))
    return max(0.5, min(15.0, ttl_seconds / 3.0))


def _lease_heartbeat_key(key: str) -> str:
    return f"{key}{LEASE_HEARTBEAT_SUFFIX}"


def _lease_observation_key(key: str) -> str:
    return f"{key}{LEASE_OBSERVATION_SUFFIX}"


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
    observation_key = _lease_observation_key(key)

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
        parsed_heartbeat = _parse_lease_heartbeat(observed_heartbeat)
        heartbeat_is_trusted = (
            parsed_heartbeat is not None and parsed_heartbeat[0] == observed_token
        )
        if heartbeat_is_trusted:
            if not _lease_heartbeat_is_stale(
                observed_heartbeat,
                ttl_seconds=ttl_seconds,
            ):
                return False
        elif not _legacy_lease_observation_is_stale(
            observation_key,
            observed_token,
            ttl_seconds=ttl_seconds,
        ):
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
                "redis.call('del', KEYS[3]) "
                "return 1"
            ),
            3,
            key,
            heartbeat_key,
            observation_key,
            observed_token,
            observed_heartbeat if observed_heartbeat is not None else "__missing__",
            token,
            new_heartbeat,
            ttl_seconds,
        )
        if replaced:
            logger.info("coordination_stale_lease_replaced key=%s", key)
        return bool(replaced)
    except redis.RedisError as exc:
        logger.warning(
            "coordination_lease_operation_failed key=%s error_type=%s",
            key,
            type(exc).__name__,
        )
        raise CoordinationUnavailableError(error_message) from exc


def _legacy_lease_observation_is_stale(
    observation_key: str,
    observed_token: str,
    *,
    ttl_seconds: int,
) -> bool:
    observed_at = time.time()
    raw_observation = redis_client.get(observation_key)
    parsed_observation = _parse_lease_heartbeat(raw_observation)
    if parsed_observation is not None and parsed_observation[0] == observed_token:
        return (
            observed_at - parsed_observation[1]
            >= _lease_takeover_stale_after_seconds(ttl_seconds)
        )
    redis_client.set(
        observation_key,
        _lease_heartbeat_value(observed_token, at=observed_at),
        ex=max(2, int(ttl_seconds) * 2),
    )
    return False


@contextmanager
def _redis_lease_heartbeat(key: str, ttl_seconds: int, token: str | None = None):
    if token is None:
        raise ValueError("lease heartbeat requires an ownership token")
    stop_event = threading.Event()
    renew_interval_seconds = _lease_renewal_interval_seconds(ttl_seconds)
    guard = RedisLeaseGuard(key=key, token=token, ttl_seconds=ttl_seconds)

    def _renew() -> None:
        while not stop_event.wait(renew_interval_seconds):
            try:
                guard.ensure_owned()
            except LeaseOwnershipLostError:
                return
            except CoordinationUnavailableError:
                continue

    _renewal_thread = threading.Thread(
        target=_renew,
        name=f"threatlens-lease-renewal:{key}",
        daemon=True,
    )
    _renewal_thread.start()
    try:
        guard.ensure_owned()
        yield guard
    finally:
        stop_event.set()
        _renewal_thread.join(timeout=0.1)


def _domain_slot_key(domain: str, slot_number: int) -> str:
    return f"threatlens:domain:{domain}:slot:{slot_number}"


def _best_effort_release_lease(key: str, token: str) -> None:
    try:
        redis_client.eval(
            _RELEASE_LEASE_SCRIPT,
            2,
            key,
            _lease_heartbeat_key(key),
            token,
        )
    except redis.RedisError as exc:
        logger.warning(
            "coordination_lease_release_failed key=%s error_type=%s",
            key,
            type(exc).__name__,
        )


def ensure_lease_owned(lease: object) -> None:
    if isinstance(lease, RedisLeaseGuard):
        lease.ensure_owned()


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
                logger.warning(
                    "coordination_domain_slot_failed domain=%s error_type=%s",
                    domain,
                    type(exc).__name__,
                )
                raise CoordinationUnavailableError("domain slot unavailable") from exc

            if acquired:
                acquired_key = key
                break

        if acquired_key is None:
            time.sleep(DOMAIN_SLOT_WAIT_INTERVAL_SECONDS)

    if acquired_key is None:
        logger.warning("coordination_domain_slot_timeout domain=%s", domain)
        raise DomainSlotUnavailableError(f"domain slot timeout for {domain}")

    try:
        with _redis_lease_heartbeat(
            acquired_key,
            DOMAIN_SLOT_TTL_SECONDS,
            token,
        ) as guard:
            yield guard
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
        logger.warning(
            "coordination_feed_lock_failed feed_id=%s error_type=%s",
            feed_id,
            type(exc).__name__,
        )
        raise CoordinationUnavailableError("feed lock unavailable") from exc

    if not acquired:
        yield False
        return

    try:
        with _redis_lease_heartbeat(key, ttl_seconds, token) as guard:
            yield guard
    finally:
        _best_effort_release_lease(key, token)


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
        _best_effort_release_lease(key, token)


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
    _best_effort_release_lease(TAGGING_REAPPLY_LOCK_KEY, token)


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
        _best_effort_release_lease(key, resolved_token)
