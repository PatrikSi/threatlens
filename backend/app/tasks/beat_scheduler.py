import logging
from datetime import datetime, timezone

import redis
from celery.beat import PersistentScheduler

from app.core.config import get_settings
from app.core.redis_client import redis_client_from_url

logger = logging.getLogger(__name__)


def write_scheduler_heartbeat(
    client,
    *,
    heartbeat_key: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> bool:
    heartbeat_at = (now or datetime.now(timezone.utc)).isoformat()
    try:
        client.set(heartbeat_key, heartbeat_at, ex=ttl_seconds)
    except redis.RedisError as exc:
        logger.warning("beat_scheduler_heartbeat_write_failed error_type=%s", type(exc).__name__)
        return False
    return True


class WatchdogPersistentScheduler(PersistentScheduler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        settings = get_settings()
        self._heartbeat_client = redis_client_from_url(settings.redis_url, settings=settings)
        self._heartbeat_key = settings.beat_scheduler_heartbeat_key
        self._heartbeat_ttl_seconds = settings.beat_heartbeat_ttl_seconds

    def tick(self, *args, **kwargs):
        next_interval = super().tick(*args, **kwargs)
        write_scheduler_heartbeat(
            self._heartbeat_client,
            heartbeat_key=self._heartbeat_key,
            ttl_seconds=self._heartbeat_ttl_seconds,
        )
        return next_interval
