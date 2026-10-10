#!/usr/bin/env python3
"""Run the existing vendor contract in an owned, local OpenSearch 3.8.0 container."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from monitor import atomic_json

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "opensearchproject/opensearch:3.8.0"
LABEL = "threatlens.qualification.run_id"
LIMITS = {"cpus": 2, "memory_bytes": 2 * 1024**3, "jvm_heap_bytes": 512 * 1024**2, "pids": 512}
READINESS_SECONDS = 150
CONTRACT_SECONDS = 120
CLEANUP_SECONDS = 30
SOURCES = (
    "scripts/operations/run_opensearch_qualification.py",
    "scripts/operations/qualify_opensearch.py",
    "examples/automation-receiver/opensearch_connector.py",
)
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")


def clean_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "TZ", "XDG_RUNTIME_DIR", "DOCKER_HOST", "DOCKER_CONTEXT"}
    return {name: value for name, value in os.environ.items()
            if name in allowed or name.startswith("LC_")} | {"PYTHONDONTWRITEBYTECODE": "1"}


def command(arguments: list[str], *, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, check=True,
                          timeout=timeout, env=clean_environment(), cwd=ROOT)


def cleanup_owned(run_id: str, container_id: str | None, *, ambiguous: bool) -> list[str]:
    errors = []
    targets = {container_id} if container_id else set()
    started = time.monotonic()
    deadline = started + CLEANUP_SECONDS
    discover_until = started + (3 if ambiguous else 0)
    while True:
        try:
            found = command(["docker", "ps", "-aq", "--no-trunc", "--filter", f"label={LABEL}={run_id}"],
                            timeout=min(5, max(0.1, deadline - time.monotonic())))
            targets.update(found.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            errors.append("container_discovery_failed")
        for target in sorted(targets):
            if not CONTAINER_ID.fullmatch(target):
                errors.append("invalid_container_identity")
                continue
            try:
                actual = command(["docker", "inspect", "--format", f'{{{{index .Config.Labels "{LABEL}"}}}}', target],
                                 timeout=min(5, max(0.1, deadline - time.monotonic()))).stdout.strip()
                if actual != run_id:
                    errors.append("ownership_label_mismatch")
                    continue
                command(["docker", "rm", "--force", "--volumes", target],
                        timeout=min(10, max(0.1, deadline - time.monotonic())))
                targets.discard(target)
            except (OSError, subprocess.SubprocessError):
                errors.append("owned_container_removal_failed")
        if time.monotonic() >= min(deadline, discover_until):
            break
        time.sleep(max(0, min(0.2, discover_until - time.monotonic())))
    # A successful later retry cannot erase an unresolved ownership/removal error.
    return sorted(set(errors))


def run_qualification(output: Path) -> dict:
    run_id = uuid.uuid4().hex
    result = {"schema_version": 1, "kind": "opensearch_vendor_qualification", "status": "failed",
              "scope": "disposable_loopback_vendor_contract", "production_qualified": False,
              "run_id": run_id, "owned_label": f"{LABEL}={run_id}", "limits": LIMITS,
              "started_at": datetime.now(timezone.utc).isoformat(), "contract_status": "not_started"}
    container_id = None
    creation_attempted = False
    ambiguous = False
    stage = "prerequisites"
    try:
        context = os.environ.get("DOCKER_CONTEXT")
        host = command(["docker", "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"]
                       ).stdout.strip() if context else os.environ.get("DOCKER_HOST") or command(
                           ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
                       ).stdout.strip()
        if not host.startswith("unix://"):
            raise ValueError("Qualification requires a local Docker Unix socket")
        result["source_revision"] = command(["git", "rev-parse", "HEAD"]).stdout.strip()
        result["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
        result["source_dirty"] = bool(
            subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *SOURCES],
                           cwd=ROOT, timeout=15, env=clean_environment()).returncode
            or command(["git", "ls-files", "--others", "--exclude-standard", "--", *SOURCES]).stdout.strip()
        )
        image_id = command(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]).stdout.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("The pinned local image identity is invalid")
        result["vendor_image_id"] = image_id
        stage = "container_start"
        creation_attempted = ambiguous = True
        created = command([
            "docker", "run", "--detach", "--pull", "never", "--name", f"threatlens-opensearch-qualification-{run_id}",
            "--label", f"{LABEL}={run_id}", "--memory", "2g", "--memory-swap", "2g", "--cpus", "2",
            "--pids-limit", "512", "--ulimit", "nofile=65536:65536", "--publish", "127.0.0.1::9200",
            "--env", "discovery.type=single-node", "--env", "node.store.allow_mmap=false",
            "--env", "DISABLE_SECURITY_PLUGIN=true", "--env", "DISABLE_INSTALL_DEMO_CONFIG=true",
            "--env", "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m", image_id,
        ], timeout=30).stdout.strip()
        if not CONTAINER_ID.fullmatch(created):
            raise ValueError("Docker did not return the created container identity")
        container_id, ambiguous = created, False
        result["container_id"] = container_id
        address = command(["docker", "port", container_id, "9200/tcp"]).stdout.strip()
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
            raise ValueError("The owned OpenSearch port is not bound exclusively to loopback")
        stage = "readiness"
        deadline = time.monotonic() + READINESS_SECONDS
        while True:
            try:
                command(["docker", "exec", container_id, "curl", "--fail", "--silent", "--max-time", "2",
                         "http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow"],
                        timeout=min(5, max(0.1, deadline - time.monotonic())))
                break
            except (OSError, subprocess.SubprocessError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned OpenSearch readiness budget exhausted") from None
                time.sleep(max(0, min(1, deadline - time.monotonic())))
        stage = "contract"
        result["contract_status"] = "failed"
        with tempfile.TemporaryDirectory(prefix="threatlens-opensearch-contract-") as directory:
            contract_output = Path(directory) / "contract.json"
            command([sys.executable, str(ROOT / "scripts/operations/qualify_opensearch.py"),
                     "--url", f"http://{address}", "--output", str(contract_output)], timeout=CONTRACT_SECONDS)
            contract = json.loads(contract_output.read_text())
            if not isinstance(contract, dict) or contract.get("opensearch_version") != "3.8.0":
                raise ValueError("The vendor contract did not run against OpenSearch 3.8.0")
            result["contract"] = contract
        result["contract_status"] = "passed"
    except Exception as error:
        result["failure"] = {"stage": stage, "category": type(error).__name__}
    finally:
        if container_id:
            try:
                logs = command(["docker", "logs", "--tail", "500", container_id], timeout=5)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.with_suffix(".opensearch.log").write_text((logs.stdout + logs.stderr)[-131_072:])
            except Exception as error:
                result["diagnostic_error"] = type(error).__name__
        try:
            cleanup_errors = cleanup_owned(run_id, container_id, ambiguous=ambiguous) if creation_attempted else []
        except Exception as error:
            cleanup_errors = [f"cleanup_failed:{type(error).__name__}"]
        result["cleanup"] = {"completed": not cleanup_errors, "errors": cleanup_errors}
        result["status"] = "passed" if result["contract_status"] == "passed" and not cleanup_errors else "failed"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_qualification(args.output)
    print(f"OpenSearch qualification: {result['status']}; evidence: {args.output}")
    if not result["cleanup"]["completed"]:
        print(f"Inspect only owned scope {result['owned_label']} before retrying.", file=sys.stderr)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
