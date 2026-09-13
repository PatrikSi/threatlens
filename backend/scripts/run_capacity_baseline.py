#!/usr/bin/env python3
"""Run the isolated opt-in workload in a fresh pytest process."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import uuid
import json
from pathlib import Path

from capacity_process import execute_bounded


def main() -> int:
    # External CI/timeout termination must unwind the supervisor's finally
    # block so its owned processes and fixture containers are removed.
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("smoke", "baseline", "large", "sustained", "recovery"),
        default="smoke",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--target-id", default="unlabeled")
    parser.add_argument("--cpu-count", type=int, default=1)
    parser.add_argument("--max-rss-mib", type=int, default=1024)
    args = parser.parse_args()
    if args.profile == "sustained" and (
        args.duration_seconds is None or not 10 <= args.duration_seconds <= 3600
    ):
        parser.error("sustained requires --duration-seconds between 10 and 3600")
    if args.profile != "sustained" and args.duration_seconds is not None:
        parser.error("duration applies only to sustained")
    if (
        not 1 <= args.cpu_count <= len(os.sched_getaffinity(0))
        or not 256 <= args.max_rss_mib <= 4096
    ):
        parser.error("invalid CPU count or RSS limit (256..4096 MiB)")
    limits = {
        "cpu_count": args.cpu_count,
        "nice": 10,
        "max_rss_bytes": args.max_rss_mib * 1024 * 1024,
        "wall_timeout_seconds": (args.duration_seconds or 0) + 240,
        "container_cpus": 0.5,
        "postgres_memory_mib": 512,
        "redis_memory_mib": 128,
    }
    # Always ask the fixture to create disposable services. An inherited URL is
    # not sufficient evidence that a database or Redis belongs to this run.
    forbidden = ("THREATLENS_TEST_DATABASE_URL", "THREATLENS_TEST_REDIS_URL")
    if any(os.environ.get(name) for name in forbidden):
        parser.error(
            "unset THREATLENS_TEST_DATABASE_URL and THREATLENS_TEST_REDIS_URL; this harness creates disposable Docker services"
        )
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "TZ",
        "XDG_RUNTIME_DIR",
        "THREATLENS_TEST_POSTGRES_IMAGE",
        "THREATLENS_TEST_REDIS_IMAGE",
    }
    env = {
        name: value
        for name, value in os.environ.items()
        if name in allowed or name.startswith(("DOCKER_", "LC_"))
    }
    run_id = uuid.uuid4().hex
    output = args.output.resolve()
    backend = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=backend, text=True
    ).strip()
    source_dirty = (
        subprocess.run(
            [
                "git",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                "app",
                "tests/capacity",
                "scripts",
            ],
            cwd=backend,
        ).returncode
        != 0
    )
    source_dirty = source_dirty or bool(
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                "app",
                "tests/capacity",
                "scripts",
            ],
            cwd=backend,
            text=True,
        ).strip()
    )
    env.update(
        THREATLENS_CAPACITY_PROFILE=args.profile,
        THREATLENS_CAPACITY_OUTPUT=str(output),
        THREATLENS_CAPACITY_RUN_ID=run_id,
        THREATLENS_CAPACITY_SOURCE_REVISION=revision,
        THREATLENS_CAPACITY_SOURCE_DIRTY="true" if source_dirty else "false",
        THREATLENS_CAPACITY_TARGET_ID=args.target_id,
        THREATLENS_CAPACITY_LIMITS=json.dumps(limits),
        THREATLENS_CAPACITY_DURATION=str(args.duration_seconds or 0),
    )
    backend = Path(__file__).resolve().parents[1]
    # Settings load .env from cwd. A fresh working directory and a small process
    # environment keep local deployment configuration out of the test process.
    with tempfile.TemporaryDirectory(
        prefix="threatlens-capacity-"
    ) as working_directory:
        manifest = Path(working_directory) / "owned-containers.txt"
        env["THREATLENS_CAPACITY_CONTAINER_MANIFEST"] = str(manifest)
        code = execute_bounded(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(backend / "pytest.ini"),
                str(
                    backend
                    / (
                        "tests/capacity/test_recovery_workload.py"
                        if args.profile == "recovery"
                        else "tests/capacity/test_concurrent_workload.py"
                    )
                ),
                "-q",
                "-s",
            ],
            cwd=working_directory,
            env=env,
            limits=limits,
            output=output,
            manifest=manifest,
            run_id=run_id,
        )
    if code == 0 and (
        not output.exists() or json.loads(output.read_text()).get("run_id") != run_id
    ):
        parser.exit(
            1,
            "capacity workload produced no current measurements; a skipped test is not a baseline\n",
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
