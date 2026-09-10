#!/usr/bin/env python3
"""Explicit offline cutover of a bundled Compose database to split roles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ROLE_KEYS = (
    "POSTGRES_RUNTIME_USER", "POSTGRES_RUNTIME_PASSWORD",
    "POSTGRES_MIGRATION_USER", "POSTGRES_MIGRATION_PASSWORD",
)


def main() -> None:
    # Compose arguments are supplied by the operator; never source shell .env.
    command = ["docker", "compose", *sys.argv[1:]]
    config = subprocess.run(
        [*command, "config", "--format", "json"], check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/recovery/recovery_safety.py"), "validate-target"],
        input=config.stdout, check=True, capture_output=True, text=True,
    )
    document = json.loads(config.stdout)
    environment = document["services"]["db"]["environment"]
    if not all(environment.get(key) for key in ROLE_KEYS):
        raise ValueError("Configure all four runtime/migration role values before cutover")
    container_id = subprocess.run(
        [*command, "ps", "--quiet", "db"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not container_id or "\n" in container_id:
        raise ValueError("Exactly one existing db container must be running")
    inspected = json.loads(subprocess.run(
        ["docker", "inspect", container_id], check=True, capture_output=True, text=True,
    ).stdout)[0]
    actual = dict(entry.split("=", 1) for entry in inspected["Config"]["Env"] if "=" in entry)
    if any(actual.get(key) != environment.get(key) for key in (
        "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    )):
        raise ValueError("Configured recovery credentials differ from the existing db container")
    services = [name for name, service in document["services"].items()
                if (service.get("environment") or {}).get("DATABASE_URL")]
    if "web" in document["services"]:
        services.append("web")
    subprocess.run([*command, "stop", "--timeout", "60", *services], check=True)
    running = subprocess.run(
        [*command, "ps", "--services", "--status", "running"], check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    if set(running).difference({"db", "redis"}):
        raise ValueError("An application accessor remains running; cutover was refused")
    process_environment = dict(os.environ)
    process_environment.update({key: environment[key] for key in ROLE_KEYS})
    arguments = [*command, "exec", "-T"]
    for key in ROLE_KEYS:
        arguments.extend(["--env", key])
    with (ROOT / "scripts/database/provision-roles.sh").open("r", encoding="utf-8") as script:
        subprocess.run([*arguments, "db", "bash", "-s"], stdin=script, env=process_environment, check=True)
    print("Role cutover completed. Application services remain stopped.")
    print("Recreate db to install its new recovery environment, run migrate, then start the stack.")
    print("Use the same Compose arguments for: up -d db; run --rm migrate; up -d")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        # Captured Compose output contains secrets. Never render exception.cmd/output.
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(f"Database role cutover failed: {detail}. Application services may be stopped.", file=sys.stderr)
        raise SystemExit(1) from None
