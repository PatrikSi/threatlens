"""Fixed-cardinality pressure and timeout indicators for the collecting process."""

from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.runtime_metrics import RUNTIME_EVENTS, collect_runtime_events
from app.schemas.operations import OperationsComponentCheck, OperationsIssue
from app.services.operations_common import issue, safe_db_probe


RUNTIME_METRIC_KEYS = frozenset({
    "database_connections", "database_lock_waiters", "database_oldest_lock_wait_seconds",
    "database_oldest_transaction_seconds", "sampled_process_memory_bytes",
    "container_memory_bytes", "container_memory_limit_bytes", "container_memory_percent",
    "container_oom_kills", "container_memory_pressure_avg10",
    *(f"{event}_last_15m" for event in RUNTIME_EVENTS),
})


def safe_runtime_metrics(raw: object) -> dict[str, int | float | None]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if key in RUNTIME_METRIC_KEYS
        and (value is None or (
            not isinstance(value, bool) and isinstance(value, (int, float))
            and 0 <= value <= 1e15 and math.isfinite(value)
        ))
    }


def collect_memory_pressure(
    *, cgroup: Path = Path("/sys/fs/cgroup"), process: Path = Path("/proc/self/statm")
) -> dict[str, int | float | None]:
    result: dict[str, int | float | None] = {
        "sampled_process_memory_bytes": None, "container_memory_bytes": None,
        "container_memory_limit_bytes": None, "container_memory_percent": None,
        "container_oom_kills": None, "container_memory_pressure_avg10": None,
    }
    try:
        result["sampled_process_memory_bytes"] = int(process.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    for key, filename in (
        ("container_memory_bytes", "memory.current"),
        ("container_memory_limit_bytes", "memory.max"),
    ):
        try:
            result[key] = max(0, int((cgroup / filename).read_text().strip()))
        except (OSError, ValueError):
            pass
    used, limit = result["container_memory_bytes"], result["container_memory_limit_bytes"]
    if used is not None and limit:
        result["container_memory_percent"] = min(100.0, round(used / limit * 100, 1))
    try:
        events = dict(line.split() for line in (cgroup / "memory.events").read_text().splitlines())
        result["container_oom_kills"] = max(0, int(events["oom_kill"]))
        pressure = (cgroup / "memory.pressure").read_text().splitlines()[0].split()[1:]
        result["container_memory_pressure_avg10"] = float(dict(value.split("=") for value in pressure)["avg10"])
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return safe_runtime_metrics(result)


def _database_pressure(db: Session) -> dict[str, int | float | None]:
    row = db.execute(text("""
        SELECT count(*) AS database_connections,
          count(*) FILTER (WHERE wait_event_type = 'Lock') AS database_lock_waiters,
          coalesce(max(extract(epoch FROM clock_timestamp() - query_start))
            FILTER (WHERE wait_event_type = 'Lock'), 0) AS database_oldest_lock_wait_seconds,
          coalesce(max(extract(epoch FROM clock_timestamp() - xact_start)), 0)
            AS database_oldest_transaction_seconds
        FROM pg_stat_activity WHERE datname = current_database() AND usename = current_user
    """)).mappings().one()
    return {key: max(0, int(value)) for key, value in row.items()}


def collect_runtime_capacity(
    db: Session, *, checked_at: datetime, database_ok: bool, issues: list[OperationsIssue]
) -> OperationsComponentCheck:
    metrics = collect_memory_pressure()
    metrics.update(collect_runtime_events(now=checked_at.timestamp()))
    database = safe_db_probe(db, "runtime_database_pressure", lambda: _database_pressure(db), {}) if database_ok else {}
    for key in ("database_connections", "database_lock_waiters", "database_oldest_lock_wait_seconds", "database_oldest_transaction_seconds"):
        metrics[key] = database.get(key)
    pressured = (metrics.get("container_memory_percent") or 0) >= 85
    waiting = (metrics.get("database_oldest_lock_wait_seconds") or 0) >= 5
    deadlines = sum(int(metrics.get(f"{event}_last_15m") or 0) for event in RUNTIME_EVENTS)
    if pressured or waiting or deadlines:
        issues.append(issue(
            "runtime_capacity_pressure", "warning", "runtime_capacity",
            "Memory pressure, sustained database waits, or recent deadline failures need attention.",
            "Requests or background work may be delayed or retried.",
            "Inspect worker queues and container limits; use the processing worklist for failed items and compare release capacity measurements.",
        ))
    known = database_ok and bool(database) and all(metrics.get(f"{event}_last_15m") is not None for event in RUNTIME_EVENTS)
    return OperationsComponentCheck(
        key="runtime_capacity", label="Runtime capacity", checked_at=checked_at,
        status="degraded" if pressured or waiting or deadlines else "healthy" if known else "unknown",
        summary="Database: current runtime role. Memory: collecting container/process. Deadline counters: shared, best effort, latest 15 minute buckets.",
        metrics=metrics,
    )
