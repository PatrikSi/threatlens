"""Missing telemetry must not imply a fully observed, healthy runtime."""

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import redis

from app.core import runtime_metrics
from app.services import operations_runtime as runtime


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
MEMORY_KEYS = (
    "sampled_process_memory_bytes", "container_memory_bytes", "container_memory_limit_bytes",
    "container_memory_percent", "container_oom_kills", "container_memory_pressure_avg10",
)


@pytest.fixture
def runtime_probes(monkeypatch):
    values = SimpleNamespace(
        memory={key: None for key in MEMORY_KEYS},
        counters={f"{event}_last_15m": 0 for event in runtime_metrics.RUNTIME_EVENTS},
        database={
            "database_connections": 1, "database_lock_waiters": 0,
            "database_oldest_lock_wait_seconds": 0, "database_oldest_transaction_seconds": 0,
        },
    )
    monkeypatch.setattr(runtime, "collect_memory_pressure", lambda: values.memory.copy())
    monkeypatch.setattr(runtime, "collect_runtime_events", lambda **_kwargs: values.counters.copy())
    monkeypatch.setattr(runtime, "_database_pressure", lambda _db: values.database.copy())
    return values


def _capacity(*, database_ok=True):
    issues = []
    result = runtime.collect_runtime_capacity(
        SimpleNamespace(begin_nested=nullcontext), checked_at=NOW,
        database_ok=database_ok, issues=issues,
    )
    return result, issues


@pytest.mark.parametrize("memory", [
    {}, {key: None for key in MEMORY_KEYS},
    {"container_memory_limit_bytes": 100, "container_oom_kills": 0, "container_memory_pressure_avg10": 0},
])
def test_runtime_without_memory_usage_is_unknown(runtime_probes, memory):
    runtime_probes.memory = memory
    result, issues = _capacity()
    assert result.status == "unknown"
    assert "Memory: usage unavailable; capacity unknown." in result.summary
    assert issues == []


@pytest.mark.parametrize(("memory", "summary"), [
    ({"sampled_process_memory_bytes": 0}, "process RSS only"),
    ({"container_memory_bytes": 0}, "ceiling unavailable or unlimited; utilization unknown"),
    ({"container_memory_bytes": 20, "container_memory_limit_bytes": 100, "container_memory_percent": 20}, "container usage and ceiling"),
])
def test_runtime_identifies_usable_memory_modes(runtime_probes, memory, summary):
    runtime_probes.memory.update(memory)
    result, issues = _capacity()
    assert result.status == "healthy" and issues == []
    assert summary in result.summary


@pytest.mark.parametrize("missing", ["counter", "database_probe", "database"])
def test_runtime_with_memory_stays_unknown_when_other_telemetry_is_missing(runtime_probes, missing):
    runtime_probes.memory["sampled_process_memory_bytes"] = 10
    if missing == "counter":
        runtime_probes.counters["database_deadline_last_15m"] = None
    elif missing == "database_probe":
        runtime_probes.database = {}
    result, issues = _capacity(database_ok=missing != "database")
    assert result.status == "unknown" and issues == []


@pytest.mark.parametrize("signal", ["memory", "lock_wait", "deadline"])
def test_observed_runtime_pressure_degrades_even_with_missing_telemetry(runtime_probes, signal):
    if signal == "memory":
        runtime_probes.memory.update(container_memory_bytes=85, container_memory_percent=85)
        runtime_probes.counters["database_deadline_last_15m"] = None
    elif signal == "lock_wait":
        runtime_probes.database["database_oldest_lock_wait_seconds"] = 5
    else:
        runtime_probes.counters["database_deadline_last_15m"] = 1
    result, issues = _capacity()
    assert result.status == "degraded"
    assert [issue.code for issue in issues] == ["runtime_capacity_pressure"]


def test_missing_memory_files_return_unknown_usage(tmp_path):
    result = runtime.collect_memory_pressure(cgroup=tmp_path, process=tmp_path / "missing-statm")
    assert result == {key: None for key in MEMORY_KEYS}


def test_unlimited_container_memory_retains_usage_without_inventing_utilization(tmp_path):
    (tmp_path / "memory.current").write_text("100")
    (tmp_path / "memory.max").write_text("max")
    result = runtime.collect_memory_pressure(cgroup=tmp_path, process=tmp_path / "missing-statm")
    assert result["container_memory_bytes"] == 100
    assert result["container_memory_limit_bytes"] is None
    assert result["container_memory_percent"] is None


def test_failed_runtime_counter_read_returns_unknown(monkeypatch):
    def unavailable(_url):
        raise redis.ConnectionError("Synthetic telemetry unavailable")

    monkeypatch.setattr(runtime_metrics, "_client", unavailable)
    assert runtime_metrics.collect_runtime_events(now=NOW.timestamp()) == {
        f"{event}_last_15m": None for event in runtime_metrics.RUNTIME_EVENTS
    }
