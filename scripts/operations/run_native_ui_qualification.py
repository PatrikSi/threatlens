#!/usr/bin/env python3
"""Qualify the retained native UI workflows in a fresh disposable local stack."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import tarfile
import time
import uuid
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError

from native_ui_config import build_config

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "e085733b1200b8308f0011cf50d3abe79fa792b4"
DRIVER_SHA = "fa64844fc5d289b3339fc8fe6b01ba391f8c58c6e03e99b566587e8e720b718c"
LOCK_SHA = "ff0c8a90497ff530bc41335654a59f644226e0782773f1454ab0a42afda8e64b"
PLAYWRIGHT = "mcr.microsoft.com/playwright@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27"
POSTGRES = "postgres@sha256:b6ccf02e9b47eac0d67b5eaa0ef56fd59163bffa5506f64e96ceb5053130ec86"
REDIS = "redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
CAPS = {
    "api": (.5, 512, 256), "beat": (.25, 320, 96), "db": (.5, 512, 256),
    "migrate": (1, 512, 64), "redis": (.25, 128, 128),
    "review-source": (.1, 64, 32), "web": (.25, 128, 64),
    "worker": (.5, 512, 256), "worker-ai": (.25, 512, 128),
    "worker-exports": (.25, 512, 128), "worker-maintenance": (.25, 512, 128),
    "worker-notifications": (.25, 512, 256),
}
IMAGE_GROUPS = ["backend/app", "backend/alembic", "backend/alembic.ini", "backend/scripts",
                "backend/compliance", "backend/.dockerignore", "backend/requirements-lock.txt",
                "backend/requirements.txt", "web", "docker", "docker-compose.yml",
                "docker-compose.build.yml", "VERSION", "LICENSE"]
CHECKS = ["/settings/access", "article preview and team assessment navigation",
          "reviewed publication preview and consumer contrast",
          "provider draft tab retention and discard navigation"]
BACKGROUND = ["beat", "worker", "worker-ai", "worker-notifications", "worker-exports", "worker-maintenance"]
CERTIFICATE = ROOT / ".github/native-ui-review-certificate.pem"


def readiness_admission(status: int, content_type: str, body: bytes) -> bool:
    if type(status) is not int or content_type != "application/json" or not isinstance(body, bytes) or not 1 <= len(body) <= 2000000:
        raise ValueError("Invalid readiness response")
    value = json.loads(body)
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise ValueError("Invalid readiness body")
    if status == 200 and value["ok"] is True:
        return True
    if status == 503 and value["ok"] is False:
        return False
    raise ValueError("Inconsistent readiness status/body")


def wait_readiness(read, deadline: float, attempts: list, *, clock=time.monotonic, sleep=time.sleep) -> None:
    """Await application readiness within the existing background startup budget."""
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Original background startup deadline expired")
        status, content_type, body = read(min(15, remaining))
        row = {"http_status": status, "response_bytes": len(body),
               "response_sha256": hashlib.sha256(body).hexdigest()}
        attempts.append(row)
        admitted = readiness_admission(status, content_type, body)
        row["ok"] = admitted
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Readiness exceeded original startup deadline")
        if admitted:
            return
        sleep(min(5, remaining))


def pressure(path: Path) -> dict[str, float]:
    result = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        value = float(dict(field.split("=", 1) for field in fields[1:])["avg10"])
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Invalid host pressure sample")
        result[fields[0]] = value
    return result


def eligible_host(sample: dict) -> bool:
    try:
        memory = sample["available_memory_bytes"]
        docker = sample["docker_seconds"]
        values = [sample[key][scope] for key, scope in [("cpu", "some"), ("memory", "some"),
                  ("memory", "full"), ("io", "some"), ("io", "full")]]
        if type(memory) is not int or memory < 0 or type(docker) not in [int, float] or not math.isfinite(docker) or docker < 0:
            return False
        if any(type(value) not in [int, float] or not math.isfinite(value) or not 0 <= value <= 100 for value in values):
            return False
        return memory >= 3 * 1024**3 and docker <= 5 and all(value <= cap for value, cap in zip(values, [25, 1, .5, 5, 1]))
    except (KeyError, TypeError):
        return False


def browser_projection(value: dict) -> dict:
    """Preserve actual partial failures without exposing paths, IDs or error text."""
    if value.get("sourceSha") != SOURCE or value.get("version") != "2.1.0":
        raise ValueError("Browser result provenance mismatch")
    steps = []
    previous_index = -1
    expected = expected_steps()
    for step in value["steps"]:
        key = tuple(step[field] for field in ["engine", "theme", "layout", "name"])
        if key not in expected or step.get("status") not in ["passed", "failed"]:
            raise ValueError("Unexpected partial browser step")
        index = expected.index(key)
        if index <= previous_index:
            raise ValueError("Duplicate or out-of-order partial browser step")
        previous_index = index
        elapsed = step["elapsedSeconds"]
        if type(elapsed) not in [float, int] or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("Invalid partial browser duration")
        row = dict(zip(["engine", "theme", "layout", "name"], key))
        row.update({"status": step["status"], "elapsed_seconds": elapsed})
        if step["status"] == "failed":
            error = step.get("error", "")
            row["failure_category"] = next((label for marker, label in [
                ("page.screenshot", "screenshot"), ("page.goto", "navigation"),
                ("waitForURL", "login_navigation"), ("Accessibility", "accessibility"),
                ("Surface review", "surface"), ("Timeout", "timeout"),
            ] if marker in error), "assertion_or_other")
        steps.append(row)
    return {"executed": len(steps), "passed": sum(s["status"] == "passed" for s in steps),
            "failed": sum(s["status"] == "failed" for s in steps), "omitted": 33 - len(steps),
            "steps": steps, "summary_available": "summary" in value}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_private(path: Path, value: object) -> None:
    with path.open("x") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    path.chmod(0o600)


def integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 10000:
        raise ValueError("Invalid bounded result counter")
    return value


def expected_steps() -> list[tuple[str, str, str, str]]:
    result = []
    for engine in ["chromium", "firefox", "webkit"]:
        for theme in ["light", "dark"]:
            names = ["real local cookie login", "route /settings/access", CHECKS[1], CHECKS[2]]
            if theme == "dark":
                names.append(CHECKS[3])
            result.extend((engine, theme, "desktop", name) for name in names)
            result.append((engine, theme, "mobile", "mobile route /settings/access"))
    return result


def validate_browser(value: dict, directory: Path) -> dict:
    summary = value["summary"]
    if value["sourceSha"] != SOURCE or value["version"] != "2.1.0":
        raise ValueError("Browser source/version mismatch")
    if value["runtime"]["playwright"] != "1.63.0" or set(value["runtime"]["engineVersions"]) != {"chromium", "firefox", "webkit"}:
        raise ValueError("Incomplete pinned engine execution")
    for key in ["executedChecks", "expectedCheckCount", "passed", "failed", "pageErrors", "consoleErrors",
                "apiServerErrors", "blockedExternalRequests", "createdSyntheticResources", "cleanupFailures"]:
        integer(summary[key])
    steps = value["steps"]
    actual = [(step["engine"], step["theme"], step["layout"], step["name"]) for step in steps]
    if actual != expected_steps() or summary["executedChecks"] != 33 or summary["expectedCheckCount"] != 33:
        raise ValueError("Native workflow omissions or order mismatch")
    if summary["expectedCountMatched"] is not True or summary["passed"] != 33 or summary["failed"] != 0:
        raise ValueError("Native workflow failure")
    for key in ["pageErrors", "consoleErrors", "apiServerErrors", "blockedExternalRequests", "createdSyntheticResources"]:
        if summary[key] != len(value[key]) or summary[key] != 0:
            raise ValueError("Unexpected listener/resource event")
    if value["status"] != "passed" or summary["cleanupFailures"] != 0:
        raise ValueError("Browser or UI cleanup failure")
    if any(step["status"] != "passed" or not isinstance(step["elapsedSeconds"], (int, float))
           or isinstance(step["elapsedSeconds"], bool) or not math.isfinite(step["elapsedSeconds"])
           or step["elapsedSeconds"] < 0 for step in steps):
        raise ValueError("Invalid successful workflow record")
    audits, captures = [], []

    def walk(item: object) -> None:
        if isinstance(item, dict):
            if "axeViolations" in item:
                if item["axeViolations"] != []:
                    raise ValueError("Accessibility violations")
                audits.append(item["axeScope"])
            if "screenshot" in item:
                name = item["screenshot"]
                if not isinstance(name, str) or re.fullmatch(r"[a-zA-Z0-9-]+\.png", name) is None:
                    raise ValueError("Unsafe screenshot path")
                file = directory / name
                if file.is_symlink():
                    raise ValueError("Screenshot symlink")
                data = file.read_bytes()
                if not 24 <= len(data) <= 8 * 1024**2 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                    raise ValueError("Invalid screenshot artifact")
                width, height = struct.unpack(">II", data[16:24])
                if width not in [1440, 390] or not 1 <= height <= 20000:
                    raise ValueError("Unexpected screenshot geometry")
                captures.append({"label": name, "width": width, "height": height,
                                 "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(steps)
    if len(audits) != 33 or len(captures) != 33 or len({c["label"] for c in captures}) != 33:
        raise ValueError("Incomplete capture/accessibility surfaces")
    return {"executed": 33, "passed": 33, "failed": 0, "axe_surfaces": len(audits),
            "axe_violations": 0, "screenshots": captures,
            "summary": {key: summary[key] for key in ["executedChecks", "expectedCheckCount", "passed", "failed", "pageErrors", "consoleErrors", "apiServerErrors", "blockedExternalRequests", "createdSyntheticResources", "cleanupFailures"]},
            "engine_versions": value["runtime"]["engineVersions"],
            "steps": [{key: step[key] for key in ["engine", "theme", "layout", "name", "status", "elapsedSeconds"]} for step in steps]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.source_revision != SOURCE:
        raise ValueError("This retained qualification requires its frozen source")
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    env = {key: os.environ[key] for key in ["PATH", "HOME"] if key in os.environ}
    env.update({"LANG": "C.UTF-8", "DOCKER_HOST": "unix:///var/run/docker.sock", "NODE_OPTIONS": "--max-old-space-size=640"})
    owner = secrets.token_hex(10)
    project = "threatlens-native-" + owner
    builder = project + "-builder"
    builder_container = "buildx_buildkit_" + builder + "0"
    browser_name = project + "-browser"
    source = evidence / "source"
    tags = {kind: project + "-" + kind + ":2.1.0" for kind in ["backend", "web"]}
    record = {"schema": "threatlens-hosted-native-ui-v1", "status": "failed", "application_revision": SOURCE,
              "profile": "Fresh isolated full native stack and unchanged 33 UI workflows",
              "started_epoch": time.time(), "predeclared_browser_attempts": 1, "browser_attempted": False,
              "limits": {"build_cpu": 1, "build_memory_bytes": 1536 * 1024**2, "build_swap": False,
                         "build_parallelism": 1, "build_seconds_each": 1200, "browser_cpus": 2,
                         "browser_memory_bytes": 3 * 1024**3, "browser_memory_swap_bytes": 3 * 1024**3,
                         "browser_pids": 768, "browser_shm_bytes": 256 * 1024**2,
                         "action_ms": 30000, "assertion_ms": 5000, "browser_seconds": 900},
              "stages": [], "cleanup": {"status": "not_started"}, "failures": []}
    deadline = time.monotonic() + 3600
    compose = []
    built_images = {}
    builder_attempted = False
    cleanup_deadline = None

    def raw(command: list[str], timeout: float = 30, input_bytes: bytes | None = None) -> bytes:
        if cleanup_deadline is not None:
            timeout = min(timeout, cleanup_deadline - time.monotonic())
            if timeout <= 0:
                raise TimeoutError("Owned cleanup deadline expired")
        result = subprocess.run(command, cwd=ROOT, env=env, input=input_bytes,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)
        if len(result.stdout) > 32 * 1024**2:
            raise ValueError("Bounded command output exceeded")
        return result.stdout

    def docker_inspect(identifier: str) -> dict:
        result = json.loads(raw(["docker", "inspect", identifier]))
        if not isinstance(result, list) or len(result) != 1:
            raise ValueError("Ambiguous Docker identity")
        return result[0]

    def run(name: str, command: list[str], seconds: int, input_bytes: bytes | None = None,
            *, allow_failure: bool = False, binary_output: bool = False) -> bytes:
        if time.monotonic() + seconds > deadline:
            raise TimeoutError("Full original stage budget unavailable")
        started = time.monotonic()
        row = {"name": name, "original_exit": None, "status": "failed"}
        record["stages"].append(row)
        log = evidence / (name + ".log")
        output = evidence / (name + ".stdout")
        errors = evidence / (name + ".stderr")
        try:
            with output.open("xb") as stdout, errors.open("xb") as stderr:
                result = subprocess.run(command, cwd=source if source.exists() else ROOT, env=env,
                                        input=input_bytes, stdout=stdout, stderr=stderr, timeout=seconds)
            row["original_exit"] = result.returncode
            if output.stat().st_size > (64 if binary_output else 32) * 1024**2 or errors.stat().st_size > 8 * 1024**2:
                raise ValueError("Bounded stage output exceeded")
            with log.open("xb") as handle:
                if not binary_output:
                    handle.write(output.read_bytes())
                handle.write(errors.read_bytes())
            log.chmod(0o600)
            row["log_sha256"] = digest(log)
            if result.returncode and not allow_failure:
                raise RuntimeError("Original command failed")
            row["status"] = "passed" if result.returncode == 0 else "failed"
            return output.read_bytes()
        except subprocess.TimeoutExpired:
            row["deadline_expired"] = True
            with log.open("xb") as handle:
                if not binary_output:
                    with output.open("rb") as stdout:
                        handle.write(stdout.read(32 * 1024**2))
                with errors.open("rb") as stderr:
                    handle.write(stderr.read(8 * 1024**2))
            log.chmod(0o600)
            row["log_sha256"] = digest(log)
            raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)

    def quiet_window() -> None:
        if time.monotonic() + 600 + 900 > deadline:
            raise TimeoutError("Full environment and browser budgets unavailable")
        started = time.monotonic()
        quiet_start = None
        samples = []
        record["host_gate"] = {"status": "failed", "deadline_seconds": 600,
                               "continuous_quiet_seconds": 60, "sample_interval_seconds": 15,
                               "minimum_available_memory_bytes": 3 * 1024**3,
                               "maximum_avg10": {"cpu_some": 25, "memory_some": 1,
                                                  "memory_full": .5, "io_some": 5, "io_full": 1},
                               "samples": samples}
        while time.monotonic() - started <= 600:
            sample_started = time.monotonic()
            try:
                meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
                sample = {"elapsed_seconds": round(sample_started - started, 3),
                          "available_memory_bytes": int(meminfo["MemAvailable"].split()[0]) * 1024,
                          **{key: pressure(Path("/proc/pressure") / key) for key in ["cpu", "memory", "io"]}}
                docker_started = time.monotonic()
                raw(["docker", "info", "--format", "{{.ServerVersion}}"], 5)
                sample["docker_seconds"] = round(time.monotonic() - docker_started, 6)
                sample["eligible"] = eligible_host(sample)
            except Exception as error:
                sample = {"elapsed_seconds": round(sample_started - started, 3), "eligible": False,
                          "error_type": type(error).__name__}
            samples.append(sample)
            now = time.monotonic()
            if not sample["eligible"] or now - started > 600:
                quiet_start = None
            elif quiet_start is None:
                quiet_start = now
            elif now - quiet_start >= 60:
                record["host_gate"].update({"status": "passed", "observed_quiet_seconds": round(now - quiet_start, 3)})
                return
            remaining = 600 - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(max(0, 15 - (time.monotonic() - sample_started)), remaining))
        raise RuntimeError("Host never satisfied unchanged quiet/headroom gate")

    def services() -> dict:
        ids = raw(["docker", "ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=" + project]).decode().splitlines()
        result = {}
        for identifier in ids:
            container = docker_inspect(identifier)
            labels = container["Config"]["Labels"]
            if labels.get("com.docker.compose.project") != project:
                raise ValueError("Runtime ownership mismatch")
            service = labels.get("com.docker.compose.service")
            if service not in CAPS or service in result:
                raise ValueError("Unexpected/duplicate owned service")
            result[service] = container
        return result

    def snapshot() -> dict:
        items = services()
        if set(items) != set(CAPS):
            raise ValueError("Incomplete native stack")
        result = {}
        for name, container in items.items():
            state, host = container["State"], container["HostConfig"]
            cpu, mib, pids = CAPS[name]
            expected = built_images["web"] if name == "web" else database_id if name == "db" else redis_id if name == "redis" else built_images["backend"]
            if container["Image"] != expected or state["OOMKilled"] is not False or container["RestartCount"] != 0:
                raise ValueError("Runtime image/restart/OOM mismatch")
            if state["Running"] is not (name != "migrate") or state["ExitCode"] != 0:
                raise ValueError("Unexpected native runtime state")
            health = state.get("Health", {}).get("Status")
            if health != (None if name in ["migrate", "review-source"] else "healthy"):
                raise ValueError("Unhealthy native service")
            if (host["NanoCpus"], host["Memory"], host["MemorySwap"], host["PidsLimit"], host["ReadonlyRootfs"]) != (int(cpu * 1e9), mib * 1024**2, mib * 1024**2, pids, True):
                raise ValueError("Actual native caps mismatch")
            result[name] = {"image_id": container["Image"], "running": state["Running"], "health": health,
                            "restarts": container["RestartCount"], "oom_killed": state["OOMKilled"],
                            "read_only": True, "cpu_nanos": host["NanoCpus"], "memory_bytes": host["Memory"],
                            "memory_swap_bytes": host["MemorySwap"], "pids_limit": host["PidsLimit"]}
        return result

    try:
        record["workflow_helper_revision"] = raw(["git", "rev-parse", "HEAD"]).decode().strip()
        record["image_source_groups"] = []
        for group in IMAGE_GROUPS:
            left = raw(["git", "rev-parse", SOURCE + ":" + group]).decode().strip()
            right = raw(["git", "rev-parse", "HEAD:" + group]).decode().strip()
            if left != right:
                raise ValueError("Application/image source group changed")
            record["image_source_groups"].append({"path": group, "tested_object": left, "workflow_object": right, "identical": True})
        record["helpers"] = {name: digest(ROOT / "scripts/operations" / name) for name in ["run_native_ui_qualification.py", "native_ui_config.py", "native_ui_review.mjs", "seed_native_ui_review.py"]}
        if record["helpers"]["native_ui_review.mjs"] != DRIVER_SHA:
            raise ValueError("Original browser workload changed")
        archive = run("source-archive", ["git", "archive", SOURCE], 60, binary_output=True)
        source.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            handle.extractall(source, filter="data")
        if (source / "backend/uv.lock").exists() or (source / "backups").exists() or list(source.rglob(".env")):
            raise ValueError("Unexpected archive inputs")
        if digest(source / "web/package-lock.json") != LOCK_SHA:
            raise ValueError("Runtime dependency lock mismatch")
        if raw(["node", "--version"]).decode().strip() != "v22.23.0":
            raise ValueError("Pinned installation Node missing")
        run("npm-ci", ["npm", "--prefix", str(source / "web"), "ci", "--ignore-scripts"], 300)
        record["installed_dependencies"] = {}
        for package, version in [("playwright", "1.63.0"), ("@playwright/test", "1.63.0"), ("@axe-core/playwright", "4.13.0")]:
            actual = json.loads((source / "web/node_modules" / package / "package.json").read_text())["version"]
            if actual != version:
                raise ValueError("Installed browser dependency differs from source lock")
            record["installed_dependencies"][package] = actual
        env.update({"COMPOSE_PROJECT_NAME": project, "ADMIN_EMAIL": "native-review@example.test",
                    "ADMIN_PASSWORD": secrets.token_urlsafe(32), "APP_ENV": "development", "AI_ENABLED": "true",
                    "ALLOW_PRIVATE_NETWORK_FETCH": "true", "AUTH_COOKIE_SECURE": "false", "SEED_ADMIN_ON_STARTUP": "true",
                    "AI_API_KEY": "", "MCP_ENABLED": "false", "MCP_OAUTH_ENABLED": "false",
                    "WORKER_CONCURRENCY": "1", "NOTIFICATION_WORKER_CONCURRENCY": "1",
                    "THREATLENS_CSP_FRAME_SRC": "'self' http://review-source:8765"})
        run("bootstrap", ["bash", str(source / "bootstrap.sh"), "--force"], 180)
        buildkit = evidence / "buildkit.toml"
        buildkit.write_text("[worker.oci]\nmax-parallelism = 1\n")
        builder_attempted = True
        run("builder-start", ["docker", "buildx", "create", "--name", builder, "--driver", "docker-container",
                              "--driver-opt", "memory=1536m", "--driver-opt", "memory-swap=1536m",
                              "--driver-opt", "cpu-period=100000", "--driver-opt", "cpu-quota=100000",
                              "--buildkitd-config", str(buildkit), "--bootstrap", "unix:///var/run/docker.sock"], 120)
        host = docker_inspect(builder_container)["HostConfig"]
        if (host["Memory"], host["MemorySwap"], host["CpuPeriod"], host["CpuQuota"]) != (1536 * 1024**2, 1536 * 1024**2, 100000, 100000):
            raise ValueError("Build resource guard mismatch")
        record["actual_builder_limits"] = {key: host[key] for key in ["Memory", "MemorySwap", "CpuPeriod", "CpuQuota"]}
        for kind in ["backend", "web"]:
            run("build-" + kind, ["docker", "buildx", "build", "--builder", builder, "--platform", "linux/amd64", "--load",
                                  "--file", str(source / "docker" / (kind + ".Dockerfile")), "--tag", tags[kind],
                                  "--build-arg", "VCS_REF=" + SOURCE, "--build-arg", "APP_VERSION=2.1.0", str(source / kind)], 1200)
            image = docker_inspect(tags[kind])
            labels = image["Config"]["Labels"]
            if labels["org.opencontainers.image.revision"] != SOURCE or labels["org.opencontainers.image.version"] != "2.1.0" or image["Config"]["User"] in ["", "0", "root"]:
                raise ValueError("Image source/version/user mismatch")
            built_images[kind] = image["Id"]
        record["production_image_ids"] = dict(built_images)
        run("builder-remove", ["docker", "buildx", "rm", "--force", builder], 60)
        builder_attempted = False
        for name, image in [("postgres", POSTGRES), ("redis", REDIS), ("playwright", PLAYWRIGHT)]:
            run("pull-" + name, ["docker", "pull", image], 180)
            actual = docker_inspect(image)
            if actual["Os"] != "linux" or actual["Architecture"] != "amd64" or image not in actual["RepoDigests"]:
                raise ValueError("Pinned image platform/digest mismatch")
            record[name + "_image"] = {"requested": image, "actual_id": actual["Id"], "repo_digests": actual["RepoDigests"]}
        database_id = record["postgres_image"]["actual_id"]
        redis_id = record["redis_image"]["actual_id"]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = "http://127.0.0.1:" + str(port)
        env.update({"PUBLIC_APP_URL": base_url, "CORS_ORIGINS": base_url, "API_DATABASE_POOL_SIZE": "2", "API_DATABASE_MAX_OVERFLOW": "0"})
        base_file = source / "docker-compose.yml"
        original = base_file.read_text()
        old_port = '"${THREATLENS_WEB_PORT:-3000}:3000"'
        if original.count(old_port) != 1:
            raise ValueError("Unexpected Compose port recipe")
        base_file.write_text(original.replace(old_port, '"127.0.0.1:' + str(port) + ':3000"'))
        publisher = evidence / "publisher"
        publisher.mkdir(mode=0o755)
        (publisher / "article.html").write_text("<!doctype html><html><head><title>Synthetic defensive intelligence</title></head><body><h1>Synthetic defensive intelligence</h1><p>Owned disposable publisher fixture.</p></body></html>")
        (publisher / "article.html").chmod(0o644)
        overrides = {"services": {}}
        for service, (cpu, mib, pids) in CAPS.items():
            row = {"cpus": str(cpu), "mem_limit": str(mib) + "m", "memswap_limit": str(mib) + "m", "pids_limit": pids}
            if service not in ["db", "redis", "web"]:
                row.update({"image": tags["backend"], "pull_policy": "never"})
            elif service == "web":
                row.update({"image": tags["web"], "pull_policy": "never"})
            else:
                row.update({"image": POSTGRES if service == "db" else REDIS, "pull_policy": "never"})
            if service == "review-source":
                row.update({"read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                            "command": ["python", "-m", "http.server", "8765", "--directory", "/srv/review"],
                            "volumes": [str(publisher) + ":/srv/review:ro"], "networks": ["backplane"]})
            overrides["services"][service] = row
        override = evidence / "compose.override.json"
        write_private(override, overrides)
        compose = ["docker", "compose", "--project-directory", str(source), "--env-file", str(source / ".env"),
                   "--project-name", project, "--file", str(base_file), "--file", str(override)]
        run("compose-config", [*compose, "config", "--quiet"], 60)
        run("stack-bootstrap", [*compose, "up", "--no-build", "--detach", "--wait", "--wait-timeout", "240", "db", "redis", "migrate", "api", "web", "review-source"], 300)
        seed_code = (ROOT / "scripts/operations/seed_native_ui_review.py").read_bytes()
        seed = json.loads(run("seed", [*compose, "exec", "--no-TTY", "--interactive", "-e", "REVIEW_DISPOSABLE_DATABASE=1", "-e", "REVIEW_SOURCE_SHA=" + SOURCE,
                                      "-e", "REVIEW_PUBLISHER_ORIGIN=http://review-source:8765", "api", "python", "-"], 90, seed_code))
        if seed.get("source_revision") != SOURCE or seed.get("synthetic") is not True or seed.get("schema") != "threatlens-native-ui-seed-v1":
            raise ValueError("Seed provenance invalid")
        for key in ["team_id", "feed_id", "item_id"]:
            if str(uuid.UUID(seed[key])) != seed[key]:
                raise ValueError("Seed identifier invalid")
        if seed.get("counts") != {"teams": 1, "groups": 1, "memberships": 1, "feeds": 1, "items": 1, "articles": 1, "classifications": 1} or seed.get("attention_queue_rows") != 0:
            raise ValueError("Seed scope invalid")
        record["seed"] = {"source_revision": SOURCE, "synthetic": True, "counts": seed["counts"], "attention_queue_rows": 0}
        background_started = time.monotonic()
        run("background-start", [*compose, "up", "--no-build", "--detach", "--wait", "--wait-timeout", "240", *BACKGROUND], 300)
        record["runtime_before"] = snapshot()
        opener = build_opener(ProxyHandler({}))
        contract = json.loads((source / "docs/reference/openapi.json").read_text())
        record["http_checks"] = []
        record["readiness_admission"] = {"startup_deadline_seconds": 240, "read_seconds_max": 15,
                                         "poll_seconds": 5, "attempts": [], "status": "failed"}
        def read_ready(timeout):
            request = Request(base_url + "/api/v1/health/ready", headers={"Accept": "application/json"})
            try:
                response = opener.open(request, timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                if response.geturl() != request.full_url:
                    raise ValueError("Readiness redirect")
                return response.code, response.headers.get_content_type(), response.read(2000001)
        wait_readiness(read_ready, background_started + 240, record["readiness_admission"]["attempts"])
        record["readiness_admission"]["status"] = "passed"
        record["http_checks"].append({"path": "/api/v1/health/ready", "status": 200, "semantic_check": True})
        with opener.open(Request(base_url + "/api/openapi.json", headers={"Accept": "application/json"}), timeout=15) as response:
            body = response.read(2000001)
            if response.status != 200 or response.headers.get_content_type() != "application/json" or len(body) > 2000000 or response.geturl() != base_url + "/api/openapi.json":
                raise ValueError("Contract response invalid")
            if json.loads(body) != contract:
                raise ValueError("Contract semantic mismatch")
        record["http_checks"].append({"path": "/api/openapi.json", "status": 200, "semantic_check": True})
        quiet_window()
        fixture = services()["review-source"]
        networks = fixture["NetworkSettings"]["Networks"]
        if len(networks) != 1:
            raise ValueError("Unexpected owned publisher network")
        fixture_ip = next(iter(networks.values()))["IPAddress"]
        if re.fullmatch(r"[0-9.]+", fixture_ip) is None:
            raise ValueError("Publisher address invalid")
        artifacts = evidence / "browser"
        state = evidence / "state"
        artifacts.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        (artifacts / "review.mjs").write_bytes((ROOT / "scripts/operations/native_ui_review.mjs").read_bytes())
        write_private(artifacts / "config.json", build_config(base_url, "http://review-source:8765", SOURCE, built_images))
        write_private(state / "seed.json", seed)
        credentials = state / "fixture.env"
        with credentials.open("x") as handle:
            handle.write("ADMIN_EMAIL=" + env["ADMIN_EMAIL"] + "\nADMIN_PASSWORD=" + env["ADMIN_PASSWORD"] + "\n")
        credentials.chmod(0o600)
        record["browser_container_creation_attempted"] = True
        run("browser-create", ["docker", "create", "--name", browser_name, "--label", "threatlens.native.ui=" + owner,
                                "--user", str(os.getuid()) + ":" + str(os.getgid()), "--network", "host", "--cpus", "2",
                                "--memory", "3g", "--memory-swap", "3g", "--pids-limit", "768", "--shm-size", "256m",
                                "--volume", str(source / "web") + ":/workspace:ro", "--volume", str(artifacts) + ":/artifacts:rw",
                                "--volume", str(state) + ":/review-state:ro", "--add-host", "review-source:" + fixture_ip,
                                "--env", "HOME=/tmp", "--env", "REVIEW_ARTIFACT_ROOT=/artifacts", "--env", "REVIEW_SOURCE_SHA=" + SOURCE,
                                "--env", "REVIEW_CHECKS=" + ",".join(CHECKS), "--env", "REVIEW_EXPECTED_CHECKS=33", PLAYWRIGHT, "node", "/artifacts/review.mjs"], 60)
        browser = docker_inspect(browser_name)
        host = browser["HostConfig"]
        if browser["Image"] != record["playwright_image"]["actual_id"] or browser["Config"]["Labels"]["threatlens.native.ui"] != owner:
            raise ValueError("Browser ownership/image mismatch")
        if (host["NanoCpus"], host["Memory"], host["MemorySwap"], host["PidsLimit"], host["ShmSize"]) != (2000000000, 3 * 1024**3, 3 * 1024**3, 768, 256 * 1024**2):
            raise ValueError("Browser cap mismatch")
        record["browser_attempted"] = True
        run("browser", ["docker", "start", "--attach", browser_name], 900, allow_failure=True)
        terminal = docker_inspect(browser_name)
        record["browser_terminal"] = {"running": terminal["State"]["Running"], "exit_code": terminal["State"]["ExitCode"],
                                      "oom_killed": terminal["State"]["OOMKilled"], "restarts": terminal["RestartCount"]}
        results = list(artifacts.glob("run-*/results.json"))
        if len(results) != 1:
            raise ValueError("Ambiguous browser result")
        value = json.loads(results[0].read_text())
        record["browser"] = browser_projection(value)
        record["browser_result_sha256"] = digest(results[0])
        if record["stages"][-1]["original_exit"] != 0 or terminal["State"]["Running"] is not False or terminal["State"]["ExitCode"] != 0 or terminal["State"]["OOMKilled"] is not False or terminal["RestartCount"] != 0:
            raise ValueError("Browser original exit/terminal failure")
        record["browser"] = validate_browser(value, results[0].parent)
        record["runtime_after"] = snapshot()
        record["status"] = "passed"
    except Exception as error:
        record["failures"].append({"phase": record["stages"][-1]["name"] if record["stages"] else "preflight", "error_type": type(error).__name__})
        if record["browser_attempted"]:
            try:
                terminal = docker_inspect(browser_name)
                record["browser_terminal"] = {"running": terminal["State"]["Running"], "exit_code": terminal["State"]["ExitCode"],
                                              "oom_killed": terminal["State"]["OOMKilled"], "restarts": terminal["RestartCount"]}
                results = list((evidence / "browser").glob("run-*/results.json"))
                if len(results) == 1:
                    record.setdefault("browser", browser_projection(json.loads(results[0].read_text())))
                    record["browser_result_sha256"] = digest(results[0])
            except Exception as diagnostic_error:
                record["failures"].append({"phase": "failure_evidence", "error_type": type(diagnostic_error).__name__})
        if compose:
            try:
                with (evidence / "owned-stack-failure.log").open("xb") as output:
                    subprocess.run([*compose, "logs", "--no-color", "--tail", "80"], cwd=source, env=env,
                                   stdout=output, stderr=output, timeout=20, check=True)
            except Exception as diagnostic_error:
                record["failures"].append({"phase": "owned_stack_log", "error_type": type(diagnostic_error).__name__})
    finally:
        cleanup_deadline = time.monotonic() + 300
        cleanup_errors = []

        def clean(phase, action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append({"phase": phase, "error_type": type(error).__name__})

        def remove_browsers():
            ids = raw(["docker", "ps", "--all", "--quiet", "--filter", "label=threatlens.native.ui=" + owner]).decode().splitlines()
            for identifier in ids:
                item = docker_inspect(identifier)
                if item["Name"] != "/" + browser_name or item["Config"]["Labels"].get("threatlens.native.ui") != owner:
                    raise ValueError("Foreign browser cleanup target")
                raw(["docker", "rm", "--force", identifier])

        def remove_stack():
            if compose:
                services()
                raw([*compose, "down", "--volumes", "--remove-orphans", "--timeout", "60"], 180)

        def remove_builder():
            if builder_attempted:
                ids = raw(["docker", "ps", "--all", "--quiet", "--filter", "name=^/" + builder_container + "$"]).decode().splitlines()
                for identifier in ids:
                    item = docker_inspect(identifier)
                    if item["Name"] != "/" + builder_container:
                        raise ValueError("Foreign builder cleanup target")
                names = raw(["docker", "buildx", "ls", "--format", "{{.Name}}"], 30).decode().splitlines()
                if builder in names:
                    raw(["docker", "buildx", "rm", "--force", builder], 60)

        def check_remnants():
            remaining = raw(["docker", "ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=" + project]).decode().splitlines()
            browsers = raw(["docker", "ps", "--all", "--quiet", "--filter", "label=threatlens.native.ui=" + owner]).decode().splitlines()
            volumes = raw(["docker", "volume", "ls", "--quiet", "--filter", "label=com.docker.compose.project=" + project]).decode().splitlines()
            builder_containers = raw(["docker", "ps", "--all", "--quiet", "--filter", "name=^/" + builder_container + "$"]).decode().splitlines()
            builder_volumes = raw(["docker", "volume", "ls", "--quiet", "--filter", "name=^" + builder_container + "_state$"]).decode().splitlines()
            if remaining or browsers or volumes or builder_containers or builder_volumes:
                raise ValueError("Incomplete owned cleanup")
        for phase, action in [("browser", remove_browsers), ("stack", remove_stack), ("builder", remove_builder), ("remnants", check_remnants)]:
            clean(phase, action)
        record["cleanup"] = ({"status": "failed", "errors": cleanup_errors} if cleanup_errors else
                             {"status": "passed", "remaining_containers": 0, "remaining_volumes": 0})
        cleanup_deadline = None
        if cleanup_errors:
            record["status"] = "failed"
        try:
            archive = evidence / "private-evidence.tar"
            allowed = [path for pattern in ["*.log", "browser/config.json", "browser/run-*/results.json", "browser/run-*/*.png", "state/seed.json"]
                       for path in evidence.glob(pattern)]
            if sum(path.stat().st_size for path in allowed) > 64 * 1024**2:
                raise ValueError("Private artifact input bound exceeded")
            with tarfile.open(archive, "x") as handle:
                for path in allowed:
                    if path.is_symlink() or not path.is_file():
                        raise ValueError("Private artifact contains unsafe file")
                    handle.add(path, arcname=str(path.relative_to(evidence)), recursive=False)
            if archive.stat().st_size > 65 * 1024**2:
                raise ValueError("Private archive bound exceeded")
            ciphertext = evidence / "private-evidence.cms"
            result = subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-in", str(archive),
                                     "-out", str(ciphertext), "-outform", "DER", str(CERTIFICATE)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
            record["private_artifact"] = {"encryption_exit": result.returncode, "bytes": ciphertext.stat().st_size,
                                          "sha256": digest(ciphertext), "certificate_sha256": digest(CERTIFICATE)}
            archive.unlink()
        except Exception as error:
            record["status"] = "failed"
            record["failures"].append({"phase": "private_artifact", "error_type": type(error).__name__})
        record["finished_epoch"] = time.time()
        write_private(evidence / "public-projection.json", record)
        print(json.dumps({"status": record["status"], "browser_attempted": record["browser_attempted"],
                          "cleanup": record["cleanup"], "public_projection_sha256": digest(evidence / "public-projection.json")}))
    raise SystemExit(0 if record["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
