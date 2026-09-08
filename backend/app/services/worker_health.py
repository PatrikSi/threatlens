from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any

from app.core.config import Settings, get_settings
from app.schemas.operations import (
    OperationsWorkerNode,
    OperationsWorkerProbeEvidence,
    OperationsWorkerQueue,
    OperationsWorkerTopologyResponse,
)
from app.services.beat_heartbeat import BeatHeartbeatSnapshot, read_beat_heartbeat
from app.services.queue_execution_canaries import (
    QueueExecutionCanary,
    monitored_worker_queues,
    read_queue_execution_canaries,
    required_worker_queues,
    safe_worker_name,
)
from app.tasks.celery_app import (
    QUEUE_AI,
    QUEUE_AI_REPORTS,
    QUEUE_DEFAULT,
    QUEUE_INGEST,
    QUEUE_LIFECYCLE,
    QUEUE_MAINTENANCE,
    QUEUE_NOTIFICATIONS,
    QUEUE_PROCESSING,
    celery_app,
)


MAX_WORKERS = 64
MAX_COUNT = 1_000_000
MAX_PROCESSED_TOTAL = 1_000_000_000_000
WORKER_TOPOLOGY_CACHE_SECONDS = 5.0
MAX_WORKER_PROBE_TIMEOUT_SECONDS = 8.0
PROBE_NAMES = (
    "ping",
    "active_queues",
    "stats",
    "active",
    "reserved",
    "scheduled",
)

_QUEUE_PRESENTATION = {
    QUEUE_DEFAULT: ("Default tasks", "worker"),
    QUEUE_INGEST: ("Feed ingestion", "worker"),
    QUEUE_PROCESSING: ("Item processing", "worker"),
    QUEUE_NOTIFICATIONS: ("Notifications", "worker-notifications"),
    QUEUE_MAINTENANCE: ("Maintenance", "worker-maintenance"),
    QUEUE_LIFECYCLE: ("Data lifecycle", "worker-maintenance"),
    QUEUE_AI: ("AI enrichment", "worker-ai"),
    QUEUE_AI_REPORTS: ("AI reports", "worker-ai"),
}
_KNOWN_QUEUES = frozenset(_QUEUE_PRESENTATION)
_CANARY_DISPATCH_REASONS = frozenset(
    {"healthy", "missing", "stale", "invalid", "future", "redis_unavailable"}
)


@dataclass(slots=True)
class _ProbeResult:
    name: str
    state: str
    responses: dict[str, Any]
    duration_ms: int
    observed_count: int = 0
    truncated: bool = False
    invalid_envelope_count: int = 0


@dataclass(slots=True)
class _WorkerValues:
    queues: list[str]
    ping_ok: bool | None = None
    capacity: int | None = None
    active_count: int | None = None
    reserved_count: int | None = None
    scheduled_count: int | None = None
    processed_total: int | None = None
    uptime_seconds: int | None = None
    active_by_queue: dict[str, int] | None = None
    reserved_by_queue: dict[str, int] | None = None
    scheduled_by_queue: dict[str, int] | None = None


@dataclass(slots=True)
class _CachedTopology:
    key: tuple[object, ...]
    expires_at: float
    snapshot: OperationsWorkerTopologyResponse


_topology_cache_lock = threading.Lock()
_topology_cache: _CachedTopology | None = None


def collect_worker_topology(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> OperationsWorkerTopologyResponse:
    active_settings = settings or get_settings()
    cache_key = _topology_cache_key(active_settings)
    with _topology_cache_lock:
        cached = _topology_cache
        monotonic_now = time.monotonic()
        if (
            cached is not None
            and cached.key == cache_key
            and cached.expires_at > monotonic_now
        ):
            return cached.snapshot.model_copy(deep=True)
        snapshot = _collect_worker_topology(
            active_settings,
            now=now,
        )
        _store_cached_topology(
            cache_key,
            snapshot,
            observed_at=time.monotonic(),
        )
        return snapshot.model_copy(deep=True)


def _collect_worker_topology(
    active_settings: Settings,
    *,
    now: datetime | None,
) -> OperationsWorkerTopologyResponse:
    generated_at = _as_utc(now or datetime.now(timezone.utc))
    started_at = time.perf_counter()
    probe_timeout = _bounded_probe_timeout(
        active_settings.health_worker_ping_timeout_seconds
    )
    results = _collect_probe_results(
        timeout=probe_timeout,
    )
    observed_worker_names = {
        name
        for result in results.values()
        for name in result.responses
        if isinstance(name, str) and name
    }
    raw_worker_names = sorted(observed_worker_names)[:MAX_WORKERS]
    observed_worker_count = min(
        MAX_COUNT,
        max(
            len(observed_worker_names),
            *(result.observed_count for result in results.values()),
        ),
    )
    worker_inventory_truncated = bool(
        len(observed_worker_names) > MAX_WORKERS
        or any(result.truncated for result in results.values())
    )
    display_names = _display_name_map(raw_worker_names)
    values, invalid_counts = _extract_worker_values(
        raw_worker_names,
        results,
    )
    workers = _build_workers(raw_worker_names, display_names, results, values)
    probes = _build_probe_evidence(
        results,
        raw_worker_names,
        invalid_counts,
    )

    required_queues = required_worker_queues(active_settings)
    observed_queues = {queue for worker in workers for queue in worker.queues}
    monitored_queues = set(monitored_worker_queues(active_settings))
    canaries = read_queue_execution_canaries(
        settings=active_settings,
        queues=sorted(observed_queues & monitored_queues | set(required_queues)),
        now=generated_at,
    )
    canary_dispatch = read_beat_heartbeat(
        redis_url=active_settings.redis_url,
        heartbeat_key=active_settings.beat_scheduler_heartbeat_key,
        stale_after_seconds=max(
            1,
            int(active_settings.beat_heartbeat_stale_after_seconds),
        ),
        now=generated_at,
    )
    inventory_quality = next(
        evidence.quality
        for evidence in probes
        if evidence.probe == "active_queues"
    )
    queue_rows = _build_queues(
        workers,
        worker_values={display_names[name]: values[name] for name in raw_worker_names},
        required_queues=required_queues,
        inventory_quality=inventory_quality,
        canaries=canaries,
        canary_dispatch_ok=canary_dispatch.ok,
    )
    missing_queues = [row.key for row in queue_rows if row.required and not row.consumers]
    stale_queues = [
        row.key for row in queue_rows if row.required and row.execution_reason == "stale"
    ]
    missing_evidence = [
        row.key
        for row in queue_rows
        if row.required
        and row.execution_reason in {
            "missing",
            "invalid",
            "future",
            "redis_unavailable",
        }
    ]
    saturated_workers = [worker.name for worker in workers if worker.saturated]
    status, reason = _topology_status(
        raw_worker_names=raw_worker_names,
        probes=probes,
        inventory_quality=inventory_quality,
        missing_queues=missing_queues,
        stale_queues=stale_queues,
        missing_evidence=missing_evidence,
        saturated=bool(saturated_workers),
        canary_dispatch_ok=canary_dispatch.ok,
    )
    capacities = [worker.capacity for worker in workers]
    total_capacity = (
        min(MAX_COUNT, sum(value for value in capacities if value is not None))
        if (
            not worker_inventory_truncated
            and _probe_quality(probes, "stats") == "complete"
            and workers
            and all(value is not None for value in capacities)
        )
        else None
    )
    return OperationsWorkerTopologyResponse(
        generated_at=generated_at,
        status=status,
        reason=reason,
        timeout_seconds=probe_timeout,
        duration_ms=_duration_ms(started_at),
        responding_worker_count=len(raw_worker_names),
        observed_worker_count=observed_worker_count,
        worker_inventory_truncated=worker_inventory_truncated,
        total_capacity=total_capacity,
        active_count=_complete_topology_sum(
            workers,
            "active_count",
            probe_quality=_probe_quality(probes, "active"),
            inventory_truncated=worker_inventory_truncated,
        ),
        reserved_count=_complete_topology_sum(
            workers,
            "reserved_count",
            probe_quality=_probe_quality(probes, "reserved"),
            inventory_truncated=worker_inventory_truncated,
        ),
        scheduled_count=_complete_topology_sum(
            workers,
            "scheduled_count",
            probe_quality=_probe_quality(probes, "scheduled"),
            inventory_truncated=worker_inventory_truncated,
        ),
        missing_queues=missing_queues,
        stale_execution_queues=stale_queues,
        missing_execution_evidence_queues=missing_evidence,
        canary_dispatch_ok=bool(canary_dispatch.ok),
        canary_dispatch_reason=_canary_dispatch_reason(canary_dispatch),
        canary_dispatch_heartbeat_at=_heartbeat_datetime(canary_dispatch),
        canary_dispatch_age_seconds=_bounded_int(
            canary_dispatch.age_seconds,
            maximum=1_000_000_000,
        ),
        probes=probes,
        workers=workers,
        queues=queue_rows,
    )


def _call_probe(name: str, *, timeout: float) -> _ProbeResult:
    started_at = time.perf_counter()
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
        raw = getattr(inspector, name)()
    except Exception:
        return _ProbeResult(name, "failed", {}, _duration_ms(started_at))
    if raw is None or raw == {}:
        return _ProbeResult(name, "no_replies", {}, _duration_ms(started_at))
    if not isinstance(raw, dict):
        return _ProbeResult(name, "invalid", {}, _duration_ms(started_at))
    observed_count = min(MAX_COUNT, len(raw))
    bounded = {}
    invalid_envelope_count = 0
    for worker_name, response in islice(raw.items(), MAX_WORKERS):
        if not isinstance(worker_name, str) or not worker_name:
            invalid_envelope_count += 1
            continue
        bounded[worker_name] = response
    return _ProbeResult(
        name,
        "complete",
        bounded,
        _duration_ms(started_at),
        observed_count=observed_count,
        truncated=len(raw) > MAX_WORKERS,
        invalid_envelope_count=invalid_envelope_count,
    )


def _collect_probe_results(*, timeout: float) -> dict[str, _ProbeResult]:
    bounded_timeout = _bounded_probe_timeout(timeout)
    with ThreadPoolExecutor(
        max_workers=len(PROBE_NAMES),
        thread_name_prefix="worker-health",
    ) as executor:
        pending = {
            name: executor.submit(_call_probe, name, timeout=bounded_timeout)
            for name in PROBE_NAMES
        }
        return {name: pending[name].result() for name in PROBE_NAMES}


def _bounded_probe_timeout(timeout: object) -> float:
    try:
        parsed = float(timeout)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return min(MAX_WORKER_PROBE_TIMEOUT_SECONDS, max(0.1, parsed))


def _extract_worker_values(
    worker_names: list[str],
    results: dict[str, _ProbeResult],
) -> tuple[dict[str, _WorkerValues], dict[str, int]]:
    values = {name: _WorkerValues(queues=[]) for name in worker_names}
    invalid_counts = {name: 0 for name in PROBE_NAMES}
    for worker_name in worker_names:
        worker = values[worker_name]
        ping = results["ping"].responses.get(worker_name)
        if worker_name in results["ping"].responses:
            if isinstance(ping, dict) and ping.get("ok") == "pong":
                worker.ping_ok = True
            else:
                worker.ping_ok = False
                invalid_counts["ping"] += 1

        queues = results["active_queues"].responses.get(worker_name)
        if worker_name in results["active_queues"].responses:
            if isinstance(queues, list):
                bounded_queues = queues[:64]
                worker.queues = sorted(
                    {
                        entry.get("name")
                        for entry in bounded_queues
                        if isinstance(entry, dict)
                        and isinstance(entry.get("name"), str)
                        and entry.get("name") in _KNOWN_QUEUES
                    }
                )
                if len(queues) > 64 or any(
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("name"), str)
                    for entry in bounded_queues
                ):
                    invalid_counts["active_queues"] += 1
            else:
                invalid_counts["active_queues"] += 1

        stats = results["stats"].responses.get(worker_name)
        if worker_name in results["stats"].responses:
            if isinstance(stats, dict):
                pool = stats.get("pool")
                capacity = None
                if isinstance(pool, dict):
                    capacity = _bounded_int(
                        pool.get("max-concurrency", pool.get("max_concurrency")),
                        maximum=MAX_COUNT,
                    )
                if capacity is None or capacity <= 0:
                    invalid_counts["stats"] += 1
                else:
                    worker.capacity = capacity
                worker.uptime_seconds = _bounded_int(
                    stats.get("uptime"),
                    maximum=1_000_000_000,
                )
                totals = stats.get("total")
                if isinstance(totals, dict):
                    processed_values = list(islice(totals.values(), 4_097))
                    if len(processed_values) <= 4_096 and all(
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and value >= 0
                        for value in processed_values
                    ):
                        worker.processed_total = min(
                            MAX_PROCESSED_TOTAL,
                            sum(processed_values),
                        )
            else:
                invalid_counts["stats"] += 1

        for probe_name, attribute in (
            ("active", "active_count"),
            ("reserved", "reserved_count"),
            ("scheduled", "scheduled_count"),
        ):
            tasks = results[probe_name].responses.get(worker_name)
            if worker_name not in results[probe_name].responses:
                continue
            if isinstance(tasks, list):
                setattr(worker, attribute, min(MAX_COUNT, len(tasks)))
                queue_counts = (
                    _task_counts_by_queue(
                        tasks,
                        scheduled=probe_name == "scheduled",
                    )
                    if len(tasks) <= MAX_COUNT
                    else None
                )
                setattr(
                    worker,
                    f"{probe_name}_by_queue",
                    queue_counts,
                )
                if queue_counts is None:
                    invalid_counts[probe_name] += 1
            else:
                invalid_counts[probe_name] += 1
    return values, invalid_counts


def _build_workers(
    raw_worker_names: list[str],
    display_names: dict[str, str],
    results: dict[str, _ProbeResult],
    values: dict[str, _WorkerValues],
) -> list[OperationsWorkerNode]:
    workers = []
    for raw_name in raw_worker_names:
        responded_to = [
            name for name in PROBE_NAMES if raw_name in results[name].responses
        ]
        missing_responses = [name for name in PROBE_NAMES if name not in responded_to]
        entry = values[raw_name]
        saturated = (
            entry.capacity is not None
            and entry.capacity > 0
            and entry.active_count is not None
            and entry.active_count >= entry.capacity
            and entry.reserved_count is not None
            and entry.reserved_count > 0
        )
        workers.append(
            OperationsWorkerNode(
                name=display_names[raw_name],
                queues=entry.queues,
                responded_to=responded_to,
                missing_responses=missing_responses,
                ping_ok=entry.ping_ok,
                capacity=entry.capacity,
                active_count=entry.active_count,
                reserved_count=entry.reserved_count,
                scheduled_count=entry.scheduled_count,
                processed_total=entry.processed_total,
                uptime_seconds=entry.uptime_seconds,
                saturated=saturated,
            )
        )
    return workers


def _build_probe_evidence(
    results: dict[str, _ProbeResult],
    all_worker_names: list[str],
    invalid_counts: dict[str, int],
) -> list[OperationsWorkerProbeEvidence]:
    evidence = []
    expected = set(all_worker_names)
    for name in PROBE_NAMES:
        result = results[name]
        responders = set(result.responses) & expected
        missing_count = len(expected - responders)
        invalid_count = min(
            MAX_WORKERS,
            result.invalid_envelope_count + invalid_counts[name],
        )
        quality = result.state
        if quality == "complete":
            if missing_count or result.truncated:
                quality = "partial"
            elif invalid_count:
                quality = "invalid"
        evidence.append(
            OperationsWorkerProbeEvidence(
                probe=name,
                quality=quality,
                responder_count=len(responders),
                observed_responder_count=result.observed_count,
                responses_truncated=result.truncated,
                missing_responder_count=missing_count,
                invalid_response_count=invalid_count,
                duration_ms=result.duration_ms,
            )
        )
    return evidence


def _build_queues(
    workers: list[OperationsWorkerNode],
    *,
    worker_values: dict[str, _WorkerValues],
    required_queues: list[str],
    inventory_quality: str,
    canaries: dict[str, QueueExecutionCanary],
    canary_dispatch_ok: bool,
) -> list[OperationsWorkerQueue]:
    observed = {queue for worker in workers for queue in worker.queues}
    queue_names = sorted(set(required_queues) | observed)
    worker_by_name = {worker.name: worker for worker in workers}
    rows = []
    for queue_name in queue_names:
        consumers = sorted(
            worker.name for worker in workers if queue_name in worker.queues
        )[:MAX_WORKERS]
        consumer_nodes = [worker_by_name[name] for name in consumers]
        capacity = _complete_sum(consumer_nodes, "capacity")
        worker_pool_active_count = _complete_sum(consumer_nodes, "active_count")
        consumer_values = [worker_values[name] for name in consumers]
        active_count = _complete_queue_sum(
            consumer_values,
            "active_by_queue",
            queue_name,
        )
        reserved_count = _complete_queue_sum(
            consumer_values,
            "reserved_by_queue",
            queue_name,
        )
        scheduled_count = _complete_queue_sum(
            consumer_values,
            "scheduled_by_queue",
            queue_name,
        )
        saturated = bool(
            capacity is not None
            and capacity > 0
            and worker_pool_active_count is not None
            and worker_pool_active_count >= capacity
            and reserved_count is not None
            and reserved_count > 0
        )
        canary = canaries.get(
            queue_name,
            QueueExecutionCanary(queue_name, "missing"),
        )
        required = queue_name in required_queues
        if not consumers:
            status = "critical" if inventory_quality == "complete" else "unknown"
        elif canary.reason == "stale" and not canary_dispatch_ok:
            status = "unknown"
        elif canary.reason == "stale" or saturated:
            status = "degraded"
        elif canary.reason in {
            "missing",
            "invalid",
            "future",
            "redis_unavailable",
        }:
            status = "unknown"
        else:
            status = "healthy"
        label, service_hint = _QUEUE_PRESENTATION[queue_name]
        rows.append(
            OperationsWorkerQueue(
                key=queue_name,
                label=label,
                service_hint=service_hint,
                required=required,
                status=status,
                consumers=consumers,
                consumer_count=len(consumers),
                capacity=capacity,
                active_count=active_count,
                reserved_count=reserved_count,
                scheduled_count=scheduled_count,
                saturated=saturated,
                execution_reason=canary.reason,
                execution_heartbeat_at=canary.heartbeat_at,
                execution_age_seconds=canary.age_seconds,
                execution_worker=canary.worker_name,
            )
        )
    return rows


def _topology_status(
    *,
    raw_worker_names: list[str],
    probes: list[OperationsWorkerProbeEvidence],
    inventory_quality: str,
    missing_queues: list[str],
    stale_queues: list[str],
    missing_evidence: list[str],
    saturated: bool,
    canary_dispatch_ok: bool,
) -> tuple[str, str]:
    if not raw_worker_names:
        if any(probe.quality in {"failed", "invalid"} for probe in probes):
            return "unavailable", "probe_failed"
        return "unavailable", "no_replies"
    if inventory_quality in {"failed", "no_replies", "invalid"}:
        return "unavailable", "queue_inventory_unavailable"
    if missing_queues and inventory_quality == "complete":
        return "critical", "missing_consumers"
    if any(probe.quality == "failed" for probe in probes):
        return "degraded", "probe_failed"
    if inventory_quality == "partial" or any(
        probe.quality != "complete" for probe in probes
    ):
        return "degraded", "partial_inventory"
    if not canary_dispatch_ok and (stale_queues or missing_evidence):
        return "degraded", "canary_dispatch_unavailable"
    if stale_queues:
        return "degraded", "execution_stalled"
    if saturated:
        return "degraded", "saturated"
    if missing_evidence:
        return "degraded", "execution_evidence_missing"
    return "healthy", "healthy"


def _display_name_map(raw_names: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    used: set[str] = set()
    for raw_name in raw_names:
        candidate = safe_worker_name(raw_name)
        if candidate in used:
            digest = hashlib.sha256(
                str(raw_name).encode("utf-8", errors="replace")
            ).hexdigest()[:8]
            candidate = f"{candidate[:246]}-{digest}"
        mapped[raw_name] = candidate
        used.add(candidate)
    return mapped


def _complete_sum(workers: list[OperationsWorkerNode], attribute: str) -> int | None:
    if not workers:
        return None
    values = [getattr(worker, attribute) for worker in workers]
    if any(value is None for value in values):
        return None
    return min(MAX_COUNT, sum(int(value) for value in values))


def _complete_topology_sum(
    workers: list[OperationsWorkerNode],
    attribute: str,
    *,
    probe_quality: str,
    inventory_truncated: bool,
) -> int | None:
    if inventory_truncated or probe_quality != "complete":
        return None
    return _complete_sum(workers, attribute)


def _probe_quality(
    probes: list[OperationsWorkerProbeEvidence],
    probe_name: str,
) -> str:
    return next(
        (probe.quality for probe in probes if probe.probe == probe_name),
        "failed",
    )


def _complete_queue_sum(
    workers: list[_WorkerValues],
    attribute: str,
    queue_name: str,
) -> int | None:
    if not workers:
        return None
    values = [getattr(worker, attribute) for worker in workers]
    if any(value is None for value in values):
        return None
    return min(
        MAX_COUNT,
        sum(value.get(queue_name, 0) for value in values if value is not None),
    )


def _task_counts_by_queue(
    tasks: list[object],
    *,
    scheduled: bool,
) -> dict[str, int] | None:
    counts = {queue: 0 for queue in _KNOWN_QUEUES}
    for task in islice(tasks, MAX_COUNT):
        if not isinstance(task, dict):
            return None
        request = task.get("request") if scheduled else task
        if not isinstance(request, dict):
            return None
        delivery_info = request.get("delivery_info")
        if not isinstance(delivery_info, dict):
            return None
        queue_name = delivery_info.get("routing_key")
        if not isinstance(queue_name, str) or queue_name not in _KNOWN_QUEUES:
            return None
        counts[queue_name] = min(MAX_COUNT, counts[queue_name] + 1)
    return counts


def _bounded_int(value: object, *, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return min(maximum, max(0, parsed))


def _canary_dispatch_reason(snapshot: BeatHeartbeatSnapshot) -> str:
    reason = str(snapshot.reason)
    return reason if reason in _CANARY_DISPATCH_REASONS else "invalid"


def _heartbeat_datetime(snapshot: BeatHeartbeatSnapshot) -> datetime | None:
    if not snapshot.heartbeat_at:
        return None
    try:
        return _as_utc(datetime.fromisoformat(snapshot.heartbeat_at))
    except (TypeError, ValueError):
        return None


def _topology_cache_key(settings: Settings) -> tuple[object, ...]:
    return (
        float(settings.health_worker_ping_timeout_seconds),
        bool(settings.ai_enabled),
        str(settings.redis_url),
        str(settings.beat_scheduler_heartbeat_key),
        int(settings.beat_heartbeat_stale_after_seconds),
    )


def _store_cached_topology(
    key: tuple[object, ...],
    snapshot: OperationsWorkerTopologyResponse,
    *,
    observed_at: float,
) -> None:
    global _topology_cache
    _topology_cache = _CachedTopology(
        key=key,
        expires_at=observed_at + WORKER_TOPOLOGY_CACHE_SECONDS,
        snapshot=snapshot.model_copy(deep=True),
    )


def _clear_worker_topology_cache() -> None:
    global _topology_cache
    with _topology_cache_lock:
        _topology_cache = None


def _duration_ms(started_at: float) -> int:
    return min(120_000, max(0, int((time.perf_counter() - started_at) * 1000)))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
