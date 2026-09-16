#!/usr/bin/env python3
"""Render pasteable environments using the canonical Compose defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ENVIRONMENT_MAPPINGS = (
    "db-environment",
    "redis-environment",
    "migration-environment",
    "backend-environment",
)


def load_configuration(environment_file: Path, compose_file: Path) -> dict:
    # Shell application overrides must not change freshly generated credentials
    # or make this output disagree with the ordinary bootstrap .env file.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR"}
        or key.startswith("DOCKER_")
    }
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", str(environment_file),
            "-f", str(compose_file), "config", "--format", "json",
        ],
        env=environment,
        cwd=compose_file.parent,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    document = json.loads(result.stdout)
    if not isinstance(document, dict):
        raise ValueError("Compose configuration is not an object")
    return document


def render_mappings(environment_file: Path, compose_file: Path) -> str:
    document = load_configuration(environment_file, compose_file)
    lines: list[str] = []
    for name in ENVIRONMENT_MAPPINGS:
        mapping = document[f"x-{name}"]
        if not isinstance(mapping, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in mapping.items()
        ):
            raise ValueError("Compose environment mapping is not a string mapping")
        lines.append(f"x-{name}: &{name}")
        for key, value in mapping.items():
            # JSON string literals are also YAML scalars. Compose config has
            # already doubled literal dollar signs for safe interpolation when
            # these mappings are pasted into a new Compose document.
            lines.append(f"  {key}: {json.dumps(value, ensure_ascii=True)}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: bootstrap_compose_env.py ENV_FILE COMPOSE_FILE", file=sys.stderr)
        return 2
    try:
        output = render_mappings(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        # Compose diagnostics can contain interpolated secrets. Keep errors
        # actionable without copying its output or traceback into deployment logs.
        print(
            "Unable to render the bootstrap environment. Install Docker Compose v2 "
            "and use the complete matching ThreatLens checkout.",
            file=sys.stderr,
        )
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
