"""Qualification transport failures must not bypass owned cleanup or evidence."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest


def _script(name):
    path = Path(__file__).resolve().parents[3] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_mcp_startup_retries_connection_reset(monkeypatch):
    module = _script("verify_mcp_proxy.py")
    clock = _Clock()
    monkeypatch.setattr(module, "time", clock)
    attempts = []

    def ready():
        attempts.append(clock.now)
        if len(attempts) == 1:
            raise ConnectionResetError("transient reset")
        return True

    module.wait_until(ready, seconds=1)
    assert attempts == [0.0, 0.3]


def test_mcp_startup_reset_keeps_original_budget(monkeypatch):
    module = _script("verify_mcp_proxy.py")
    clock = _Clock()
    monkeypatch.setattr(module, "time", clock)
    attempts = []

    def reset():
        attempts.append(clock.now)
        raise ConnectionResetError("persistent reset")

    with pytest.raises(RuntimeError, match="startup budget"):
        module.wait_until(reset, seconds=1)
    assert all(attempt < 1 for attempt in attempts)
    assert len(attempts) == 4


def test_mcp_startup_does_not_hide_programming_errors(monkeypatch):
    module = _script("verify_mcp_proxy.py")
    monkeypatch.setattr(module, "time", _Clock())
    with pytest.raises(ValueError, match="invalid probe"):
        module.wait_until(lambda: (_ for _ in ()).throw(ValueError("invalid probe")))


@pytest.fixture
def isolation(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    module = _script("verify_runtime_isolation.py")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    directory = tmp_path / "owned-stack"
    directory.mkdir()
    result_path = tmp_path / "result.json"
    clock = _Clock()
    state = SimpleNamespace(
        module=module, directory=directory, result_path=result_path, clock=clock,
        commands=[], startup_error=None, logs_timeout=False, cleanup_failure=False,
        pressure_cleanup_failure=False, transport_errors=[],
    )
    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: str(directory))
    monkeypatch.setattr(sys, "argv", ["verify_runtime_isolation.py", "--output", str(result_path)])

    def run(command, **kwargs):
        state.commands.append(command)
        if command[:2] == ["git", "archive"]:
            with tarfile.open(fileobj=kwargs["stdout"], mode="w"):
                pass
        if "--wait-timeout" in command and state.startup_error is not None:
            raise state.startup_error
        if "logs" in command and state.logs_timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        code = 0
        if "down" in command and state.cleanup_failure:
            code = 17
        if command[:3] == ["docker", "rm", "-f"] and state.pressure_cleanup_failure:
            code = 18
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, command)
        return subprocess.CompletedProcess(command, code)

    def output(command, **kwargs):
        if command[:2] == ["git", "rev-parse"]:
            return "a" * 40
        if command[:3] == ["docker", "image", "inspect"]:
            return "sha256:" + ("b" if "backend" in command[-1] else "c") * 64
        if command[:2] == ["docker", "inspect"]:
            if command[-1].endswith("-memory-probe"):
                return json.dumps([{"State": {"OOMKilled": True}}])
            project = next(command[command.index("--project-name") + 1]
                           for command in state.commands if "--project-name" in command)
            return json.dumps([{
                "Config": {"Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": "api",
                }},
                "HostConfig": {
                    "ReadonlyRootfs": True, "Memory": 1024, "NanoCpus": 1000,
                    "PidsLimit": 32, "MemorySwap": 1024,
                    "SecurityOpt": ["no-new-privileges:true"],
                },
            }])
        if "ps" in command:
            return "owned-container"
        if "port" in command:
            return "127.0.0.1:12345"
        raise AssertionError(command)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(*args, **kwargs):
        clock.now += 0.05
        if state.transport_errors:
            raise state.transport_errors.pop(0)
        return Response()

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.subprocess, "check_output", output)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        wait=lambda **kwargs: 0, poll=lambda: 0,
    ))
    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return state


@pytest.mark.parametrize("error", [
    ConnectionResetError("transient reset"), TimeoutError("transient timeout"),
    URLError(TimeoutError("wrapped timeout")), URLError(ConnectionResetError("wrapped reset")),
    HTTPError("http://fixture", 503, "workers warming up", None, None),
])
def test_isolation_initial_readiness_retries_transient_transport(isolation, error):
    isolation.transport_errors = [error]
    assert isolation.module.main() == 0
    result = json.loads(isolation.result_path.read_text())
    assert result["status"] == "passed"
    assert result["restart_recovered"]


@pytest.mark.parametrize("error", [
    HTTPError("http://fixture", 404, "missing", None, None),
    URLError("invalid hostname"),
])
def test_isolation_readiness_preserves_nontransient_errors(isolation, error):
    isolation.transport_errors = [error]
    with pytest.raises(type(error)) as caught:
        isolation.module.main()
    assert caught.value is error
    assert any("down" in command for command in isolation.commands)


def test_isolation_transient_polling_uses_existing_deadline(monkeypatch):
    module = _script("verify_runtime_isolation.py")
    clock = _Clock()
    monkeypatch.setattr(module, "time", clock)
    error = TimeoutError("service remains unavailable")
    attempts = []

    def unavailable():
        attempts.append(clock.now)
        raise error

    with pytest.raises(TimeoutError) as caught:
        module.wait_for_initial_readiness(unavailable, seconds=4)
    assert caught.value is error
    assert attempts == [0.0, 2.0, 4.0]


def test_isolation_logs_timeout_still_cleans_and_preserves_original_error(isolation):
    error = RuntimeError("startup check failed")
    isolation.startup_error = error
    isolation.logs_timeout = True
    with pytest.raises(RuntimeError, match="startup check failed") as caught:
        isolation.module.main()
    assert caught.value is error
    assert any("down" in command for command in isolation.commands)
    result = json.loads(isolation.result_path.read_text())
    assert result["error_type"] == "RuntimeError"
    assert result["diagnostic_errors"][0]["error_type"] == "TimeoutExpired"


def test_isolation_cleanup_failure_reports_without_masking_original_error(isolation):
    error = RuntimeError("startup check failed")
    isolation.startup_error = error
    isolation.cleanup_failure = True
    with pytest.raises(RuntimeError, match="startup check failed") as caught:
        isolation.module.main()
    assert caught.value is error
    result = json.loads(isolation.result_path.read_text())
    assert result["status"] == "failed"
    assert result["cleanup_errors"][0]["step"] == "cleanup"
    assert any("cleanup failed" in note for note in error.__notes__)


def test_isolation_cleanup_failure_rejects_an_otherwise_passed_run(isolation):
    isolation.cleanup_failure = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        isolation.module.main()
    result = json.loads(isolation.result_path.read_text())
    assert result["status"] == "failed"
    assert result["cleanup_errors"][0]["step"] == "cleanup"
    assert any(command[:3] == ["docker", "rm", "-f"] for command in isolation.commands)


def test_isolation_logs_timeout_cleans_all_created_resources_and_writes_result(isolation):
    isolation.logs_timeout = True
    assert isolation.module.main() == 0
    assert any("down" in command for command in isolation.commands)
    assert any(command[:3] == ["docker", "rm", "-f"] for command in isolation.commands)
    result = json.loads(isolation.result_path.read_text())
    assert result["status"] == "passed"
    assert result["diagnostic_errors"][0]["error_type"] == "TimeoutExpired"


def test_isolation_pressure_cleanup_failure_is_reported(isolation):
    isolation.pressure_cleanup_failure = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        isolation.module.main()
    result = json.loads(isolation.result_path.read_text())
    assert result["status"] == "failed"
    assert result["cleanup_errors"][0]["step"] == "pressure-cleanup"


def test_isolation_evidence_records_immutable_tested_image_ids(isolation):
    assert isolation.module.main() == 0
    result = json.loads(isolation.result_path.read_text())
    assert result["images"]["backend"]["id"] == "sha256:" + "b" * 64
    assert result["images"]["web"]["id"] == "sha256:" + "c" * 64
