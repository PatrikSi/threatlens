#!/usr/bin/env python3
"""Keep reference workloads unchanged; independently verify their owned cleanup."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

from qualification_runtime import require_local_docker

RUN_ID = re.compile(r"[0-9a-f]{32}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
DISCOVERY_SECONDS = 3.0
ATTESTATION_SECONDS = 10.0


def attest_cleanup(artifact: Path, *, environment: dict[str, str]) -> dict:
    result = {"schema_version": 1, "kind": "independent_capacity_cleanup",
              "status": "failed", "probe_count": 0, "errors": []}
    try:
        if not environment.get("DOCKER_HOST", "").startswith("unix://") or environment.get("DOCKER_CONTEXT"):
            raise ValueError("Cleanup requires the established local Unix endpoint")
        if artifact.stat().st_size > 1_048_576:
            raise ValueError("Capacity artifact exceeds the attestation input bound")
        measurement = json.loads(artifact.read_text())
        run_id = measurement.get("run_id") if isinstance(measurement, dict) else None
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError("Capacity artifact lacks its exact supervisor run identity")
        result.update(run_id=run_id, docker_endpoint="established_local_unix")
    except (OSError, UnicodeError, ValueError) as error:
        result["errors"].append({"stage": "identity", "error_type": type(error).__name__})
        return result

    began = time.monotonic()
    deadline = began + ATTESTATION_SECONDS
    # Even a successful legacy supervisor can miss a daemon creation accepted
    # before its Docker client exits. Observe only this exact label, never remove.
    discover_until = began + DISCOVERY_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result["errors"].append({"stage": "discovery", "error_type": "TimeoutError"})
            return result
        try:
            discovered = subprocess.run([
                "docker", "ps", "-aq", "--no-trunc", "--filter",
                f"label=threatlens.capacity.run_id={run_id}",
            ], capture_output=True, text=True, timeout=min(2.0, remaining),
                check=False, env=environment)
            result["probe_count"] += 1
            if discovered.returncode or not isinstance(discovered.stdout, str):
                raise ValueError("Docker cleanup discovery is unavailable")
            identities = discovered.stdout.splitlines()
            if any(not CONTAINER_ID.fullmatch(value) for value in identities):
                raise ValueError("Docker returned an invalid container identity")
            result["remaining_container_count"] = len(identities)
            if identities:
                result["errors"].append({"stage": "discovery", "error_type": "OwnedContainersRemain"})
                return result
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            result["errors"].append({"stage": "discovery", "error_type": type(error).__name__})
            return result
        delay = min(discover_until, deadline) - time.monotonic()
        if delay <= 0:
            result["status"] = "passed"
            return result
        time.sleep(min(0.2, delay))


def run_reference(command: list[str], *, artifact: Path, log: Path,
                  attestation: Path, environment: dict[str, str]) -> int:
    with log.open("w") as output:
        try:
            workload_exit = subprocess.run(command, env=environment, stdout=output,
                                           stderr=subprocess.STDOUT, check=False).returncode
        except OSError:
            workload_exit = 126
    cleanup = attest_cleanup(artifact, environment=environment)
    attestation.write_text(json.dumps(cleanup, indent=2, sort_keys=True) + "\n")
    # Cleanup evidence never replaces the workload's genuine failure code.
    return workload_exit if workload_exit else 0 if cleanup["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-docker-host", action="store_true")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    docker_environment = require_local_docker()
    if args.print_docker_host:
        print(docker_environment["DOCKER_HOST"])
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or any(path is None for path in (args.artifact, args.log, args.attestation)):
        parser.error("--artifact, --log, --attestation and a measurement command are required")
    environment = dict(os.environ)
    environment.update(docker_environment)
    environment.pop("DOCKER_CONTEXT", None)
    environment.pop("THREATLENS_TEST_DATABASE_URL", None)
    environment.pop("THREATLENS_TEST_REDIS_URL", None)
    return run_reference(command, artifact=args.artifact, log=args.log,
                         attestation=args.attestation, environment=environment)


if __name__ == "__main__":
    raise SystemExit(main())
