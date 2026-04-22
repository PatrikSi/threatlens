#!/usr/bin/env python3
"""Sync repository compliance artifacts into backend/ and web/ build contexts."""

from __future__ import annotations

import shutil
from pathlib import Path


def _sync_bundle(target_root: Path, repo_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    licenses_target = target_root / "licenses"
    licenses_target.mkdir(parents=True, exist_ok=True)

    shutil.copy2(repo_root / "LICENSE", target_root / "LICENSE")
    shutil.copy2(repo_root / "THIRD_PARTY_NOTICES.md", target_root / "THIRD_PARTY_NOTICES.md")

    for source in (repo_root / "docs" / "licenses").glob("*.txt"):
        shutil.copy2(source, licenses_target / source.name)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    _sync_bundle(repo_root / "backend" / "compliance", repo_root)
    _sync_bundle(repo_root / "web" / "compliance", repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
