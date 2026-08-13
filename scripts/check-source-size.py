#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "backend" / "app", ROOT / "web" / "src")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx"}
DEFAULT_MAX_LINES = 1_200
FILE_MAX_LINES: dict[str, int] = {}


def is_production_source(path: Path) -> bool:
    return path.suffix in SOURCE_SUFFIXES and not any(
        marker in path.name for marker in (".test.", ".spec.")
    )


def main() -> int:
    violations: list[str] = []
    checked = 0

    for source_root in SOURCE_ROOTS:
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or not is_production_source(path):
                continue

            checked += 1
            relative_path = path.relative_to(ROOT).as_posix()
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            max_lines = FILE_MAX_LINES.get(relative_path, DEFAULT_MAX_LINES)
            if line_count > max_lines:
                violations.append(f"{relative_path}: {line_count} lines (limit {max_lines})")

    if violations:
        print("Source-size quality gate failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "Split the file into cohesive modules, or lower an existing exception after refactoring it.",
            file=sys.stderr,
        )
        return 1

    print(f"Source-size quality gate passed for {checked} production files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
