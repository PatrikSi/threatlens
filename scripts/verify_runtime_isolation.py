#!/usr/bin/env python3
"""Build tracked sources and exercise only an owned, disposable Compose stack."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    directory = Path(tempfile.mkdtemp(prefix="threatlens-runtime-isolation-"))
    project = f"threatlens-isolation-{uuid.uuid4().hex[:12]}"
    pressure = f"{project}-memory-probe"
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "LANG", "XDG_RUNTIME_DIR"} or key.startswith("DOCKER_")}
    result = {"status": "failed", "project": project, "logs": str(directory)}
    compose: list[str] = []
    stress = None

    def run(command: list[str], *, name: str, timeout: int = 240, check: bool = True):
        with (directory / f"{name}.log").open("w") as log:
            return subprocess.run(command, cwd=directory, env=env, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout, check=check)

    def output(command: list[str]) -> str:
        return subprocess.check_output(command, cwd=directory, env=env, text=True, timeout=30).strip()

    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        result["revision"] = revision
        archive = directory / "source.tar"
        with archive.open("wb") as target:
            subprocess.run(["git", "archive", revision], cwd=repo, stdout=target, check=True)
        with tarfile.open(archive) as source:
            source.extractall(directory, filter="data")
        archive.unlink()
        # Neither deployment configuration nor untracked files enter the build.
        assert not (directory / "backend/uv.lock").exists()
        assert not (directory / "backups").exists()
        fixture = directory / "fixture.env"
        fixture.write_text("\n".join(f"{key}={value}" for key, value in {
            "POSTGRES_DB": "synthetic", "POSTGRES_USER": "recovery_admin",
            "POSTGRES_RUNTIME_USER": "synthetic_runtime", "POSTGRES_MIGRATION_USER": "synthetic_migration",
            **{key: secrets.token_hex(24) for key in (
                "POSTGRES_PASSWORD", "POSTGRES_RUNTIME_PASSWORD", "POSTGRES_MIGRATION_PASSWORD",
                "REDIS_PASSWORD", "JWT_SECRET", "APP_DATA_ENCRYPTION_KEY", "ADMIN_PASSWORD",
            )},
            "APP_ENV": "development", "AUTH_COOKIE_SECURE": "false",
            "SEED_ADMIN_ON_STARTUP": "true", "THREATLENS_WEB_PORT": "127.0.0.1:0",
        }.items()) + "\n")
        fixture.chmod(0o600)
        backend_image, web_image = f"{project}-backend:test", f"{project}-web:test"
        for name, image in (("backend", backend_image), ("web", web_image)):
            run(["docker", "build", "-f", f"docker/{name}.Dockerfile", "-t", image,
                 "--build-arg", f"VCS_REF={revision}", name], name=f"build-{name}", timeout=900)
        backend_services = ("api", "migrate", "worker", "worker-exports", "worker-ai",
                            "worker-maintenance", "worker-notifications", "beat")
        override = directory / "isolation.override.yml"
        override.write_text("services:\n" + "".join(
            f"  {service}:\n    image: {backend_image}\n    pull_policy: never\n" for service in backend_services
        ) + f"  web:\n    image: {web_image}\n    pull_policy: never\n")
        compose = ["docker", "compose", "--project-name", project, "--env-file", str(fixture),
                   "--file", "docker-compose.yml", "--file", str(override)]
        run([*compose, "up", "-d", "--wait", "--wait-timeout", "240"], name="startup", timeout=300)
        containers = output([*compose, "ps", "-a", "-q"]).splitlines()
        inspected = json.loads(output(["docker", "inspect", *containers]))
        budgets = {}
        for container in inspected:
            labels = container["Config"]["Labels"]
            assert labels["com.docker.compose.project"] == project
            service = labels["com.docker.compose.service"]
            host = container["HostConfig"]
            assert host["ReadonlyRootfs"] and host["Memory"] > 0
            assert host["NanoCpus"] > 0 and host["PidsLimit"] > 0
            assert host["MemorySwap"] == host["Memory"]
            assert "no-new-privileges:true" in host["SecurityOpt"]
            budgets[service] = {key: host[key] for key in ("Memory", "NanoCpus", "PidsLimit")}
        result["budgets"] = budgets
        port = output([*compose, "port", "web", "3000"])
        base = f"http://{port}"

        def ready() -> float:
            started = time.monotonic()
            with urllib.request.urlopen(f"{base}/api/v1/health/ready", timeout=15) as response:
                assert response.status == 200
            return (time.monotonic() - started) * 1000

        result["initial_readiness_ms"] = round(ready(), 2)
        probe = (
            "import os,tempfile; from pathlib import Path; "
            "p=Path('/app/forbidden-write'); "
            "assert os.getuid()!=0; "
            "assert 'CapEff:\\t0000000000000000' in Path('/proc/self/status').read_text(); "
            "f=tempfile.TemporaryFile(); f.write(b'x'*16777216); f.close(); "
            "assert not os.access('/app',os.W_OK)"
        )
        run([*compose, "exec", "-T", "worker-exports", "python", "-c", probe], name="write-boundaries")
        # Deliberately consume the export container's capped CPU while probing
        # API readiness. The stress process ends itself and belongs to this stack.
        stress_code = "import time; end=time.monotonic()+20\nwhile time.monotonic()<end: sum(i*i for i in range(10000))"
        stress = subprocess.Popen([*compose, "exec", "-T", "worker-exports", "python", "-c", stress_code],
                                  cwd=directory, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        latencies = []
        until = time.monotonic() + 20
        while time.monotonic() < until:
            latencies.append(ready())
            time.sleep(0.5)
        stress.wait(timeout=10)
        result["export_cpu_pressure_readiness"] = {"requests": len(latencies), "max_ms": round(max(latencies), 2)}
        # Exercise OOM isolation in a separate owned container, never on the host.
        run(["docker", "create", "--name", pressure, "--label", f"threatlens.isolation={project}",
             "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
             "--memory", "128m", "--memory-swap", "128m", "--pids-limit", "32", "--cpus", "0.25",
             backend_image, "python", "-c", "data=bytearray(256*1024*1024)"], name="create-memory-probe")
        run(["docker", "start", "-a", pressure], name="memory-pressure", check=False, timeout=45)
        pressure_state = json.loads(output(["docker", "inspect", pressure]))[0]["State"]
        assert pressure_state["OOMKilled"] is True
        result["oom_isolated"] = True
        ready()
        run([*compose, "restart", "beat", "worker-exports"], name="restart")
        run([*compose, "up", "-d", "--wait", "--wait-timeout", "180"], name="restart-health", timeout=240)
        ready()
        result["restart_recovered"] = True
        result["status"] = "passed"
        return 0
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        raise
    finally:
        if stress is not None and stress.poll() is None:
            stress.terminate()
            stress.wait(timeout=10)
        if compose:
            run([*compose, "logs", "--no-color", "--tail", "80"], name="service-logs", check=False)
            run([*compose, "down", "--volumes", "--remove-orphans"], name="cleanup", check=False)
        run(["docker", "rm", "-f", pressure], name="pressure-cleanup", check=False)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
