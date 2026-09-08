"""Bound only the process tree and Docker resources created by this run."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path


def process_tree_rss(pid):
    pending, visited, rss = [pid], set(), 0
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            rss += int(
                Path(f"/proc/{current}/statm").read_text().split()[1]
            ) * os.sysconf("SC_PAGE_SIZE")
            pending.extend(
                int(child)
                for child in Path(f"/proc/{current}/task/{current}/children")
                .read_text()
                .split()
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rss, len(visited)


def cleanup_containers(manifest, run_id):
    if not manifest.exists():
        return
    for container_id in manifest.read_text().splitlines():
        if len(container_id) != 64 or any(
            char not in "0123456789abcdef" for char in container_id
        ):
            continue
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "threatlens.capacity.run_id"}}',
                container_id,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == run_id:
            subprocess.run(
                ["docker", "rm", "-f", container_id], capture_output=True, timeout=15
            )


def execute_bounded(command, *, cwd, env, limits, output, manifest, run_id):
    def child_limits():
        if limits.get("cpu_count"):
            os.sched_setaffinity(
                0, sorted(os.sched_getaffinity(0))[: limits["cpu_count"]]
            )
        os.nice(limits.get("nice", 0))

    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=cwd, env=env, start_new_session=True, preexec_fn=child_limits
    )
    peak, process_peak, failure = 0, 0, None
    try:
        while process.poll() is None:
            rss, count = process_tree_rss(process.pid)
            peak, process_peak = max(peak, rss), max(process_peak, count)
            if rss > limits["max_rss_bytes"]:
                failure = "owned_process_tree_rss_limit"
            elif time.monotonic() - started > limits["wall_timeout_seconds"]:
                failure = "wall_time_limit"
            if failure:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.1)
        code = process.wait()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        cleanup_containers(manifest, run_id)
    watchdog = {
        "sample_interval_ms": 100,
        "owned_process_tree_rss_peak_bytes": peak,
        "owned_process_count_peak": process_peak,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "limits": limits,
        "failure": failure,
        "memory_scope": "sum of owned process RSS; shared pages counted per process; excludes Docker services",
    }
    if failure or code != 0:
        result = json.loads(output.read_text()) if output.exists() else {}
        if result.get("run_id") != run_id:
            partial = Path(str(output) + ".partial.json")
            result = json.loads(partial.read_text()) if partial.exists() else {}
            if result.get("run_id") != run_id:
                result = {"run_id": run_id}
        result.update(status="failed", watchdog=watchdog, process_exit_code=code)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    elif output.exists():
        result = json.loads(output.read_text())
        result["watchdog"] = watchdog
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return code if code else 1 if failure else 0
