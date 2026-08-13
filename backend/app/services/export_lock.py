import logging
import uuid
from contextlib import contextmanager
from typing import Iterator

from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.redis_client import redis_client_from_url

logger = logging.getLogger(__name__)


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
        raise ExportLockUnavailableError("Export concurrency service is unavailable") from exc
    if not acquired:
        raise ExportAlreadyRunningError("Another export is already running for this user")

    try:
        yield
    finally:
        try:
            client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                token,
            )
        except RedisError:
            logger.warning("export_lock_release_failed user_id=%s", user_id, exc_info=True)
