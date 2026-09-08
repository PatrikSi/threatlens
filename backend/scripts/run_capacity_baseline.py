#!/usr/bin/env python3
"""Run the isolated opt-in workload in a fresh pytest process."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import uuid
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=("smoke", "baseline", "large"), default="smoke"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
    env.update(
        THREATLENS_CAPACITY_PROFILE=args.profile,
        THREATLENS_CAPACITY_OUTPUT=str(output),
        THREATLENS_CAPACITY_RUN_ID=run_id,
    )
    backend = Path(__file__).resolve().parents[1]
    # Settings load .env from cwd. A fresh working directory and a small process
    # environment keep local deployment configuration out of the test process.
    with tempfile.TemporaryDirectory(
        prefix="threatlens-capacity-"
    ) as working_directory:
        code = subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(backend / "pytest.ini"),
                str(backend / "tests/capacity/test_concurrent_workload.py"),
                "-q",
                "-s",
            ],
            cwd=working_directory,
            env=env,
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
