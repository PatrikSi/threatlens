#!/usr/bin/env python3
"""Generate the backend runtime lockfile from the current Python environment."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
from pathlib import Path

EXCLUDED = {"pip", "setuptools", "wheel"}


def _sorted_runtime_lines() -> list[str]:
    rows: dict[str, str] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue
        key = name.lower().replace("_", "-")
        if key in EXCLUDED:
            continue
        rows[key] = f"{name}=={version}"
    return [rows[key] for key in sorted(rows)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/requirements-lock.txt"),
        help="Path for the generated backend runtime lockfile.",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(
            [
                "# Locked backend runtime dependency set used by backend/Dockerfile.",
                "# Refresh this file from a backend runtime environment that matches backend/requirements.txt.",
                *(_sorted_runtime_lines()),
                "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
