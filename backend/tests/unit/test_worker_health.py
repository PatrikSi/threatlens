from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import worker_health
from app.services.beat_heartbeat import BeatHeartbeatSnapshot
from app.services.queue_execution_canaries import QueueExecutionCanary


class _Inspector:
    def __init__(self, responses, failures: set[str] | None = None):
        self.responses = responses
        self.failures = failures or set()

    def _response(self, name: str):
        if name in self.failures:
            raise RuntimeError("broker detail must not escape")
        return self.responses.get(name)

    def ping(self):
        return self._response("ping")

    def active_queues(self):
        return self._response("active_queues")

    def stats(self):
        return self._response("stats")

    def active(self):
        return self._response("active")

    def reserved(self):
        return self._response("reserved")

    def scheduled(self):
        return self._response("scheduled")


def _settings(**overrides):
    values = {
        "health_worker_ping_timeout_seconds": 1.0,
        "ai_enabled": False,
        "redis_url": "redis://redis.internal/0",
        "beat_scheduler_heartbeat_key": "scheduler-heartbeat",
        "beat_heartbeat_stale_after_seconds": 180,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _responses(*, queues_by_worker, capacity=4, active_count=1, reserved_count=0):
    names = list(queues_by_worker)
    return {
        "ping": {name: {"ok": "pong"} for name in names},
        "active_queues": {
            name: [{"name": queue} for queue in queues]
            for name, queues in queues_by_worker.items()
        },
        "stats": {
            name: {
                "pool": {"max-concurrency": capacity},
                "uptime": 3600,
                "total": {"app.tasks.safe": 12},
                "broker": "redis://admin:secret@private.internal/0",
            }
            for name in names
        },
        "active": {
            name: [
                {
                    "id": f"task-{index}",
                    "args": ["secret-task-argument"],
                    "delivery_info": {"routing_key": queues_by_worker[name][0]},
                }
                for index in range(active_count)
            ]
            for name in names
        },
        "reserved": {
            name: [
                {
                    "id": f"reserved-{index}",
                    "delivery_info": {"routing_key": queues_by_worker[name][0]},
                }
                for index in range(reserved_count)
            ]
            for name in names
        },
        "scheduled": {name: [] for name in names},
    }


def _install(
    monkeypatch,
    responses,
    *,
    failures=None,
    canary_reason="fresh",
    canary_dispatch_reason="healthy",
):
    worker_health._clear_worker_topology_cache()
    inspector = _Inspector(responses, failures=failures)
    monkeypatch.setattr(
        worker_health.celery_app.control,
        "inspect",
        lambda timeout: inspector,
    )

    def read_canaries(*, queues, **_kwargs):
        return {
            queue: QueueExecutionCanary(
                queue,
                canary_reason,
                heartbeat_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
                age_seconds=10,
                worker_name="worker@node-1",
            )
            for queue in queues
        }

    monkeypatch.setattr(worker_health, "read_queue_execution_canaries", read_canaries)
    monkeypatch.setattr(
        worker_health,
        "read_beat_heartbeat",
        lambda **_kwargs: BeatHeartbeatSnapshot(
            canary_dispatch_reason == "healthy",
            "2026-09-01T12:00:00+00:00",
            10,
            canary_dispatch_reason,
        ),
    )


def test_worker_topology_reports_allowlisted_capacity_load_and_queue_consumers(
    monkeypatch,
):
    responses = _responses(
        queues_by_worker={
            "worker@node-1": ["default", "ingest", "processing"],
            "worker@node-2": ["notifications", "maintenance"],
        },
        capacity=4,
        active_count=1,
    )
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(
        _settings(),
        now=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert result.status == "healthy"
    assert result.reason == "healthy"
    assert result.responding_worker_count == 2
    assert result.total_capacity == 8
    assert result.active_count == 2
    assert {probe.quality for probe in result.probes} == {"complete"}
    queues = {queue.key: queue for queue in result.queues}
    assert queues["ingest"].consumers == ["worker@node-1"]
    assert queues["notifications"].service_hint == "worker-notifications"
    rendered = result.model_dump_json()
    assert "secret-task-argument" not in rendered
    assert "admin:secret" not in rendered
    assert "private.internal" not in rendered


def test_worker_topology_distinguishes_partial_inventory(monkeypatch):
    responses = _responses(
        queues_by_worker={
            "worker@node-1": ["ingest", "processing"],
            "worker@node-2": ["notifications", "maintenance"],
        }
    )
    responses["active_queues"] = {
        "worker@node-1": [{"name": "ingest"}, {"name": "processing"}]
    }
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    assert result.status == "degraded"
    assert result.reason == "partial_inventory"
    queue_probe = next(
        probe for probe in result.probes if probe.probe == "active_queues"
    )
    assert queue_probe.quality == "partial"
    assert queue_probe.missing_responder_count == 1
    assert "active_queues" in result.workers[1].missing_responses


def test_worker_topology_distinguishes_missing_consumers(monkeypatch):
    responses = _responses(
        queues_by_worker={"worker@node-1": ["ingest", "processing"]}
    )
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    assert result.status == "critical"
    assert result.reason == "missing_consumers"
    assert result.missing_queues == ["maintenance", "notifications"]
    queues = {queue.key: queue for queue in result.queues}
    assert queues["maintenance"].status == "critical"
    assert queues["maintenance"].consumer_count == 0


def test_worker_topology_distinguishes_no_replies_and_failed_inventory(monkeypatch):
    empty = {name: None for name in worker_health.PROBE_NAMES}
    _install(monkeypatch, empty)

    no_replies = worker_health.collect_worker_topology(_settings())

    assert no_replies.status == "unavailable"
    assert no_replies.reason == "no_replies"
    assert {probe.quality for probe in no_replies.probes} == {"no_replies"}

    responses = _responses(
        queues_by_worker={
            "worker@node-1": [
                "ingest",
                "processing",
                "notifications",
                "maintenance",
            ]
        }
    )
    _install(monkeypatch, responses, failures={"active_queues"})

    failed = worker_health.collect_worker_topology(_settings())

    assert failed.status == "unavailable"
    assert failed.reason == "queue_inventory_unavailable"
    queue_probe = next(
        probe for probe in failed.probes if probe.probe == "active_queues"
    )
    assert queue_probe.quality == "failed"

    _install(monkeypatch, responses, failures={"stats"})
    auxiliary_failure = worker_health.collect_worker_topology(_settings())
    assert auxiliary_failure.status == "degraded"
    assert auxiliary_failure.reason == "probe_failed"


def test_worker_topology_prioritizes_stall_saturation_and_missing_evidence(
    monkeypatch,
):
    responses = _responses(
        queues_by_worker={
            "worker@node-1": [
                "ingest",
                "processing",
                "notifications",
                "maintenance",
            ]
        },
        capacity=1,
        active_count=1,
        reserved_count=1,
    )
    _install(monkeypatch, responses, canary_reason="fresh")
    saturated = worker_health.collect_worker_topology(_settings())
    assert saturated.reason == "saturated"
    assert [queue.key for queue in saturated.queues if queue.saturated] == ["ingest"]

    _install(monkeypatch, responses, canary_reason="stale")
    stalled = worker_health.collect_worker_topology(_settings())
    assert stalled.reason == "execution_stalled"
    assert stalled.stale_execution_queues == [
        "ingest",
        "maintenance",
        "notifications",
        "processing",
    ]

    _install(
        monkeypatch,
        responses,
        canary_reason="stale",
        canary_dispatch_reason="stale",
    )
    dispatch_unavailable = worker_health.collect_worker_topology(_settings())
    assert dispatch_unavailable.reason == "canary_dispatch_unavailable"
    assert dispatch_unavailable.canary_dispatch_ok is False
    assert dispatch_unavailable.canary_dispatch_reason == "stale"
    assert all(queue.status == "unknown" for queue in dispatch_unavailable.queues)

    responses = _responses(
        queues_by_worker={
            "worker@node-1": [
                "ingest",
                "processing",
                "notifications",
                "maintenance",
            ]
        },
        capacity=2,
        active_count=0,
    )
    _install(monkeypatch, responses, canary_reason="redis_unavailable")
    missing = worker_health.collect_worker_topology(_settings())
    assert missing.reason == "execution_evidence_missing"
    assert missing.missing_execution_evidence_queues == [
        "ingest",
        "maintenance",
        "notifications",
        "processing",
    ]


def test_worker_topology_does_not_claim_saturation_without_reserved_work(
    monkeypatch,
):
    responses = _responses(
        queues_by_worker={
            "worker@node-1": [
                "ingest",
                "processing",
                "notifications",
                "maintenance",
            ]
        },
        capacity=1,
        active_count=1,
        reserved_count=0,
    )
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    assert result.reason == "healthy"
    assert result.workers[0].saturated is False
    assert all(queue.saturated is False for queue in result.queues)


def test_worker_topology_cache_expires_after_collection_not_before(
    monkeypatch,
):
    responses = _responses(
        queues_by_worker={
            "worker@node-1": [
                "ingest",
                "processing",
                "notifications",
                "maintenance",
            ]
        }
    )
    _install(monkeypatch, responses)
    inspector = _Inspector(responses)
    inspect_calls = 0

    def inspect(*, timeout):
        nonlocal inspect_calls
        inspect_calls += 1
        return inspector

    monkeypatch.setattr(worker_health.celery_app.control, "inspect", inspect)
    monotonic_values = iter((100.0, 110.0, 111.0))
    monkeypatch.setattr(
        worker_health.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    first = worker_health.collect_worker_topology(_settings())
    second = worker_health.collect_worker_topology(_settings())

    assert inspect_calls == len(worker_health.PROBE_NAMES)
    assert second.generated_at == first.generated_at


def test_worker_topology_reports_truncated_inventory_as_partial(monkeypatch):
    queues = ["ingest", "processing", "notifications", "maintenance"]
    responses = _responses(
        queues_by_worker={
            f"worker@node-{index:02d}": queues
            for index in range(worker_health.MAX_WORKERS + 1)
        },
        active_count=0,
    )
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    assert result.status == "degraded"
    assert result.reason == "partial_inventory"
    assert result.responding_worker_count == worker_health.MAX_WORKERS
    assert result.observed_worker_count == worker_health.MAX_WORKERS + 1
    assert result.worker_inventory_truncated is True
    assert result.total_capacity is None
    assert result.active_count is None
    assert result.reserved_count is None
    assert result.scheduled_count is None
    assert len(result.workers) == worker_health.MAX_WORKERS
    assert all(probe.quality == "partial" for probe in result.probes)
    assert all(probe.responses_truncated is True for probe in result.probes)
    assert all(
        probe.observed_responder_count == worker_health.MAX_WORKERS + 1
        for probe in result.probes
    )


def test_worker_topology_bounds_malformed_worker_and_probe_values(monkeypatch):
    monkeypatch.setattr(worker_health, "MAX_COUNT", 3)
    queues = ["ingest", "processing", "notifications", "maintenance"]
    responses = _responses(queues_by_worker={"worker /unsafe": queues})
    responses["active_queues"]["worker /unsafe"].append("malformed")
    responses["active"]["worker /unsafe"] = [None] * 4
    responses["ping"][123] = {"ok": "pong"}
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    assert result.responding_worker_count == 1
    assert len(result.workers[0].name) <= 255
    assert result.workers[0].active_count == 3
    assert result.active_count is None
    assert all(queue.active_count is None for queue in result.queues)
    active_probe = next(
        probe for probe in result.probes if probe.probe == "active"
    )
    assert active_probe.quality == "invalid"
    assert active_probe.invalid_response_count == 1
    queue_probe = next(
        probe for probe in result.probes if probe.probe == "active_queues"
    )
    assert queue_probe.quality == "invalid"
    ping_probe = next(
        probe for probe in result.probes if probe.probe == "ping"
    )
    assert ping_probe.quality == "invalid"
    assert ping_probe.invalid_response_count == 1


def test_worker_topology_does_not_publish_partial_load_as_zero(monkeypatch):
    queues = ["ingest", "processing", "notifications", "maintenance"]
    responses = _responses(
        queues_by_worker={
            "worker@node-1": queues,
            "worker@node-2": queues,
        },
        active_count=1,
        reserved_count=1,
    )
    responses["active"].pop("worker@node-2")
    responses["reserved"].pop("worker@node-2")
    responses["scheduled"].pop("worker@node-2")
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    assert result.reason == "partial_inventory"
    assert result.active_count is None
    assert result.reserved_count is None
    assert result.scheduled_count is None
    assert result.total_capacity == 8
    assert result.workers[0].active_count == 1
    assert result.workers[1].active_count is None
    assert all(queue.active_count is None for queue in result.queues)
    load_probes = {
        probe.probe: probe for probe in result.probes if probe.probe in {
            "active",
            "reserved",
            "scheduled",
        }
    }
    assert {probe.quality for probe in load_probes.values()} == {"partial"}
    assert all(probe.missing_responder_count == 1 for probe in load_probes.values())


def test_worker_topology_marks_missing_stats_capacity_invalid(monkeypatch):
    responses = _responses(
        queues_by_worker={
            "worker@node-1": [
                "ingest",
                "processing",
                "notifications",
                "maintenance",
            ]
        }
    )
    responses["stats"]["worker@node-1"] = {}
    _install(monkeypatch, responses)

    result = worker_health.collect_worker_topology(_settings())

    stats_probe = next(
        probe for probe in result.probes if probe.probe == "stats"
    )
    assert result.status == "degraded"
    assert result.reason == "partial_inventory"
    assert result.total_capacity is None
    assert result.workers[0].capacity is None
    assert stats_probe.quality == "invalid"
    assert stats_probe.invalid_response_count == 1


def test_worker_probes_run_concurrently_with_a_bounded_timeout(monkeypatch):
    barrier = threading.Barrier(len(worker_health.PROBE_NAMES))
    observed_timeouts: list[float] = []
    observed_lock = threading.Lock()

    def probe(name, *, timeout):
        with observed_lock:
            observed_timeouts.append(timeout)
        barrier.wait(timeout=1)
        return worker_health._ProbeResult(name, "no_replies", {}, 0)

    monkeypatch.setattr(worker_health, "_call_probe", probe)
    started_at = time.perf_counter()

    results = worker_health._collect_probe_results(timeout=60)

    assert time.perf_counter() - started_at < 1.5
    assert list(results) == list(worker_health.PROBE_NAMES)
    assert observed_timeouts == [worker_health.MAX_WORKER_PROBE_TIMEOUT_SECONDS] * len(
        worker_health.PROBE_NAMES
    )
