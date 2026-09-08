from copy import deepcopy

import pytest

from scripts.capacity_results import compare_results, fingerprint


def run():
    identity = {
        "target_id": "fixture-host",
        "workload": {"duration_seconds": 600},
        "hardware": {"cpus": 1},
    }
    return {
        "schema_version": 2,
        "comparison_identity": identity,
        "comparison_fingerprint": fingerprint(identity),
        "git_revision": "release-a",
        "budget_violations": {},
        "latencies": {"export:succeeded": {"count": 30, "p95_ms": 100}},
        "memory": {"process_rss_increase_bytes": 100},
        "queue": {"recovery_ms": 100},
        "database": {},
        "outcomes": {},
    }


def test_compare_cross_release_regression_and_success_counts():
    a = run()
    b = deepcopy(a)
    b["git_revision"] = "release-b"
    b["latencies"]["export:succeeded"]["p95_ms"] = 140
    result = compare_results(a, b)
    assert result["compatible"]
    assert result["regressions"] == ["export:succeeded"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_id", "different-host"),
        ("workload", {"duration_seconds": 60}),
        ("hardware", {"cpus": 4}),
    ],
)
def test_incompatible_hardware_workload_or_target_is_not_compared(field, value):
    a, b = run(), run()
    b["comparison_identity"][field] = value
    b["comparison_fingerprint"] = fingerprint(b["comparison_identity"])
    result = compare_results(a, b)
    assert not result["compatible"]
    assert result["metrics"] == []


@pytest.mark.parametrize(
    "mutation", ["schema", "fingerprint", "failed", "budget", "sampler"]
)
def test_reject_invalid_or_failed_results(mutation):
    a = run()
    if mutation == "schema":
        a["schema_version"] = 1
    elif mutation == "fingerprint":
        a["comparison_identity"]["target_id"] = "tampered"
    elif mutation == "failed":
        a["status"] = "failed"
    elif mutation == "budget":
        a["budget_violations"] = {"memory": 1}
    else:
        a["sampler"] = {"errors": ["ConnectionError"]}
    with pytest.raises(ValueError):
        compare_results(a, run())


def test_small_sample_and_zero_baseline_are_not_fabricated_percentages():
    a, b = run(), run()
    a["latencies"]["export:succeeded"]["count"] = 4
    a["memory"]["process_rss_increase_bytes"] = 0
    result = compare_results(a, b)
    assert result["metrics"][0]["status"] == "insufficient_samples"
    assert result["metrics"][1]["change_percent"] is None
