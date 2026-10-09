#!/usr/bin/env python3
"""Load the same opt-in observer around untouched capacity reference scripts."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sys

CONTRACT = "threatlens-capacity-diagnostics-v1"
OBSERVER_FILES = ("capacity_diagnostics.py", "capacity_diagnostics_plugin.py")


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
    names = ("governance_operations", "operation_events", "query_events", "lock_samples", "host_samples", "lane_starts")
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
