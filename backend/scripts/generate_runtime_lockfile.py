#!/usr/bin/env python3
"""Generate the backend runtime lockfile from installed runtime requirements."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
from collections import deque
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

EXCLUDED = {canonicalize_name(name) for name in {"pip", "setuptools", "wheel"}}


def _requirement_lines(requirements_path: Path) -> list[str]:
    lines: list[str] = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        candidate = raw_line.split("#", 1)[0].strip()
        if not candidate or candidate.startswith("-"):
            continue
        lines.append(candidate)
    return lines


def _marker_matches(requirement: Requirement, *, active_extras: set[str]) -> bool:
    if requirement.marker is None:
        return True

    environment = default_environment()
    if not active_extras:
        return requirement.marker.evaluate({**environment, "extra": ""})
    return any(requirement.marker.evaluate({**environment, "extra": extra}) for extra in active_extras)


def _sorted_runtime_lines(requirements_path: Path) -> list[str]:
    rows: dict[str, str] = {}
    processed_base: set[str] = set()
    processed_extras: dict[str, set[str]] = {}
    pending = deque(
        (canonicalize_name(requirement.name), set(requirement.extras))
        for requirement in (Requirement(line) for line in _requirement_lines(requirements_path))
    )
    while pending:
        key, active_extras = pending.popleft()
        if key in EXCLUDED:
            continue

        if active_extras:
            already_processed = processed_extras.setdefault(key, set())
            new_extras = active_extras - already_processed
            if not new_extras:
                continue
            already_processed.update(new_extras)
            marker_extras = new_extras
        else:
            if key in processed_base:
                continue
            processed_base.add(key)
            marker_extras = set()

        dist = metadata.distribution(key)
        name = (dist.metadata.get("Name") or key).strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue
        rows[key] = f"{name}=={version}"
        for raw_requirement in dist.requires or []:
            requirement = Requirement(raw_requirement)
            if _marker_matches(requirement, active_extras=marker_extras):
                pending.append((canonicalize_name(requirement.name), set(requirement.extras)))
    return [rows[key] for key in sorted(rows)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/requirements-lock.txt"),
        help="Path for the generated backend runtime lockfile.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("backend/requirements.txt"),
        help="Runtime requirements input used as the dependency roots.",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(
            [
                "# Locked backend runtime dependency set used by backend/Dockerfile.",
                "# Refresh this file from a backend runtime environment that matches backend/requirements.txt.",
                *(_sorted_runtime_lines(args.input)),
                "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
