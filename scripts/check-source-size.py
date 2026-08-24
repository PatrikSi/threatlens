#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "backend" / "app", ROOT / "web" / "src")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx"}
DEFAULT_MAX_LINES = 1_200
MAX_PHYSICAL_LINE_LENGTH = 300


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
            lines = path.read_text(encoding="utf-8").splitlines()
            line_count = len(lines)
            if line_count > DEFAULT_MAX_LINES:
                violations.append(
                    f"{relative_path}: {line_count} lines "
                    f"(limit {DEFAULT_MAX_LINES})"
                )
            for line_number, line in enumerate(lines, start=1):
                if len(line) > MAX_PHYSICAL_LINE_LENGTH:
                    violations.append(
                        f"{relative_path}:{line_number}: {len(line)} characters "
                        f"(limit {MAX_PHYSICAL_LINE_LENGTH})"
                    )

    if violations:
        print("Source-size quality gate failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "Split large files into cohesive modules and wrap compressed physical lines.",
            file=sys.stderr,
        )
        return 1

    print(f"Source-size quality gate passed for {checked} production files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
