import json
import os
import subprocess
import sys
import time

from scripts.capacity_process import execute_bounded, process_tree_rss
from tests.capacity.sustained import paced_lane


def test_watchdog_stops_only_owned_group_and_writes_failure(tmp_path):
    sibling = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True
    )
    try:
        output = tmp_path / "result.json"
        code = execute_bounded(
            [
                sys.executable,
                "-c",
                "import time; x=bytearray(32*1024*1024); time.sleep(10)",
            ],
            cwd=tmp_path,
            env={},
            limits={
                "cpu_count": 1,
                "nice": 0,
                "max_rss_bytes": 1024 * 1024,
                "wall_timeout_seconds": 5,
            },
            output=output,
            manifest=tmp_path / "absent",
            run_id="fixture",
        )
        result = json.loads(output.read_text())
        assert code != 0
        assert result["status"] == "failed"
        assert result["watchdog"]["failure"] == "owned_process_tree_rss_limit"
        assert result["watchdog"]["owned_process_tree_rss_peak_bytes"] > 1024 * 1024
        assert sibling.poll() is None
    finally:
        sibling.terminate()
        sibling.wait(timeout=5)


def test_watchdog_wall_limit_and_current_process_memory(tmp_path):
    assert process_tree_rss(os.getpid())[0] > 0
    output = tmp_path / "result.json"
    code = execute_bounded(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        env={},
        limits={
            "cpu_count": 1,
            "nice": 0,
            "max_rss_bytes": 1024**3,
            "wall_timeout_seconds": 0.1,
        },
        output=output,
        manifest=tmp_path / "absent",
        run_id="fixture",
    )
    assert code != 0
    assert json.loads(output.read_text())["watchdog"]["failure"] == "wall_time_limit"


def test_paced_lane_does_not_catch_up_after_slow_operations():
    starts = []

    def slow_operation(_index):
        starts.append(time.monotonic())
        time.sleep(0.025)

    assert paced_lane(slow_operation, duration_seconds=0.07, interval_seconds=0.01) <= 3
    assert all(b - a >= 0.02 for a, b in zip(starts, starts[1:], strict=False))


def test_watchdog_reaps_orphan_that_ignores_term_and_preserves_sibling(tmp_path):
    import signal
    from pathlib import Path

    sibling = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True
    )
    child_file = tmp_path / "child.pid"
    child_code = (
        "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open("
        + repr(str(child_file))
        + ", 'w').write(str(os.getpid())); time.sleep(20)"
    )
    leader_code = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', "
        + repr(child_code)
        + "]); time.sleep(20)"
    )
    try:
        output = tmp_path / "result.json"
        execute_bounded(
            [sys.executable, "-c", leader_code],
            cwd=tmp_path,
            env={},
            limits={
                "cpu_count": 1,
                "nice": 0,
                "max_rss_bytes": 1024**3,
                "wall_timeout_seconds": 0.75,
            },
            output=output,
            manifest=tmp_path / "absent",
            run_id="orphan-test",
        )
        assert child_file.exists()
        child_pid = int(child_file.read_text())
        assert not Path(f"/proc/{child_pid}").exists()
        assert sibling.poll() is None
    finally:
        os.killpg(sibling.pid, signal.SIGKILL)
        sibling.wait(timeout=5)


def test_failed_leader_cannot_leave_owned_orphan_running(tmp_path):
    from pathlib import Path

    child_file = tmp_path / "child.pid"
    child_code = (
        "import os,time; open("
        + repr(str(child_file))
        + ", 'w').write(str(os.getpid())); time.sleep(20)"
    )
    leader_code = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', "
        + repr(child_code)
        + "]); time.sleep(.2); sys.exit(3)"
    )
    code = execute_bounded(
        [sys.executable, "-c", leader_code],
        cwd=tmp_path,
        env={},
        limits={
            "cpu_count": 1,
            "nice": 0,
            "max_rss_bytes": 1024**3,
            "wall_timeout_seconds": 5,
        },
        output=tmp_path / "result.json",
        manifest=tmp_path / "absent",
        run_id="failure-test",
    )
    assert code == 3
    assert not Path(f"/proc/{int(child_file.read_text())}").exists()


def test_fixture_container_identity_excludes_image_pull_stderr(monkeypatch):
    from tests.capacity.docker_services import DockerService

    monkeypatch.setenv("THREATLENS_CAPACITY_RUN_ID", "fixture")

    def output(command, **kwargs):
        assert kwargs["stderr"] == subprocess.PIPE
        return "a" * 64 + "\n"

    monkeypatch.setattr(subprocess, "check_output", output)
    assert DockerService("redis").command("run", "fixture-image") == "a" * 64
