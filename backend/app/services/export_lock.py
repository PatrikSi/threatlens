import logging
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.redis_client import redis_client_from_url

logger = logging.getLogger(__name__)
_RENEW_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)
_RELEASE_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class ExportAlreadyRunningError(RuntimeError):
    pass


class ExportLockUnavailableError(RuntimeError):
    pass


@contextmanager
def acquire_export_lock(*, user_id: uuid.UUID, settings: Settings) -> Iterator[None]:
    client = redis_client_from_url(settings.redis_url, decode_responses=True, settings=settings)
    key = f"threatlens:export:user:{user_id}"
    token = str(uuid.uuid4())
    try:
        acquired = client.set(key, token, nx=True, ex=settings.export_lock_ttl_seconds)
    except RedisError as exc:
        _close_export_client(client, user_id=user_id)
        raise ExportLockUnavailableError("Export concurrency service is unavailable") from exc
    if not acquired:
        _close_export_client(client, user_id=user_id)
        raise ExportAlreadyRunningError("Another export is already running for this user")

    stop_renewal = threading.Event()
    renewal_thread = threading.Thread(
        target=_renew_export_lock_until_stopped,
        kwargs={
            "client": client,
            "key": key,
            "token": token,
            "ttl_seconds": settings.export_lock_ttl_seconds,
            "stop_event": stop_renewal,
            "user_id": user_id,
        },
        name=f"threatlens-export-lock:{user_id}",
        daemon=True,
    )
    renewal_started = False
    try:
        renewal_thread.start()
        renewal_started = True
        yield
        try:
            still_owned = _renew_export_lock(
                client,
                key=key,
                token=token,
                ttl_seconds=settings.export_lock_ttl_seconds,
            )
        except RedisError as exc:
            raise ExportLockUnavailableError(
                "Export concurrency lock could not be verified"
            ) from exc
        if not still_owned:
            raise ExportLockUnavailableError(
                "Export concurrency lock expired before generation completed"
            )
    finally:
        if renewal_started:
            stop_renewal.set()
            renewal_thread.join(timeout=1)
        try:
            client.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
        except RedisError:
            logger.warning("export_lock_release_failed user_id=%s", user_id, exc_info=True)
        _close_export_client(client, user_id=user_id)


def _renew_export_lock(
    client: Any,
    *,
    key: str,
    token: str,
    ttl_seconds: int,
) -> bool:
    return bool(
        client.eval(
            _RENEW_LOCK_SCRIPT,
            1,
            key,
            token,
            ttl_seconds,
        )
    )


def _renew_export_lock_until_stopped(
    *,
    client: Any,
    key: str,
    token: str,
    ttl_seconds: int,
    stop_event: threading.Event,
    user_id: uuid.UUID,
) -> None:
    interval_seconds = _lock_renewal_interval_seconds(ttl_seconds)
    while not stop_event.wait(interval_seconds):
        try:
            if not _renew_export_lock(
                client, key=key, token=token, ttl_seconds=ttl_seconds
            ):
                logger.error("export_lock_ownership_lost user_id=%s", user_id)
                return
        except RedisError:
            logger.warning(
                "export_lock_renewal_failed user_id=%s", user_id, exc_info=True
            )


def _lock_renewal_interval_seconds(ttl_seconds: int) -> float:
    return max(0.1, ttl_seconds / 3)


def _close_export_client(client: Any, *, user_id: uuid.UUID) -> None:
    try:
        client.close()
    except RedisError:
        logger.warning("export_lock_close_failed user_id=%s", user_id, exc_info=True)
