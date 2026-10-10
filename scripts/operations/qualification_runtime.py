"""Owned disposable service/process lifecycle for local topology qualification."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def clean_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "TMPDIR", "LANG", "TZ", "XDG_RUNTIME_DIR"}
    return {key: value for key, value in os.environ.items() if key in allowed or key.startswith(("DOCKER_", "LC_"))}


def require_local_docker() -> dict[str, str]:
    """Resolve context precedence before allowing disposable local creation."""
    environment = clean_environment()
    context = environment.get("DOCKER_CONTEXT")
    host = environment.get("DOCKER_HOST")
    if context or not host:
        arguments = ["docker", "context", "inspect", *([context] if context else []),
                     "--format", "{{.Endpoints.docker.Host}}"]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=15,
                                check=False, env=environment)
        if result.returncode:
            raise RuntimeError("Unable to establish the qualification Docker endpoint")
        host = result.stdout.strip()
    if not host.startswith("unix://"):
        raise ValueError("Qualification requires a local Docker Unix socket")
    # Bind all later create/inspect/remove calls to the validated socket. A
    # concurrent context switch must not redirect resources or their cleanup.
    environment.pop("DOCKER_CONTEXT", None)
    environment["DOCKER_HOST"] = host
    return environment


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class DisposableTopology:
    def __init__(self, directory: Path, *, docker_environment: dict[str, str] | None = None):
        self.directory = directory
        self.docker_environment = dict(docker_environment) if docker_environment is not None else clean_environment()
        self.run_id = uuid.uuid4().hex
        self.containers: list[str] = []
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs = []
        self.image_ids: dict[str, str] = {}
        self.cleanup_result: dict = {"status": "not_attempted"}

    def docker(self, *args: str, timeout: int = 60) -> str:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                                env=self.docker_environment, check=False)
        if result.returncode:
            raise RuntimeError(f"Disposable Docker operation {args[0]} failed")
        return result.stdout.strip()

    def service(self, service: str, image: str, *args: str, command: list[str] | None = None) -> str:
        name = f"threatlens-qualification-{self.run_id}-{service}"
        self.image_ids[service] = self.docker("image", "inspect", "--format", "{{.Id}}", image)
        self.containers.append(name)
        self.docker("run", "--rm", "--detach", "--name", name, "--label",
                    f"threatlens.qualification.run={self.run_id}",
                    "--label", f"com.docker.compose.project=qualification-{self.run_id}",
                    "--label", f"com.docker.compose.service={service}", *args, image, *(command or []))
        return name

    def start(self, role: str, command: list[str], env: dict[str, str]) -> subprocess.Popen:
        log = (self.directory / f"{role}.log").open("a")
        self.logs.append(log)
        process = subprocess.Popen(command, cwd=self.directory, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        self.processes[role] = process
        return process

    def stop(self, role: str) -> None:
        process = self.processes.pop(role, None)
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def memory(self) -> dict[str, int]:
        totals = {}
        for role, process in self.processes.items():
            if process.poll() is not None:
                raise RuntimeError(f"Required qualification process {role} exited")
            waiting, visited, total = [process.pid], set(), 0
            while waiting and len(visited) < 64:
                pid = waiting.pop()
                if pid in visited:
                    continue
                visited.add(pid)
                try:
                    fields = Path(f"/proc/{pid}/status").read_text().splitlines()
                    total += next((int(line.split()[1]) * 1024 for line in fields if line.startswith("VmRSS:")), 0)
                    waiting += [int(value) for value in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
                except (OSError, ValueError) as error:
                    if pid == process.pid:
                        raise RuntimeError(f"Required process {role} memory/liveness could not be observed") from error
            totals[role] = total
        return totals

    def close(self) -> None:
        failures = []
        for role in list(self.processes):
            try:
                self.stop(role)
            except (OSError, subprocess.SubprocessError):
                failures.append(f"process:{role}")
        label = f"label=threatlens.qualification.run={self.run_id}"
        # Discover actual ownership, including a daemon-accepted create whose
        # client acknowledgement was lost. Never remove by a name alone.
        try:
            containers = self.docker("ps", "--all", "--quiet", "--filter", label, timeout=30).splitlines()
        except (RuntimeError, OSError, subprocess.SubprocessError):
            failures.append("container-discovery")
            containers = []
        remaining = None
        # One additional sweep covers a create completing during the first
        # observation/removal. A failed operation remains a failed gate even if
        # a later attempt succeeds; cleanup still makes bounded best effort.
        for _sweep in range(2):
            for identity in containers:
                try:
                    removed = subprocess.run(["docker", "rm", "--force", "--volumes", identity],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             timeout=30, check=False, env=self.docker_environment)
                    if removed.returncode:
                        failures.append("container-removal")
                except (OSError, subprocess.SubprocessError):
                    failures.append("container-removal")
            try:
                remaining = None
                remaining = self.docker("ps", "--all", "--quiet", "--filter", label, timeout=30).splitlines()
            except (RuntimeError, OSError, subprocess.SubprocessError):
                failures.append("container-verification")
                break
            if not remaining:
                break
            containers = remaining
        if remaining:
            failures.append("containers-remain")
        for log in self.logs:
            try:
                log.close()
            except OSError:
                failures.append("log-close")
        self.cleanup_result = {"status": "failed" if failures else "passed",
                               "exact_label": label.removeprefix("label="),
                               "remaining_container_ids": remaining, "errors": failures}
        if failures:
            raise RuntimeError(f"Disposable cleanup could not complete {len(failures)} owned resource operations")


def wait_http(client, path: str, *, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get(path, timeout=1).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(.2)
    raise TimeoutError("Disposable service startup exceeded its deadline")
