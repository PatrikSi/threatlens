from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from app.tasks import system_health_tasks


def test_health_periodic_tasks_do_not_accumulate_result_backend_rows():
    assert system_health_tasks.record_queue_execution_canary.ignore_result is True
    assert system_health_tasks.collect_system_health_sample.ignore_result is True


def test_sampler_collects_one_coherent_topology_snapshot(monkeypatch):
    sampled_at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    settings = object()
    db = object()
    topology = object()
    overview = SimpleNamespace(generated_at=sampled_at)
    sample = SimpleNamespace(
        sampled_at=sampled_at,
        overall_status="degraded",
        worker_status="degraded",
        worker_reason="execution_stalled",
    )
    topology_calls = 0
    overview_calls = 0

    @contextmanager
    def session():
        yield db

    def collect_topology(received_settings, *, now):
        nonlocal topology_calls
        topology_calls += 1
        assert received_settings is settings
        assert now.tzinfo is not None
        return topology

    def collect_overview(received_db, *, now, worker_topology):
        nonlocal overview_calls
        overview_calls += 1
        assert received_db is db
        assert now.tzinfo is not None
        assert worker_topology is topology
        return overview

    def record(received_db, *, overview: object, worker_topology: object):
        assert received_db is db
        assert overview is not None
        assert worker_topology is topology
        return sample, True

    monkeypatch.setattr(system_health_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(system_health_tasks, "db_session", session)
    monkeypatch.setattr(
        system_health_tasks,
        "collect_worker_topology",
        collect_topology,
    )
    monkeypatch.setattr(
        system_health_tasks,
        "collect_operations_overview",
        collect_overview,
    )
    monkeypatch.setattr(
        system_health_tasks,
        "record_system_health_sample",
        record,
    )

    result = system_health_tasks.collect_system_health_sample.run()

    assert topology_calls == 1
    assert overview_calls == 1
    assert result == {
        "status": "ok",
        "created": True,
        "sampled_at": sampled_at.isoformat(),
        "overall_status": "degraded",
        "worker_status": "degraded",
        "worker_reason": "execution_stalled",
    }
