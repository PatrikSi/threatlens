#!/usr/bin/env python3
"""Qualify a disposable local nginx/API/processing/export topology, never production."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import httpx

from monitor import atomic_json, collect
from evidence import LABEL
from qualification_runtime import DisposableTopology, ROOT, clean_environment, free_port, wait_http
from qualification_workload import run_workload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--web-image", default="threatlens-web:dev")
    parser.add_argument("--max-rss-mib", type=int, default=3072)
    parser.add_argument("--max-p95-ms", type=int, default=2000)
    args = parser.parse_args()
    if not 10 <= args.duration_seconds <= 3600 or not 1 <= args.concurrency <= 16 or not 512 <= args.max_rss_mib <= 4096:
        parser.error("Duration must be 10–3600 seconds, concurrency 1–16, and RSS limit 512–4096 MiB")
    if not LABEL.fullmatch(args.target_id) or not 1 <= args.max_p95_ms <= 60000:
        parser.error("Target ID requires a bounded identifier and p95 objective must be 1–60000 ms")
    if any(os.environ.get(name) for name in ("THREATLENS_TEST_DATABASE_URL", "THREATLENS_TEST_REDIS_URL", "THREATLENS_BROWSER_BASE_URL")):
        parser.error("Unset external test URLs; this harness only creates owned disposable services")
    output = args.output.resolve()
    result = {"schema_version": 1, "kind": "local_topology_qualification", "status": "failed",
        "target_id": args.target_id, "scope": "disposable_local_only", "production_qualified": False,
        "started_at": datetime.now(timezone.utc).isoformat(), "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "platform": platform.platform(), "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT)),
        "source_binding": "current host backend source and current nginx configuration; separately running API and prefork workers",
        "limits": {"duration_seconds": args.duration_seconds, "concurrency": args.concurrency,
                   "process_rss_bytes": args.max_rss_mib * 1024**2, "latency_p95_ms": args.max_p95_ms}}
    try:
        with tempfile.TemporaryDirectory(prefix="threatlens-qualification-") as temporary:
            directory = Path(temporary)
            source = directory / "source"
            for name in ("app", "alembic"):
                shutil.copytree(ROOT / "backend" / name, source / "backend" / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copy2(ROOT / "backend/alembic.ini", source / "backend/alembic.ini")
            source_digest = hashlib.sha256()
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    source_digest.update(str(path.relative_to(source)).encode() + b"\0" + path.read_bytes())
            result["backend_source_snapshot_sha256"] = source_digest.hexdigest()
            topology = DisposableTopology(directory)
            result["run_id"] = topology.run_id
            try:
                password = secrets.token_urlsafe(24)
                postgres = topology.service("postgres", "postgres:16", "--cpus", ".5", "--memory", "512m",
                    "--pids-limit", "128", "-p", "127.0.0.1::5432", "-e", "POSTGRES_DB=qualification",
                    "-e", "POSTGRES_USER=qualification", "-e", f"POSTGRES_PASSWORD={password}")
                redis = topology.service("redis", "redis:7-alpine", "--cpus", ".25", "--memory", "128m",
                    "--pids-limit", "64", "-p", "127.0.0.1::6379")
                pg_port = topology.docker("port", postgres, "5432/tcp").rsplit(":", 1)[1]
                redis_port = topology.docker("port", redis, "6379/tcp").rsplit(":", 1)[1]
                api_port, proxy_port = free_port(), free_port()
                if api_port == proxy_port:
                    raise RuntimeError("Disposable port allocation collided; rerun qualification")
                api = f"http://127.0.0.1:{api_port}"
                public = f"http://127.0.0.1:{proxy_port}"
                control_token = secrets.token_urlsafe(32)
                env = {**clean_environment(), "PYTHONPATH": str(source / "backend"), "TMPDIR": temporary,
                    "THREATLENS_QUALIFICATION_SOURCE_ROOT": str(source),
                    "APP_ENV": "test", "DATABASE_URL": f"postgresql+psycopg://qualification:{password}@127.0.0.1:{pg_port}/qualification",
                    "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0", "JWT_SECRET": secrets.token_urlsafe(48),
                    "APP_DATA_ENCRYPTION_KEY": secrets.token_urlsafe(48), "REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY": "true",
                    "PUBLIC_APP_URL": public, "CORS_ORIGINS": public, "AUTH_COOKIE_SECURE": "false",
                    "AI_ENABLED": "false", "RUN_MIGRATIONS_ON_STARTUP": "false", "SEED_ADMIN_ON_STARTUP": "false",
                    "THREATLENS_BROWSER_API_ORIGIN": api, "THREATLENS_BROWSER_BASE_URL": public,
                    "THREATLENS_BROWSER_CONTROL_TOKEN": control_token, "ALLOW_PRIVATE_NETWORK_OIDC": "true",
                    "ALLOW_INSECURE_HTTP_OIDC": "true", "DATABASE_POOL_SIZE": "3", "DATABASE_MAX_OVERFLOW": "1"}
                topology.start("api", [sys.executable, str(Path(__file__).with_name("qualification_fixture.py")), "--port", str(api_port)], env)
                with httpx.Client(base_url=api, timeout=20, trust_env=False, headers={"x-browser-control": control_token}) as controls:
                    wait_http(controls, "/__browser__/ready")
                    def control(path: str, method: str = "POST"):
                        response = controls.request(method, f"/__browser__/{path}")
                        response.raise_for_status()
                        return response.json()
                    conf = (ROOT / "web/nginx/default.conf.template").read_text()
                    conf = conf.replace("listen 3000;", f"listen 127.0.0.1:{proxy_port};")
                    conf = conf.replace("api:8000", f"127.0.0.1:{api_port}")
                    conf = conf.replace("${THREATLENS_CSP_CONNECT_SRC}", "'self'").replace("${THREATLENS_CSP_FRAME_SRC}", "'self'")
                    config_dir = directory / "nginx"
                    config_dir.mkdir()
                    (config_dir / "default.conf").write_text(conf)
                    nginx_main = directory / "nginx-main.conf"
                    shutil.copy2(ROOT / "web/nginx/nginx.conf", nginx_main)
                    config_dir.chmod(0o755)
                    directory.chmod(0o755)
                    result["nginx_source_sha256"] = hashlib.sha256((ROOT / "web/nginx/default.conf.template").read_bytes()).hexdigest()
                    topology.service("nginx", args.web_image, "--network", "host", "--cpus", ".25", "--memory", "128m",
                        "--pids-limit", "64", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                        "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m", "--entrypoint", "nginx",
                        "--mount", f"type=bind,src={config_dir},dst=/etc/nginx/conf.d,readonly",
                        "--mount", f"type=bind,src={nginx_main},dst=/etc/nginx/nginx.conf,readonly",
                        command=["-g", "daemon off;"])
                    worker = [sys.executable, "-m", "celery", "-A", "app.tasks.celery_app", "worker", "--pool=prefork",
                              "--concurrency=1", "--without-mingle", "--without-gossip", "--loglevel=WARNING"]
                    topology.start("processing", [*worker, "--hostname=qualification-processing", "--queues=default,ingest,processing"], env)
                    topology.start("maintenance", [*worker, "--hostname=qualification-maintenance", "--queues=maintenance,lifecycle-v1"], env)
                    topology.start("notifications", [*worker, "--hostname=qualification-notifications", "--queues=notifications"], env)
                    topology.start("scheduler", [sys.executable, "-m", "celery", "-A", "app.tasks.celery_app",
                        "beat", "--schedule", str(directory / "beat-schedule"), "--loglevel=WARNING"], env)
                    def start_exports():
                        topology.start("exports", [*worker, "--hostname=qualification-exports", "--queues=exports-v1"], env)
                    with httpx.Client(base_url=public, timeout=20, trust_env=False, headers={"Origin": public}) as client:
                        wait_http(client, "/api/v1/health/live")
                        result["measurements"] = run_workload(client, control, topology, duration=args.duration_seconds,
                            concurrency=args.concurrency, start_exports=start_exports, max_rss_bytes=args.max_rss_mib * 1024**2)
                    measured = result["measurements"]
                    result["container_image_ids"] = topology.image_ids
                    monitor_config = {"schema_version": 1, "deployment": "local-qualification",
                        "compose_project": f"qualification-{topology.run_id}",
                        "services": {service: {"min_instances": 1, "memory_warning_ratio": .95}
                                     for service in ("postgres", "redis", "nginx")}}
                    result["independent_host_monitor"], _ = collect(monitor_config, {}, now=datetime.now(timezone.utc))
                    if (not result["independent_host_monitor"]["fleet_available"]
                            or result["independent_host_monitor"]["active_incidents"]):
                        raise RuntimeError("Independent container monitoring did not satisfy qualification objectives")
                    if measured["http_errors"] or measured["latency_p95_ms"] > args.max_p95_ms:
                        raise RuntimeError("Mixed workload exceeded HTTP correctness/latency objectives")
                    result["status"] = "passed"
            except Exception:
                # Preserve bounded logs for diagnosis without exposing credentials.
                for path in directory.glob("*.log"):
                    data = path.read_bytes()[-128_000:]
                    # Framework startup/errors can contain test-only DSNs. These
                    # logs stay private beside the artifact, never in stdout.
                    target = output.with_name(output.stem + f"-{path.name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    target.chmod(0o600)
                raise
            finally:
                topology.close()
    except Exception as error:
        result["failure"] = {"type": type(error).__name__, "message": str(error)[:400]}
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(output, result)
    print(json.dumps({"status": result["status"], "scope": result["scope"], "artifact": str(output)}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    raise SystemExit(main())
