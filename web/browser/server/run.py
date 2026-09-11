#!/usr/bin/env python3
"""Run browser authentication checks against disposable, real application services."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener
import uuid


ROOT = Path(__file__).resolve().parents[3]
PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v1.63.0-noble"


def clean_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "TMPDIR", "LANG", "TZ", "CI", "XDG_RUNTIME_DIR"}
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed or key.startswith(("DOCKER_", "LC_"))
    }


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, timeout=120).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docker-browser",
        action="store_true",
        help="Run pinned Playwright image with host networking (Linux).",
    )
    parser.add_argument("--project", choices=("chromium", "firefox", "webkit"))
    parser.add_argument("--grep")
    parser.add_argument(
        "--ai-providers", action="store_true",
        help="Enable AI settings in the disposable server for provider configuration tests; no AI worker is started.",
    )
    args = parser.parse_args()
    for name in (
        "THREATLENS_TEST_DATABASE_URL",
        "THREATLENS_TEST_REDIS_URL",
        "THREATLENS_BROWSER_BASE_URL",
    ):
        if os.environ.get(name):
            parser.error(f"unset {name}; this launcher always creates its own services")
    run_id = uuid.uuid4().hex
    containers: list[str] = []
    processes: list[subprocess.Popen] = []
    env = clean_environment()
    local_http = build_opener(ProxyHandler({}))
    try:
        with tempfile.TemporaryDirectory(prefix="threatlens-browser-") as temporary:
            database_password = secrets.token_urlsafe(24)
            for service, image, port, options in (
                (
                    "postgres",
                    "postgres:16",
                    "5432",
                    [
                        "-e",
                        "POSTGRES_DB=browser",
                        "-e",
                        "POSTGRES_USER=browser",
                        "-e",
                        f"POSTGRES_PASSWORD={database_password}",
                    ],
                ),
                ("redis", "redis:7-alpine", "6379", []),
            ):
                name = f"threatlens-browser-{run_id}-{service}"
                containers.append(name)
                docker(
                    "run",
                    "--rm",
                    "-d",
                    "--name",
                    name,
                    "--label",
                    f"threatlens.browser.run={run_id}",
                    "-p",
                    f"127.0.0.1::{port}",
                    *options,
                    image,
                )
            pg_port = docker("port", containers[0], "5432/tcp").rsplit(":", 1)[1]
            redis_port = docker("port", containers[1], "6379/tcp").rsplit(":", 1)[1]
            api_port, web_port = free_port(), free_port()
            while web_port == api_port:
                web_port = free_port()
            api_origin = f"http://127.0.0.1:{api_port}"
            web_origin = f"http://127.0.0.1:{web_port}"
            control_token = secrets.token_urlsafe(32)
            browser_env = {
                **env,
                "THREATLENS_BROWSER_BASE_URL": web_origin,
                "THREATLENS_BROWSER_API_ORIGIN": api_origin,
                "THREATLENS_BROWSER_CONTROL_TOKEN": control_token,
                "THREATLENS_BROWSER_ENV_DIR": temporary,
                "THREATLENS_BROWSER_AI_PROVIDERS": "true" if args.ai_providers else "false",
            }
            server_env = {
                **browser_env,
                "TMPDIR": temporary,
                "PYTHONPATH": str(ROOT / "backend"),
                "APP_ENV": "test",
                "DATABASE_URL": f"postgresql+psycopg://browser:{database_password}@127.0.0.1:{pg_port}/browser",
                "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
                "JWT_SECRET": secrets.token_urlsafe(48),
                "APP_DATA_ENCRYPTION_KEY": secrets.token_urlsafe(48),
                "REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY": "true",
                "PUBLIC_APP_URL": web_origin,
                "CORS_ORIGINS": web_origin,
                "AUTH_COOKIE_SECURE": "false",
                "AUTH_COOKIE_SAMESITE": "lax",
                "ALLOW_PRIVATE_NETWORK_OIDC": "true",
                "ALLOW_INSECURE_HTTP_OIDC": "true",
                "AI_ENABLED": "true" if args.ai_providers else "false",
                "ALLOW_SELF_REGISTRATION": "false",
                "OIDC_TOTAL_TIMEOUT_SECONDS": "3",
            }
            log_path = ROOT / "web/browser-server.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w") as log:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("fixture_server.py")),
                        "--port",
                        str(api_port),
                    ],
                    cwd=temporary,
                    env=server_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                processes.append(server)
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError(f"Browser server exited; inspect {log_path}")
                    try:
                        with local_http.open(
                            f"{api_origin}/__browser__/ready", timeout=1
                        ) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise TimeoutError(
                        f"Browser server startup timed out; inspect {log_path}"
                    )
                command = ["npm", "run", "test:browser:server", "--"]
                if args.project:
                    command += ["--project", args.project]
                if args.grep:
                    command += ["--grep", args.grep]
                if args.docker_browser:
                    container_name = f"threatlens-browser-{run_id}-playwright"
                    containers.append(container_name)
                    docker_command = [
                        "docker",
                        "run",
                        "--rm",
                        "--name",
                        container_name,
                        "--label",
                        f"threatlens.browser.run={run_id}",
                        "--network",
                        "host",
                        "--ipc",
                        "host",
                        "--user",
                        f"{os.getuid()}:{os.getgid()}",
                        "--mount",
                        f"type=bind,src={ROOT / 'web'},dst={ROOT / 'web'}",
                        "--mount",
                        f"type=bind,src={temporary},dst={temporary}",
                        "-w",
                        str(ROOT / "web"),
                    ]
                    for key in browser_env:
                        if key.startswith("THREATLENS_BROWSER_") or key == "CI":
                            docker_command += ["-e", key]
                    command = docker_command + [PLAYWRIGHT_IMAGE] + command
                browser = subprocess.Popen(
                    command, cwd=ROOT / "web", env=browser_env, start_new_session=True
                )
                processes.append(browser)
                return browser.wait(timeout=600)
    finally:
        for process in reversed(processes):
            # Kill the owned group even if npm has exited before its children.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for name in reversed(containers):
            try:
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f"Could not remove disposable container {name}: {error}", file=sys.stderr)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    raise SystemExit(main())
