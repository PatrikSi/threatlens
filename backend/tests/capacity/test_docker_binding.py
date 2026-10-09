import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.capacity import docker_services


def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("run_capacity_baseline")


def test_cli_rejects_remote_endpoint_before_workload_creation(tmp_path, monkeypatch):
    module = runner(monkeypatch)
    monkeypatch.delenv("THREATLENS_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("THREATLENS_TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2376")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(module.subprocess, "check_output", lambda command, **_k:
                        "a" * 40 + "\n" if command[1] == "rev-parse" else "")
    monkeypatch.setattr(module.subprocess, "run", lambda command, **_k:
                        subprocess.CompletedProcess(command, 0, ""))
    monkeypatch.setattr(module, "execute_bounded", lambda *_a, **_k: pytest.fail("workload created"))
    monkeypatch.setattr(sys, "argv", ["capacity", "--output", str(tmp_path / "result.json")])
    with pytest.raises(SystemExit) as exited:
        module.main()
    assert exited.value.code == 2


def test_cli_passes_a_frozen_local_binding_to_child_and_supervisor(tmp_path, monkeypatch):
    module = runner(monkeypatch)
    monkeypatch.delenv("THREATLENS_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("THREATLENS_TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "changing-context")
    monkeypatch.setenv("DOCKER_HOST", "tcp://ignored-host:2376")
    monkeypatch.setenv("THREATLENS_TEST_POSTGRES_IMAGE", "owned-postgres:fixture")
    selected = {"DOCKER_HOST": "unix:///run/owned-docker.sock"}
    monkeypatch.setattr(module, "require_local_docker", lambda: selected, raising=False)
    monkeypatch.setattr(module.subprocess, "check_output", lambda command, **_k:
                        "a" * 40 + "\n" if command[1] == "rev-parse" else "")
    monkeypatch.setattr(module.subprocess, "run", lambda command, **_k:
                        subprocess.CompletedProcess(command, 0, ""))

    def execute(_command, **kwargs):
        environment = kwargs["env"]
        assert environment["DOCKER_HOST"] == selected["DOCKER_HOST"]
        assert "DOCKER_CONTEXT" not in environment
        assert environment["THREATLENS_TEST_POSTGRES_IMAGE"] == "owned-postgres:fixture"
        kwargs["output"].write_text(json.dumps({"run_id": kwargs["run_id"]}))
        return 0

    monkeypatch.setattr(module, "execute_bounded", execute)
    monkeypatch.setattr(sys, "argv", ["capacity", "--output", str(tmp_path / "result.json")])
    assert module.main() == 0
    assert selected == {"DOCKER_HOST": "unix:///run/owned-docker.sock"}


def test_service_creation_and_cleanup_keep_the_initial_local_binding(monkeypatch):
    selected = {"DOCKER_HOST": "unix:///run/first-docker.sock"}
    monkeypatch.setattr(docker_services, "require_local_docker", lambda: selected, raising=False)
    monkeypatch.setenv("THREATLENS_CAPACITY_RUN_ID", "a" * 32)
    service = docker_services.DockerService("redis")
    selected["DOCKER_HOST"] = "unix:///run/replacement-docker.sock"
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-after-create")

    def output(_command, **kwargs):
        assert kwargs["env"] == {"DOCKER_HOST": "unix:///run/first-docker.sock"}
        return "b" * 64

    def remove(command, **kwargs):
        assert kwargs["env"] == {"DOCKER_HOST": "unix:///run/first-docker.sock"}
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(docker_services.subprocess, "check_output", output)
    monkeypatch.setattr(docker_services.subprocess, "run", remove)
    service.id = service.command("run", "fixture-image")
    service.__exit__(None, None, None)


def test_failed_normal_service_cleanup_fails_fixture_and_preserves_original_exception(monkeypatch):
    monkeypatch.setattr(docker_services, "require_local_docker", lambda:
                        {"DOCKER_HOST": "unix:///run/owned-docker.sock"}, raising=False)
    monkeypatch.setenv("THREATLENS_CAPACITY_RUN_ID", "a" * 32)
    service = docker_services.DockerService("redis")
    service.id = "b" * 64
    monkeypatch.setattr(docker_services.subprocess, "run", lambda command, **_kwargs:
                        subprocess.CompletedProcess(command, 1, ""))
    with pytest.raises(RuntimeError, match="service removal failed"):
        service.__exit__(None, None, None)
    service.__exit__(ValueError, ValueError("original failure"), None)
