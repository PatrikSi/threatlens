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
        "latencies": {name: {"count": 30, "p95_ms": 100} for name in (
            "export:succeeded", "ai_connection:succeeded", "governance", "deadline:dns", "deadline:headers")},
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
    assert result["conclusive"]
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


def test_shared_epoch_arrival_contract_requires_a_matching_baseline():
    from tests.capacity.workload_support import PROFILES

    previous, current = run(), run()
    workload = deepcopy(PROFILES["sustained"])
    assert workload["arrival_contract"] == "five-lanes-shared-monotonic-epoch-v2"
    assert workload["start_gate_participants"] == 5
    assert workload["start_gate_timeout_seconds"] == 30
    current["comparison_identity"]["workload"].update(workload)
    previous["comparison_identity"]["workload"].update({
        key: value for key, value in workload.items()
        if key not in {"arrival_contract", "start_gate_participants", "start_gate_timeout_seconds"}
    })
    for record in [previous, current]:
        record["comparison_fingerprint"] = fingerprint(record["comparison_identity"])
    result = compare_results(previous, current)
    assert not result["compatible"]
    assert result["metrics"] == []
    matched = deepcopy(current)
    matched["git_revision"] = "release-b"
    assert compare_results(current, matched)["compatible"]


@pytest.mark.parametrize(
    "mutation", ["schema", "fingerprint", "failed", "budget", "sampler", "dirty", "cleanup"]
)
def test_reject_invalid_or_failed_results(mutation):
    a = run()
    if mutation == "schema":
        a["schema_version"] = 1
    elif mutation == "fingerprint":
        a["comparison_identity"]["target_id"] = "tampered"
    elif mutation == "failed":
        a["status"] = "failed"
    elif mutation == "dirty":
        a["source_dirty"] = True
    elif mutation == "budget":
        a["budget_violations"] = {"memory": 1}
    elif mutation == "cleanup":
        a["cleanup"] = {"status": "failed", "remaining_container_ids": ["owned"]}
    else:
        a["sampler"] = {"errors": ["ConnectionError"]}
    with pytest.raises(ValueError):
        compare_results(a, run())


@pytest.mark.parametrize("cleanup", [None, [], "passed", True, 1])
@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_malformed_cleanup_evidence_is_a_contract_error(cleanup, side):
    baseline, candidate = run(), run()
    (baseline if side == "baseline" else candidate)["cleanup"] = cleanup
    with pytest.raises(ValueError, match="cleanup evidence must be an object"):
        compare_results(baseline, candidate)


def test_small_sample_and_zero_baseline_are_not_fabricated_percentages():
    a, b = run(), run()
    a["latencies"]["export:succeeded"]["count"] = 4
    a["memory"]["process_rss_increase_bytes"] = 0
    result = compare_results(a, b)
    metrics = {m["name"]: m for m in result["metrics"]}
    assert metrics["export:succeeded"]["status"] == "insufficient_samples"
    assert metrics["memory.process_rss_increase_bytes"]["change_percent"] is None
    assert metrics["memory.process_rss_increase_bytes"]["absolute_change"] == 100
    assert not result["conclusive"]
    assert result["insufficient_required_samples"] == ["export:succeeded"]


def test_missing_success_groups_cannot_pass_as_faster_safe_rejections():
    a, b = run(), run()
    del b["latencies"]["export:succeeded"]
    b["latencies"]["export:policy_conflict"] = {"count": 30, "p95_ms": 1}
    result = compare_results(a, b)
    assert not result["conclusive"]
    assert "export:succeeded" in result["insufficient_required_samples"]


def test_tracks_absolute_process_and_owned_tree_peaks():
    a, b = run(), run()
    a["memory"]["process_rss_peak_bytes"] = 1000
    b["memory"]["process_rss_peak_bytes"] = 1500
    a["watchdog"] = {"owned_process_tree_rss_peak_bytes": 2000}
    b["watchdog"] = {"owned_process_tree_rss_peak_bytes": 3000}
    result = compare_results(a, b)
    assert result["regressions"] == ["memory.process_rss_peak_bytes", "watchdog.owned_process_tree_rss_peak_bytes"]
