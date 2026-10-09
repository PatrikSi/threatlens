#!/usr/bin/env python3
"""Load the same opt-in observer around untouched capacity reference scripts."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

CONTRACT = "threatlens-capacity-diagnostics-v2"
OBSERVER_FILES = ("capacity_diagnostics.py", "capacity_diagnostics_plugin.py")
MAX_SAMPLES = 40_000
MAX_CONNECTIONS = 64
MAX_NS = 2**63 - 1
QUERY_CATEGORIES = {"other", "policy_update", "policy_exclusive", "policy_shared", "policy_read", "label_exclusive", "iam_shared"}
FENCE_CATEGORIES = {"policy_exclusive", "policy_shared", "label_exclusive", "iam_shared"}


def observer_identity(directory: Path) -> dict:
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
              for name in OBSERVER_FILES}
    encoded = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": 1, "contract": CONTRACT, "observer_file_sha256": hashes,
            "observer_sha256": hashlib.sha256(encoded).hexdigest()}


def read_record(path: Path, maximum_bytes: int) -> dict:
    if path.stat().st_size > maximum_bytes:
        raise ValueError("Diagnostic verification input exceeds its bound")
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError("Diagnostic verification requires JSON objects")
    return result


def bounded_integer(value, maximum: int, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= maximum


def numeric_age(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= sys.float_info.max and math.isfinite(value)


def verify_original_sampler(measured: dict, observed: dict, coverage: dict) -> None:
    samples = observed.get("original_sampler_samples")
    sampler, database = measured.get("sampler"), measured.get("database")
    captured = observed.get("sampler")
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise ValueError("Original sampler snapshots are missing or exceed their bound")
    if not isinstance(sampler, dict) or not isinstance(database, dict) or not isinstance(captured, dict):
        raise ValueError("Original sampler counters are missing")
    if set(captured) != {"observations", "gap_max_ns", "gap_mean_ns", "original_query_age_peak_ms",
                         "original_lock_wait_samples", "original_waiting_sessions_peak"}:
        raise ValueError("Original sampler summary fields are malformed")
    if sampler.get("errors") != [] or any(
        not bounded_integer(value, MAX_SAMPLES, 1) or value != len(samples)
        for value in (sampler.get("samples"), captured.get("observations"), coverage.get("original_sampler_samples"))
    ):
        raise ValueError("Original sampler sample counts or errors do not reconcile")
    if type(observed["dropped_events"].get("original_sampler_samples")) is not int or observed["dropped_events"]["original_sampler_samples"] != 0:
        raise ValueError("Original sampler snapshots lack a zero dropped counter")
    peaks, gaps, new_maxima = [], [], set()
    lock_count = waiting_peak = 0
    maximum, previous = 0.0, None
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or set(sample) != {"index", "sample_ns", "snapshot_elapsed_ns", "query_ages_ms"}:
            raise ValueError("Original sampler snapshot fields are malformed")
        if type(sample["index"]) is not int or sample["index"] != index or any(
            not bounded_integer(sample[name], MAX_NS) for name in ("sample_ns", "snapshot_elapsed_ns")
        ):
            raise ValueError("Original sampler snapshot index or timing is malformed")
        if previous is not None:
            if sample["sample_ns"] < previous:
                raise ValueError("Original sampler snapshot times are out of order")
            gaps.append(sample["sample_ns"] - previous)
        previous = sample["sample_ns"]
        ages = sample["query_ages_ms"]
        if not isinstance(ages, list) or len(ages) > MAX_CONNECTIONS or any(not numeric_age(age) for age in ages):
            raise ValueError("Original sampler query ages are malformed or exceed their bound")
        peak = max(ages, default=0.0)
        if peak > maximum:
            new_maxima.add(index)
        maximum = max(maximum, peak)
        peaks.append(peak)
        lock_count += len(ages)
        waiting_peak = max(waiting_peak, len(ages))
    for name, expected in (("gap_max_ns", max(gaps, default=0)),
                           ("gap_mean_ns", sum(gaps) // max(1, len(gaps)))):
        if not bounded_integer(captured[name], MAX_NS) or captured[name] != expected:
            raise ValueError("Original sampler gap counters do not reconcile")
    for name, captured_name, expected, bound in (
        ("lock_wait_samples", "original_lock_wait_samples", lock_count, MAX_SAMPLES * MAX_CONNECTIONS),
        ("waiting_sessions_peak", "original_waiting_sessions_peak", waiting_peak, MAX_CONNECTIONS),
    ):
        if any(not bounded_integer(value, bound) or value != expected
               for value in (database.get(name), captured[captured_name])):
            raise ValueError("Original sampler lock counters do not reconcile")
    peak = round(maximum, 3)
    if any(not numeric_age(value) or value != peak for value in (
        database.get("sampled_lock_waiting_query_age_peak_ms"), captured["original_query_age_peak_ms"],
    )):
        raise ValueError("Original sampler query-age peak does not reconcile")
    verify_lock_graphs(observed, samples, peaks, new_maxima)


def verify_lock_graphs(observed: dict, samples: list, peaks: list, new_maxima: set) -> None:
    alias_count = observed.get("connection_alias_count")
    graphs = observed["locks"]
    if not bounded_integer(alias_count, MAX_CONNECTIONS) or len(graphs) > MAX_SAMPLES:
        raise ValueError("Diagnostic lock graph exceeds its connection or sample bound")
    seen = set()
    for graph in graphs:
        if set(graph) != {"sample_ns", "observer_elapsed_ns", "observer_query_start_ns", "observer_query_end_ns",
                          "trigger", "original_sample_index", "original_sample_peak_ms", "connections"}:
            raise ValueError("Diagnostic lock graph fields are malformed")
        index = graph["original_sample_index"]
        if not bounded_integer(index, len(samples) - 1) or index in seen:
            raise ValueError("Diagnostic lock graph lacks unique original sample provenance")
        seen.add(index)
        if any(not bounded_integer(graph[name], MAX_NS) for name in (
            "sample_ns", "observer_elapsed_ns", "observer_query_start_ns", "observer_query_end_ns",
        )) or not graph["sample_ns"] == samples[index]["sample_ns"] <= graph["observer_query_start_ns"] <= graph["observer_query_end_ns"]:
            raise ValueError("Diagnostic lock graph timing does not match its original sample")
        if graph["observer_elapsed_ns"] < graph["observer_query_end_ns"] - graph["observer_query_start_ns"]:
            raise ValueError("Diagnostic lock graph duration excludes its query")
        if not numeric_age(graph["original_sample_peak_ms"]) or graph["original_sample_peak_ms"] != peaks[index]:
            raise ValueError("Diagnostic lock graph peak does not match its original sample")
        expected_trigger = "new_original_query_age_peak" if index in new_maxima else "periodic"
        if graph["trigger"] != expected_trigger:
            raise ValueError("Diagnostic lock graph trigger does not match its original sample")
        connections = graph["connections"]
        if not isinstance(connections, list) or len(connections) > MAX_CONNECTIONS:
            raise ValueError("Diagnostic lock graph connections are malformed or exceed their bound")
        seen_connections = set()
        for connection in connections:
            if not isinstance(connection, dict) or set(connection) != {
                "connection_id", "category", "wait_category", "blocker_ids", "is_waiter", "query_age_ms",
                "transaction_age_ms", "current_lock_wait_age_ms", "observed_fences", "transaction_end_requested",
            }:
                raise ValueError("Diagnostic lock connection fields are malformed")
            alias = connection["connection_id"]
            if not bounded_integer(alias, alias_count, 1) or alias in seen_connections:
                raise ValueError("Diagnostic lock connection lacks a unique bounded alias")
            seen_connections.add(alias)
            if type(connection["category"]) is not str or connection["category"] not in QUERY_CATEGORIES or type(connection["wait_category"]) is not str or connection["wait_category"] not in {"transactionid", "tuple", "relation", "advisory", "extend", "other"}:
                raise ValueError("Diagnostic lock connection categories are malformed")
            if any(type(connection[name]) is not bool for name in ("is_waiter", "transaction_end_requested")):
                raise ValueError("Diagnostic lock connection flags are malformed")
            blockers, fences = connection["blocker_ids"], connection["observed_fences"]
            if not isinstance(blockers, list) or len(blockers) > 16 or any(not bounded_integer(value, alias_count) for value in blockers):
                raise ValueError("Diagnostic lock blockers are malformed or exceed their bound")
            if not isinstance(fences, list) or len(fences) > len(FENCE_CATEGORIES) or any(type(value) is not str or value not in FENCE_CATEGORIES for value in fences) or len(set(fences)) != len(fences):
                raise ValueError("Diagnostic lock fence categories are malformed")
            if any(not numeric_age(connection[name]) for name in ("query_age_ms", "transaction_age_ms")) or (
                connection["current_lock_wait_age_ms"] is not None and not numeric_age(connection["current_lock_wait_age_ms"])
            ):
                raise ValueError("Diagnostic lock connection ages are malformed")
    if not new_maxima.issubset(seen):
        raise ValueError("Diagnostic lock context is missing for a new original maximum")


def verify_reference(*, observer: Path, identity: Path, measurement: Path,
                     diagnostic: Path, source_revision: str) -> None:
    expected = observer_identity(observer)
    shared = read_record(identity, 16_384)
    if type(shared.get("schema_version")) is not int or shared != expected:
        raise ValueError("The shared observer identity changed")
    measured = read_record(measurement, 1_048_576)
    observed = read_record(diagnostic, 33_554_432)
    if not isinstance(source_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", source_revision) or measured.get("git_revision") != source_revision:
        raise ValueError("Measurement does not match its frozen reference")
    if measured.get("status") != "passed":
        raise ValueError("Measurement did not pass")
    run_id = measured.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Measurement lacks an exact supervisor identity")
    if type(observed.get("schema_version")) is not int or observed["schema_version"] != 1:
        raise ValueError("Unsupported diagnostic schema")
    for key in ("contract", "observer_sha256", "observer_file_sha256"):
        if observed.get(key) != expected[key]:
            raise ValueError("The diagnostic observer identity does not match")
    if observed.get("capacity_run_id") != run_id or observed.get("application_source_revision") != source_revision:
        raise ValueError("Diagnostic source or supervisor identity does not match")
    if observed.get("status") != "passed" or observed.get("errors") != []:
        raise ValueError("Diagnostic capture did not pass")
    if type(observed.get("workload_exit_code")) is not int or observed["workload_exit_code"] != 0:
        raise ValueError("Original diagnostic workload did not pass")
    dropped = observed.get("dropped_events")
    if not isinstance(dropped, dict) or not dropped or any(type(value) is not int or value != 0 for value in dropped.values()):
        raise ValueError("Diagnostic capture dropped events or lacks its counters")
    coverage = observed.get("coverage")
    names = ("governance_operations", "operation_events", "query_events", "lock_samples", "host_samples", "lane_starts", "original_sampler_samples")
    if not isinstance(coverage, dict) or any(type(coverage.get(name)) is not int or coverage[name] < 0 for name in names):
        raise ValueError("Diagnostic capture lacks valid coverage counters")
    workload_completed = measured.get("workload_completed")
    governance = workload_completed.get("governance") if isinstance(workload_completed, dict) else None
    if type(governance) is not int or governance < 1 or coverage["governance_operations"] != governance:
        raise ValueError("Diagnostic governance capture is incomplete")
    if any(coverage[name] < 1 for name in ("operation_events", "query_events", "lock_samples", "host_samples")) or coverage["lane_starts"] < 5:
        raise ValueError("Diagnostic required capture is incomplete")
    for array, counter in (("operations", "operation_events"), ("lanes", "lane_starts"),
                           ("queries", "query_events"), ("locks", "lock_samples"), ("host", "host_samples")):
        values = observed.get(array)
        if not isinstance(values, list) or not values or any(not isinstance(value, dict) for value in values):
            raise ValueError("Diagnostic trace arrays are missing or malformed")
        if len(values) != coverage[counter]:
            raise ValueError("Diagnostic trace lengths do not match retained counters")
    if sum(row.get("lane") == "governance" for row in observed["operations"]) != governance:
        raise ValueError("Actual diagnostic governance capture is incomplete")
    lane_names = [row.get("lane") for row in observed["lanes"]]
    if any(not isinstance(name, str) for name in lane_names) or sorted(lane_names) != sorted(
        ("governance", "ai_connection", "export", "feed", "repair")
    ):
        raise ValueError("Diagnostic paced lanes are missing or duplicated")
    verify_original_sampler(measured, observed, coverage)


def run_supervisor(*, entrypoint: Path, observer: Path, output: Path,
                   arguments: list[str]) -> int:
    entrypoint, observer, output = entrypoint.resolve(), observer.resolve(), output.resolve()
    identity = observer_identity(observer)
    if observer.is_relative_to(entrypoint.parent.parent):
        raise ValueError("Observer must be external to the frozen backend tree")
    output.parent.mkdir(parents=True, exist_ok=True)
    previous_path, previous_argv = sys.path[:], sys.argv[:]
    sys.path.insert(0, str(entrypoint.parent))
    process = importlib.import_module("capacity_process")
    original = process.execute_bounded
    calls = 0

    def execute_observed(command, *args, **kwargs):
        nonlocal calls
        if command[:3] != [sys.executable, "-m", "pytest"] or not isinstance(kwargs.get("env"), dict):
            raise ValueError("Expected the unchanged capacity pytest invocation")
        calls += 1
        environment = dict(kwargs["env"])
        environment.update(
            PYTHONPATH=str(observer),
            THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT=str(output),
            THREATLENS_CAPACITY_DIAGNOSTICS_CONTRACT=CONTRACT,
            THREATLENS_CAPACITY_DIAGNOSTICS_OBSERVER_SHA256=identity["observer_sha256"],
        )
        return original([*command[:3], "-p", "capacity_diagnostics_plugin", *command[3:]],
                        *args, **{**kwargs, "env": environment})

    try:
        process.execute_bounded = execute_observed
        spec = importlib.util.spec_from_file_location("frozen_capacity_supervisor", entrypoint)
        supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervisor)
        sys.argv = [str(entrypoint), *arguments]
        code = supervisor.main()
        if not code and calls != 1:
            raise ValueError("Diagnostic observer did not reach exactly one workload")
        return code
    finally:
        process.execute_bounded = original
        sys.path[:], sys.argv[:] = previous_path, previous_argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--describe-observer", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--entrypoint", type=Path)
    parser.add_argument("--observer-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--measurement", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.describe_observer:
            args.output.write_text(json.dumps(observer_identity(args.observer_dir), indent=2, sort_keys=True) + "\n")
            return 0
        if args.verify:
            if args.identity is None or args.measurement is None or args.source_revision is None:
                parser.error("--verify requires --identity, --measurement and --source-revision")
            verify_reference(observer=args.observer_dir, identity=args.identity, measurement=args.measurement,
                             diagnostic=args.output, source_revision=args.source_revision)
            return 0
        arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
        return run_supervisor(entrypoint=args.entrypoint, observer=args.observer_dir,
                              output=args.output, arguments=arguments)
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        print(f"Capacity diagnostic operation failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
