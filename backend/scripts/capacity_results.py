"""Versioned measurement identities and conservative cross-release comparison."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

SCHEMA_VERSION = 2
MEASUREMENT_CONTRACT = "threatlens-capacity-v2"
REQUIRED_LATENCIES = {
    "export:succeeded": 20, "ai_connection:succeeded": 20, "governance": 20,
    "deadline:dns": 5, "deadline:headers": 5,
}


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def hardware_identity():
    cpu = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "unknown",
    )
    memory = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    group = next(
        (
            line.split(":", 2)[2]
            for line in Path("/proc/self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        ),
        "/",
    )
    current = Path("/sys/fs/cgroup") / group.lstrip("/")
    constraints = {"cpu.max": [], "memory.max": []}
    while current != Path("/sys/fs"):
        for name in constraints:
            path = current / name
            if path.is_file():
                value = path.read_text().strip()
                if value not in ("max", "max 100000"):
                    constraints[name].append(value)
        if current == Path("/sys/fs/cgroup"):
            break
        current = current.parent
    return {
        "cpu_model": cpu,
        "logical_cpus": os.cpu_count(),
        "memory_total_bytes": memory,
        "affinity": sorted(os.sched_getaffinity(0)),
        "cgroup_constraints": constraints,
    }


def seal_result(result, *, target_id, limits):
    identity = {
        "contract": MEASUREMENT_CONTRACT,
        "target_id": target_id,
        "hardware": hardware_identity(),
        "workload": result["workload"],
        "environment": result["environment"],
        "limits": limits,
        "budgets": result["budgets"],
        "python": result["python"],
        "platform": result["platform"],
    }
    result.update(
        schema_version=SCHEMA_VERSION,
        comparison_identity=identity,
        comparison_fingerprint=fingerprint(identity),
    )
    return result


def differences(left, right, prefix=""):
    if isinstance(left, dict) and isinstance(right, dict):
        return [
            change
            for key in sorted(set(left) | set(right))
            for change in differences(
                left.get(key), right.get(key), f"{prefix}.{key}".lstrip(".")
            )
        ]
    return (
        []
        if left == right
        else [{"field": prefix, "baseline": left, "candidate": right}]
    )


def compare_results(baseline, candidate, *, regression_percent=20):
    for run in (baseline, candidate):
        if run.get("source_dirty"):
            raise ValueError("commit measurement source before comparing releases")
        if run.get("schema_version") != SCHEMA_VERSION or not run.get(
            "comparison_identity"
        ):
            raise ValueError(
                "both runs must use the current measurement schema; legacy results are descriptive only"
            )
        if fingerprint(run["comparison_identity"]) != run.get("comparison_fingerprint"):
            raise ValueError("measurement identity fingerprint is invalid")
        if (
            run.get("budget_violations")
            or run.get("task_errors")
            or run.get("sampler", {}).get("errors")
            or run.get("status") == "failed"
        ):
            raise ValueError(
                "failed or incomplete runs cannot establish a capacity trend"
            )
    if (
        baseline["comparison_identity"].get("target_id") == "unlabeled"
        or candidate["comparison_identity"].get("target_id") == "unlabeled"
    ):
        raise ValueError(
            "label the measured host with --target-id before comparing releases"
        )
    incompatible = differences(
        baseline["comparison_identity"], candidate["comparison_identity"]
    )
    if incompatible:
        return {
            "compatible": False,
            "incompatibilities": incompatible,
            "metrics": [],
            "regressions": [],
        }
    metrics = []
    required = {} if baseline.get("profile") == "recovery" else REQUIRED_LATENCIES
    for name in sorted(set(baseline["latencies"]) | set(candidate["latencies"]) | set(required)):
        before, after = (
            baseline["latencies"].get(name),
            candidate["latencies"].get(name),
        )
        if (
            not before
            or not after
            or min(before["count"], after["count"])
            < (5 if name.startswith("deadline:") else 20)
        ):
            metrics.append(
                {
                    "name": name,
                    "status": "insufficient_samples",
                    "minimum_samples": 5 if name.startswith("deadline:") else 20,
                }
            )
            continue
        a, b = before["p95_ms"], after["p95_ms"]
        delta = (b / a - 1) * 100 if a > 0 else None
        metrics.append(
            {
                "name": name,
                "status": "compared",
                "baseline_p95_ms": a,
                "candidate_p95_ms": b,
                "change_percent": round(delta, 3) if delta is not None else None,
                "regressed": delta is not None and delta > regression_percent,
            }
        )
    for group, key in (
        ("memory", "process_rss_increase_bytes"),
        ("memory", "process_rss_peak_bytes"),
        ("watchdog", "owned_process_tree_rss_peak_bytes"),
        ("queue", "recovery_ms"),
        ("queue", "depth_peak"),
        ("queue", "oldest_pending_age_peak_ms"),
        ("faults", "worker_recovery_ms"),
        ("faults", "broker_restart_ms"),
        ("database", "sampled_lock_waiting_query_age_peak_ms"),
    ):
        a, b = baseline.get(group, {}).get(key), candidate.get(group, {}).get(key)
        if a is None or b is None:
            continue
        delta = (b / a - 1) * 100 if a > 0 else None
        metrics.append(
            {
                "name": f"{group}.{key}",
                "status": "single_run_observation",
                "baseline": a,
                "candidate": b,
                "absolute_change": b - a,
                "change_percent": round(delta, 3) if delta is not None else None,
                "regressed": delta is not None and delta > regression_percent,
            }
        )
    insufficient = [m["name"] for m in metrics
                    if m["name"] in required and m["status"] == "insufficient_samples"]
    return {
        "compatible": True,
        "conclusive": not insufficient and bool(required),
        "insufficient_required_samples": insufficient,
        "baseline_revision": baseline.get("git_revision"),
        "candidate_revision": candidate.get("git_revision"),
        "regression_threshold_percent": regression_percent,
        "metrics": metrics,
        "regressions": [m["name"] for m in metrics if m.get("regressed")],
        "outcomes": {
            "baseline": baseline.get("outcomes"),
            "candidate": candidate.get("outcomes"),
        },
    }
