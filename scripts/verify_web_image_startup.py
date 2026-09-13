#!/usr/bin/env python3
"""Check non-root web startup with legacy and read-only Compose filesystems."""

from __future__ import annotations

import argparse
from contextlib import suppress
import signal
import subprocess
import sys
import time
import uuid


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=30,
    )
    if check and result.returncode:
        raise RuntimeError(f"Docker command failed: {result.stdout}{result.stderr}")
    return result


def wait_for_web(container: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        running = docker("inspect", "--format", "{{.State.Running}}", container)
        if running.stdout.strip() != "true":
            raise RuntimeError("Web container exited before serving HTTP")
        response = docker(
            "exec", container, "wget", "-q", "-T", "2", "-O", "-",
            "http://127.0.0.1:3000/", check=False,
        )
        if response.returncode == 0 and 'id="root"' in response.stdout:
            return
        time.sleep(0.5)
    raise RuntimeError("Web image did not serve the application within 45 seconds")


def verify(image: str, platform: str | None) -> None:
    cases = {
        "legacy-writable-root": [],
        "read-only-with-tmpfs": [
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m,mode=1777",
            "--tmpfs", "/etc/nginx/conf.d:rw,noexec,nosuid,size=1m,uid=101,gid=101,mode=0755",
        ],
    }
    for case, filesystem in cases.items():
        container = f"threatlens-web-startup-{uuid.uuid4().hex[:12]}"
        try:
            docker(
                "create", "--name", container, "--pull", "never",
                "--network", "none", "--add-host", "api:127.0.0.1",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--memory", "128m", "--memory-swap", "128m",
                "--pids-limit", "64", "--cpus", "0.5",
                *(["--platform", platform] if platform else []),
                *filesystem, image,
            )
            docker("start", container)
            wait_for_web(container)
            if docker("exec", container, "id", "-u").stdout.strip() != "101":
                raise RuntimeError("Web process must run as nginx UID 101")
            docker("exec", container, "nginx", "-t")
            docker("restart", "--time", "5", container)
            wait_for_web(container)
            print(f"{case}: passed (non-root startup, application HTTP, restart)", flush=True)
        except Exception:
            with suppress(OSError, subprocess.TimeoutExpired):
                logs = docker("logs", "--tail", "60", container, check=False)
                print(f"{case} failed:\n{logs.stdout}{logs.stderr}", flush=True)
            raise
        finally:
            failure_in_progress = sys.exc_info()[0] is not None
            try:
                docker("rm", "--force", container)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                if not failure_in_progress:
                    raise
                print(f"Cleanup failed for {container}: {exc}", file=sys.stderr)


def terminate(signum: int, _frame: object) -> None:
    # Let an enclosing CI timeout unwind through the owned-container cleanup.
    raise SystemExit(128 + signum)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"))
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, terminate)
    verify(args.image, args.platform)


if __name__ == "__main__":
    main()
