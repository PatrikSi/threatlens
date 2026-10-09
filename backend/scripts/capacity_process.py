"""Bound only the process tree and Docker resources created by this run."""

from __future__ import annotations

import json
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Share the disposable-operation guard with the topology and MCP qualifiers.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from operations.qualification_runtime import require_local_docker as require_local_docker  # noqa: E402


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


def cleanup_containers(manifest, run_id, *, wait_for_pending=False, env=None):
    # Only the supervisor-generated UUID label can authorize discovery. A
    # manifest is useful evidence, but docker run may be interrupted before its
    # accepted container ID reaches that file.
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        return {"status": "failed", "remaining_container_ids": None,
                "errors": ["invalid-run-scope"]}
    started = time.monotonic()
    deadline = started + 10
    discover_until = started + (3 if wait_for_pending else 0)
    if wait_for_pending:
        print(f"Capacity cleanup scope: threatlens.capacity.run_id={run_id}", file=sys.stderr)
    try:
        targets = set(manifest.read_text().splitlines())
    except OSError:
        targets = set()
    failures = []

    def fail(reason):
        if reason not in failures:
            failures.append(reason)

    def docker(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail("cleanup-deadline")
            return None
        try:
            return subprocess.run(["docker", *args], capture_output=True, text=True,
                                  timeout=min(2, remaining), env=env)
        except (OSError, subprocess.TimeoutExpired):
            fail("docker-unavailable")
            return None

    while True:
        found = docker("ps", "-aq", "--no-trunc", "--filter", f"label=threatlens.capacity.run_id={run_id}")
        if found is not None and found.returncode == 0:
            # Successful discovery supersedes stale IDs in the manifest, which
            # also records resources already removed by normal fixture teardown.
            targets = set(found.stdout.splitlines())
        else:
            fail("container-discovery")
            print(f"Capacity container discovery unavailable for run {run_id}; inspect its exact run label.", file=sys.stderr)
        for container_id in sorted(targets):
            if len(container_id) != 64 or any(char not in "0123456789abcdef" for char in container_id):
                fail("invalid-container-identity")
                continue
            inspected = docker("inspect", "--format", '{{index .Config.Labels "threatlens.capacity.run_id"}}', container_id)
            if inspected is not None and inspected.returncode == 0 and inspected.stdout.strip() == run_id:
                removed = docker("rm", "-f", "-v", container_id)
                if removed is not None and removed.returncode == 0:
                    targets.discard(container_id)
                else:
                    fail("container-removal")
                    print(f"Capacity container removal incomplete for run {run_id}: {container_id}", file=sys.stderr)
            elif inspected is None or inspected.returncode != 0:
                fail("container-ownership-unavailable")
        # The killed Docker client cannot cancel a daemon request already in
        # flight. Allow short, bounded late discovery; never widen the label.
        remaining = min(discover_until, deadline) - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.2, remaining))
    verified = docker("ps", "-aq", "--no-trunc", "--filter", f"label=threatlens.capacity.run_id={run_id}")
    remaining_ids = None
    if verified is not None and verified.returncode == 0:
        remaining_ids = sorted(set(verified.stdout.splitlines()))
        if remaining_ids:
            fail("containers-remain")
    else:
        fail("container-verification")
    return {"status": "failed" if failures else "passed",
            "exact_label": f"threatlens.capacity.run_id={run_id}",
            "remaining_container_ids": remaining_ids, "errors": failures}


def execute_bounded(command, *, cwd, env, limits, output, manifest, run_id):
    def child_limits():
        if limits.get("cpu_count"):
            os.sched_setaffinity(
                0, sorted(os.sched_getaffinity(0))[: limits["cpu_count"]]
            )
        os.nice(limits.get("nice", 0))

    # The CLI is single-threaded at fork. Keep the leader unreaped until its
    # whole group is dead, preventing PID/group reuse during final cleanup.
    libc = ctypes.CDLL(None, use_errno=True)
    previous_subreaper = ctypes.c_int()
    if (
        libc.prctl(37, ctypes.byref(previous_subreaper), 0, 0, 0) != 0
        or libc.prctl(36, 1, 0, 0, 0) != 0
    ):
        raise OSError(
            ctypes.get_errno(), "cannot supervise orphaned capacity descendants"
        )
    started = time.monotonic()
    process = None
    peak, process_peak, failure, code = 0, 0, None, None
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, start_new_session=True, preexec_fn=child_limits
        )

        def leader_exited():
            return (
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                is not None
            )

        while not leader_exited():
            rss, count = process_tree_rss(process.pid)
            peak, process_peak = max(peak, rss), max(process_peak, count)
            if rss > limits["max_rss_bytes"]:
                failure = "owned_process_tree_rss_limit"
            elif time.monotonic() - started > limits["wall_timeout_seconds"]:
                failure = "wall_time_limit"
            if failure:
                os.killpg(process.pid, signal.SIGTERM)
                grace = time.monotonic() + 5
                while not leader_exited() and time.monotonic() < grace:
                    time.sleep(0.05)
                break
            time.sleep(0.1)
    finally:
        if process is not None:
            # A leader can exit while a child ignores TERM, or leave children
            # behind on ordinary failure. Kill the still-owned group either way.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = process.wait()
            reap_until = time.monotonic() + 5
            while time.monotonic() < reap_until:
                try:
                    child, _status = os.waitpid(-process.pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if child == 0:
                    time.sleep(0.02)
        libc.prctl(36, previous_subreaper.value, 0, 0, 0)
        try:
            cleanup = cleanup_containers(
                manifest, run_id, env=env,
                wait_for_pending=code != 0 or failure is not None or sys.exc_info()[0] is not None,
            )
        except Exception as error:
            # Teardown must not replace an original workload exception. A
            # normally returning workload still fails the gate below.
            cleanup = {"status": "failed", "remaining_container_ids": None,
                       "errors": [f"cleanup-exception:{type(error).__name__}"]}
    watchdog = {
        "sample_interval_ms": 100,
        "owned_process_tree_rss_peak_bytes": peak,
        "owned_process_count_peak": process_peak,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "limits": limits,
        "failure": failure,
        "memory_scope": "sum of owned process RSS; shared pages counted per process; excludes Docker services",
    }
    cleanup_failed = cleanup["status"] != "passed"
    if failure or code != 0 or cleanup_failed:
        result = json.loads(output.read_text()) if output.exists() else {}
        if result.get("run_id") != run_id:
            partial = Path(str(output) + ".partial.json")
            result = json.loads(partial.read_text()) if partial.exists() else {}
            if result.get("run_id") != run_id:
                result = {"run_id": run_id}
        result.update(status="failed", watchdog=watchdog, cleanup=cleanup, process_exit_code=code)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    elif output.exists():
        result = json.loads(output.read_text())
        result["watchdog"] = watchdog
        result["cleanup"] = cleanup
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return code if code else 1 if failure or cleanup_failed else 0
