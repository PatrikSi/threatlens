import subprocess
import json
import sys

import pytest

from scripts import capacity_process

RUN_ID = "a" * 32
OWNED = "b" * 64
UNRELATED = "c" * 64


def test_missing_manifest_discovers_only_exact_run_label_and_rechecks_ownership(tmp_path, monkeypatch):
    commands = []

    def docker(command, **kwargs):
        commands.append(command)
        assert 0 < kwargs["timeout"] <= 2
        if command[1] == "ps":
            assert command[-1] == f"label=threatlens.capacity.run_id={RUN_ID}"
            return subprocess.CompletedProcess(command, 0, f"{OWNED}\n{UNRELATED}\nnot-an-id\n")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, RUN_ID if command[-1] == OWNED else "another-run")
        assert command[1:] == ["rm", "-f", "-v", OWNED]
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(capacity_process.subprocess, "run", docker)
    capacity_process.cleanup_containers(tmp_path / "not-yet-written", RUN_ID)
    assert [command[-1] for command in commands if command[1] == "rm"] == [OWNED]
    assert len([command for command in commands if command[1] == "ps"]) == 2


def test_interrupted_creation_is_discovered_after_the_client_has_gone(tmp_path, monkeypatch):
    clock = [0.0]
    removed = []
    monkeypatch.setattr(capacity_process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(capacity_process.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def docker(command, **_kwargs):
        if command[1] == "ps":
            # A request accepted before client death finishes later at the daemon.
            visible = OWNED if clock[0] >= 0.4 and not removed else ""
            return subprocess.CompletedProcess(command, 0, visible)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, RUN_ID)
        assert command[-1] == OWNED
        removed.append((command[-1], clock[0]))
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(capacity_process.subprocess, "run", docker)
    capacity_process.cleanup_containers(tmp_path / "missing-id", RUN_ID, wait_for_pending=True)
    assert removed == [(OWNED, 0.4)]
    assert clock[0] == pytest.approx(3.0)


def test_stalled_daemon_cleanup_is_bounded_and_discloses_its_scope(tmp_path, monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(capacity_process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(capacity_process.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    manifest = tmp_path / "manifest"
    manifest.write_text(f"{OWNED}\n{UNRELATED}\n")

    def unavailable(command, **kwargs):
        assert 0 < kwargs["timeout"] <= 2
        clock[0] += kwargs["timeout"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(capacity_process.subprocess, "run", unavailable)
    capacity_process.cleanup_containers(manifest, RUN_ID, wait_for_pending=True)
    assert clock[0] <= 10
    assert RUN_ID in capsys.readouterr().err


def test_cleanup_never_uses_a_missing_or_non_generated_run_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(capacity_process.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("unscoped Docker call"))
    for run_id in ("", "fixture", "label=x", "a" * 31):
        capacity_process.cleanup_containers(tmp_path / "missing", run_id, wait_for_pending=True)


def test_manifest_removal_still_requires_matching_label_when_discovery_fails(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest"
    manifest.write_text(f"{OWNED}\n{UNRELATED}\n")
    removed = []

    def docker(command, **_kwargs):
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 1, "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, RUN_ID if command[-1] == OWNED else "")
        removed.append(command[-1])
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(capacity_process.subprocess, "run", docker)
    capacity_process.cleanup_containers(manifest, RUN_ID)
    assert removed == [OWNED]
    assert "discovery unavailable" in capsys.readouterr().err


def test_cleanup_failure_is_a_failed_qualification_outcome(tmp_path, monkeypatch):
    def docker(command, **_kwargs):
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, OWNED + "\n")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, RUN_ID)
        return subprocess.CompletedProcess(command, 1, "")

    monkeypatch.setattr(capacity_process.subprocess, "run", docker)
    result = capacity_process.cleanup_containers(tmp_path / "missing", RUN_ID)
    assert result["status"] == "failed"
    assert result["remaining_container_ids"] == [OWNED]
    assert "container-removal" in result["errors"]


def test_cleanup_unknown_daemon_state_cannot_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(capacity_process.subprocess, "run", lambda command, **_kwargs:
                        subprocess.CompletedProcess(command, 1, ""))
    result = capacity_process.cleanup_containers(tmp_path / "missing", RUN_ID)
    assert result["status"] == "failed"
    assert result["remaining_container_ids"] is None


def test_failed_removal_still_attempts_other_owned_resources(tmp_path, monkeypatch):
    second = "d" * 64
    removed = []

    def docker(command, **_kwargs):
        if command[1] == "ps":
            visible = [OWNED] + ([] if second in removed else [second])
            return subprocess.CompletedProcess(command, 0, "\n".join(visible))
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, RUN_ID)
        removed.append(command[-1])
        return subprocess.CompletedProcess(command, int(command[-1] == OWNED), "")

    monkeypatch.setattr(capacity_process.subprocess, "run", docker)
    result = capacity_process.cleanup_containers(tmp_path / "missing", RUN_ID)
    assert removed == [OWNED, second]
    assert result["status"] == "failed"
    assert result["remaining_container_ids"] == [OWNED]


@pytest.mark.parametrize("workload_exit", [0, 3])
def test_supervisor_preserves_workload_failure_and_rejects_failed_cleanup(
    tmp_path, monkeypatch, workload_exit
):
    output = tmp_path / "result.json"
    cleanup = {"status": "failed", "remaining_container_ids": [OWNED],
               "errors": ["container-removal"]}
    monkeypatch.setattr(capacity_process, "cleanup_containers", lambda *_args, **_kwargs: cleanup)
    child = (f"import json,sys; open({str(output)!r},'w').write(json.dumps("
             f"{{'run_id':{RUN_ID!r},'status':'passed'}})); sys.exit({workload_exit})")
    result = capacity_process.execute_bounded(
        [sys.executable, "-c", child], cwd=tmp_path, env={},
        limits={"cpu_count": 1, "nice": 0, "max_rss_bytes": 1024**3,
                "wall_timeout_seconds": 5},
        output=output, manifest=tmp_path / "missing", run_id=RUN_ID,
    )
    assert result == (workload_exit or 1)
    artifact = json.loads(output.read_text())
    assert artifact["status"] == "failed"
    assert artifact["cleanup"] == cleanup
    assert artifact["process_exit_code"] == workload_exit


def test_cleanup_uses_the_validated_environment_for_every_operation(tmp_path, monkeypatch):
    environment = {"DOCKER_HOST": "unix:///run/owned-docker.sock"}
    removed = []

    def docker(command, **kwargs):
        assert kwargs["env"] is environment
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, "" if removed else OWNED)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, RUN_ID)
        removed.append(command[-1])
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(capacity_process.subprocess, "run", docker)
    result = capacity_process.cleanup_containers(tmp_path / "missing", RUN_ID, env=environment)
    assert result["status"] == "passed"
    assert result["remaining_container_ids"] == []


def test_teardown_exception_cannot_replace_original_workload_exception(tmp_path, monkeypatch):
    def popen(*_args, **_kwargs):
        raise ValueError("original workload failure")

    def cleanup(*_args, **_kwargs):
        raise RuntimeError("cleanup failure")

    monkeypatch.setattr(capacity_process.subprocess, "Popen", popen)
    monkeypatch.setattr(capacity_process, "cleanup_containers", cleanup)
    with pytest.raises(ValueError, match="original workload failure"):
        capacity_process.execute_bounded(
            [sys.executable], cwd=tmp_path, env={},
            limits={"max_rss_bytes": 1024**3, "wall_timeout_seconds": 5},
            output=tmp_path / "result.json", manifest=tmp_path / "missing", run_id=RUN_ID,
        )
