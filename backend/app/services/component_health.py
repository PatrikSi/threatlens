"""Shared dependency probes used by readiness routes and operations diagnostics.

These probes own dependency access and typed snapshots. Consumers decide which
checks affect their status and how much detail the caller may see.
"""

from __future__ import annotations

import logging
from typing import NamedTuple, TypedDict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging_config import verbose_logging_enabled
from app.core.redis_client import redis_client_from_url
from app.services.beat_heartbeat import BeatHealthSnapshot, read_beat_heartbeat
from app.services.queue_execution_canaries import required_worker_queues
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


class WorkerQueueCoverage(TypedDict):
    required: list[str]
    covered: list[str]
    missing: list[str]
    by_worker: dict[str, list[str]]


class WorkerHealthSnapshot(NamedTuple):
    healthy: bool
    workers: dict[str, str]
    queues: WorkerQueueCoverage


def database_health_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning(
            "database_health_check_failed error_type=%s",
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )
        return False


def redis_health_ok(settings: Settings) -> bool:
    try:
        client = redis_client_from_url(settings.redis_url, settings=settings)
        return bool(client.ping())
    except Exception as exc:
        logger.warning(
            "redis_health_check_failed error_type=%s",
            type(exc).__name__,
            exc_info=verbose_logging_enabled(settings),
        )
        return False


def worker_health_snapshot(settings: Settings) -> WorkerHealthSnapshot:
    worker_ok = False
    workers: dict[str, str] = {}
    queue_snapshot: WorkerQueueCoverage = {
        "required": required_worker_queues(settings),
        "covered": [],
        "missing": required_worker_queues(settings),
        "by_worker": {},
    }
    try:
        inspector = celery_app.control.inspect(
            timeout=settings.health_worker_ping_timeout_seconds
        )
        raw_ping = inspector.ping() or {}
        for worker_name, response in raw_ping.items():
            pong_value = response.get("ok") if isinstance(response, dict) else None
            workers[worker_name] = str(pong_value or "unknown")
        raw_queues = inspector.active_queues() or {}
        covered_queues: set[str] = set()
        queues_by_worker: dict[str, list[str]] = {}
        for worker_name, queues in raw_queues.items():
            worker_queue_names = sorted(
                queue.get("name")
                for queue in queues
                if isinstance(queue, dict) and isinstance(queue.get("name"), str)
            )
            queues_by_worker[worker_name] = worker_queue_names
            covered_queues.update(worker_queue_names)
        required_queues = set(required_worker_queues(settings))
        missing_queues = sorted(required_queues - covered_queues)
        queue_snapshot = {
            "required": sorted(required_queues),
            "covered": sorted(covered_queues),
            "missing": missing_queues,
            "by_worker": queues_by_worker,
        }
        worker_ok = bool(raw_ping) and not missing_queues
    except Exception as exc:
        logger.warning(
            "worker_health_check_failed error_type=%s",
            type(exc).__name__,
            exc_info=verbose_logging_enabled(settings),
        )
        worker_ok = False
    return WorkerHealthSnapshot(worker_ok, workers, queue_snapshot)


def beat_health_snapshot(settings: Settings) -> BeatHealthSnapshot:
    return BeatHealthSnapshot(
        scheduler=read_beat_heartbeat(
            redis_url=settings.redis_url,
            heartbeat_key=settings.beat_scheduler_heartbeat_key,
            stale_after_seconds=settings.beat_heartbeat_stale_after_seconds,
        ),
        worker_round_trip=read_beat_heartbeat(
            redis_url=settings.redis_url,
            heartbeat_key=settings.beat_heartbeat_key,
            stale_after_seconds=settings.beat_heartbeat_stale_after_seconds,
        ),
    )
