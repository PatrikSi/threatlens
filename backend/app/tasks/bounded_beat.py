"""Prevent health probes from filling Redis queues while consumers are stopped."""

from __future__ import annotations

import logging

from celery import Celery
from celery.beat import PersistentScheduler, ScheduleEntry
from kombu.transport.redis import Channel as RedisChannel

logger = logging.getLogger(__name__)
CANARY_TASK = "app.tasks.system_health_tasks.record_queue_execution_canary"
MAX_CANARY_QUEUE_DEPTH = 8
INSPECTION_TIMEOUT_SECONDS = 2


def canary_queue_has_capacity(app: Celery, queue: str) -> bool:
    """Count all configured priority lists, including the broker's key prefix.

    Kombu's Redis channel owns queue naming. Using its bounded size operation
    avoids guessing separators, priority steps or prefixes. No reservation key
    survives a crash between this check and publication, so an empty recovered
    queue can always receive another probe.
    """
    try:
        options = {
            **(app.conf.broker_transport_options or {}),
            "socket_connect_timeout": INSPECTION_TIMEOUT_SECONDS,
            "socket_timeout": INSPECTION_TIMEOUT_SECONDS,
            "retry_on_timeout": False,
        }
        with app.connection_for_write(
            connect_timeout=INSPECTION_TIMEOUT_SECONDS, transport_options=options
        ) as connection:
            with connection.channel() as channel:
                if not isinstance(channel, RedisChannel):
                    logger.warning(
                        "queue_canary_admission_unsupported", extra={"queue": queue}
                    )
                    return False
                return channel._size(queue) < MAX_CANARY_QUEUE_DEPTH
    except Exception as exc:
        logger.warning(
            "queue_canary_admission_unavailable error_type=%s",
            type(exc).__name__,
            extra={"queue": queue},
        )
        return False


class BoundedCanaryScheduler(PersistentScheduler):
    """The existing singleton Beat schedule with admission for cheap probes."""

    def apply_async(self, entry: ScheduleEntry, producer=None, advance=True, **kwargs):
        if entry.task != CANARY_TASK:
            return super().apply_async(
                entry, producer=producer, advance=advance, **kwargs
            )
        current = self.reserve(entry) if advance else entry
        queue = current.options.get("queue")
        if isinstance(queue, str) and canary_queue_has_capacity(self.app, queue):
            return super().apply_async(
                current, producer=producer, advance=False, **kwargs
            )
        # A skipped probe is still a completed scheduling attempt. Persisting its
        # next due time prevents an unavailable broker causing a tight Beat loop.
        self._tasks_since_sync += 1
        if self.should_sync():
            self._do_sync()
        return None
