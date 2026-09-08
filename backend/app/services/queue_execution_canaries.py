from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.redis_client import redis_client_from_url
from app.tasks.celery_app import (
    QUEUE_AI,
    QUEUE_AI_REPORTS,
    QUEUE_DEFAULT,
    QUEUE_INGEST,
    QUEUE_LIFECYCLE,
    QUEUE_MAINTENANCE,
    QUEUE_NOTIFICATIONS,
    QUEUE_PROCESSING,
)


QUEUE_CANARY_KEY_PREFIX = "threatlens:health:queue-canary"
MAX_WORKER_NAME_CHARS = 255
MAX_CANARY_PAYLOAD_BYTES = 4_096
_FUTURE_TOLERANCE_SECONDS = 5
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.:@-]+")


@dataclass(frozen=True, slots=True)
class QueueExecutionCanary:
    queue: str
    reason: str
    heartbeat_at: datetime | None = None
    age_seconds: int | None = None
    worker_name: str | None = None


def required_worker_queues(settings: Settings) -> list[str]:
    queues = [
        QUEUE_INGEST,
        QUEUE_PROCESSING,
        QUEUE_NOTIFICATIONS,
        QUEUE_MAINTENANCE,
        QUEUE_LIFECYCLE,
    ]
    if settings.ai_enabled:
        queues.extend([QUEUE_AI, QUEUE_AI_REPORTS])
    return queues


def monitored_worker_queues(settings: Settings) -> list[str]:
    return [QUEUE_DEFAULT, *required_worker_queues(settings)]


def safe_worker_name(value: object) -> str:
    raw = str(value or "unknown")
    normalized = _SAFE_NAME_PATTERN.sub("-", raw).strip("-") or "unknown"
    changed = normalized != raw
    if len(normalized) > MAX_WORKER_NAME_CHARS:
        changed = True
        normalized = normalized[: MAX_WORKER_NAME_CHARS - 9]
    if changed:
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
        normalized = f"{normalized[: MAX_WORKER_NAME_CHARS - 9]}-{digest}"
    return normalized[:MAX_WORKER_NAME_CHARS]


def write_queue_execution_canary(
    *,
    settings: Settings,
    expected_queue: str,
    actual_queue: str | None,
    worker_name: object,
    now: datetime | None = None,
) -> QueueExecutionCanary:
    if expected_queue not in monitored_worker_queues(settings):
        return QueueExecutionCanary(expected_queue[:64] or "unknown", "invalid")
    if actual_queue != expected_queue:
        return QueueExecutionCanary(expected_queue, "invalid")

    observed_at = _as_utc(now or datetime.now(timezone.utc))
    safe_name = safe_worker_name(worker_name)
    payload = json.dumps(
        {
            "schema_version": 1,
            "queue": expected_queue,
            "heartbeat_at": observed_at.isoformat(),
            "worker_name": safe_name,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    client = None
    try:
        client = redis_client_from_url(
            settings.redis_url,
            decode_responses=True,
            settings=settings,
        )
        client.set(
            _queue_canary_key(expected_queue),
            payload,
            # Keep evidence past the stale threshold so operators can distinguish
            # a stalled consumer from a queue that has never executed a canary.
            ex=max(
                1,
                int(settings.beat_heartbeat_ttl_seconds),
                int(settings.beat_heartbeat_stale_after_seconds) * 3,
            ),
        )
    except Exception:
        return QueueExecutionCanary(expected_queue, "redis_unavailable")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return QueueExecutionCanary(
        expected_queue,
        "fresh",
        heartbeat_at=observed_at,
        age_seconds=0,
        worker_name=safe_name,
    )


def read_queue_execution_canaries(
    *,
    settings: Settings,
    queues: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, QueueExecutionCanary]:
    requested = sorted(
        set(queues if queues is not None else monitored_worker_queues(settings))
        & set(monitored_worker_queues(settings))
    )
    if not requested:
        return {}

    client = None
    try:
        client = redis_client_from_url(
            settings.redis_url,
            decode_responses=True,
            settings=settings,
        )
        payloads = client.mget([_queue_canary_key(queue) for queue in requested])
        if not isinstance(payloads, (list, tuple)) or len(payloads) != len(requested):
            raise ValueError("invalid queue canary response")
    except Exception:
        return {
            queue: QueueExecutionCanary(queue, "redis_unavailable")
            for queue in requested
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    reference = _as_utc(now or datetime.now(timezone.utc))
    return {
        queue: _parse_queue_execution_canary(
            queue,
            payload,
            stale_after_seconds=max(
                1,
                int(settings.beat_heartbeat_stale_after_seconds),
            ),
            now=reference,
        )
        for queue, payload in zip(requested, payloads, strict=True)
    }


def _parse_queue_execution_canary(
    queue: str,
    payload: object,
    *,
    stale_after_seconds: int,
    now: datetime,
) -> QueueExecutionCanary:
    if not payload:
        return QueueExecutionCanary(queue, "missing")
    try:
        raw_payload = (
            payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        )
        if len(raw_payload.encode("utf-8", errors="replace")) > MAX_CANARY_PAYLOAD_BYTES:
            raise ValueError("oversized queue canary")
        decoded = json.loads(raw_payload)
        worker_name_value = decoded.get("worker_name") if isinstance(decoded, dict) else None
        if (
            not isinstance(decoded, dict)
            or decoded.get("schema_version") != 1
            or decoded.get("queue") != queue
            or not isinstance(worker_name_value, str)
            or not worker_name_value
        ):
            raise ValueError("invalid queue canary")
        heartbeat_at = _as_utc(datetime.fromisoformat(str(decoded["heartbeat_at"])))
        worker_name = safe_worker_name(worker_name_value)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return QueueExecutionCanary(queue, "invalid")

    delta_seconds = (now - heartbeat_at).total_seconds()
    if delta_seconds < -_FUTURE_TOLERANCE_SECONDS:
        return QueueExecutionCanary(
            queue,
            "future",
            heartbeat_at=heartbeat_at,
            worker_name=worker_name,
        )
    age_seconds = max(0, int(delta_seconds))
    reason = "stale" if age_seconds > stale_after_seconds else "fresh"
    return QueueExecutionCanary(
        queue,
        reason,
        heartbeat_at=heartbeat_at,
        age_seconds=age_seconds,
        worker_name=worker_name,
    )


def _queue_canary_key(queue: str) -> str:
    return f"{QUEUE_CANARY_KEY_PREFIX}:{queue}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
