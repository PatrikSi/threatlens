import subprocess

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
    assert len([command for command in commands if command[1] == "ps"]) == 1


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
