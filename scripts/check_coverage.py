#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


MINIMUM_TOTAL_COVERAGE = 79.0
MINIMUM_REPORTING_COVERAGE = 63.0
CRITICAL_MODULE_MINIMUMS = {
    "app/services/ai_request_runtime.py": 85.0,
    "app/services/feed_fetch_ownership.py": 92.0,
    "app/services/report_dispatch.py": 85.0,
    "app/services/report_execution.py": 88.0,
    "app/services/report_idempotency.py": 89.0,
    "app/services/report_schedules.py": 68.0,
    "app/tasks/feed_fetch_tasks.py": 62.0,
    "app/tasks/feed_task_coordination.py": 68.0,
    "app/tasks/report_tasks.py": 66.0,
}


def _percentage(summary: dict[str, Any]) -> float:
    covered = int(summary.get("covered_lines", 0)) + int(
        summary.get("covered_branches", 0)
    )
    total = int(summary.get("num_statements", 0)) + int(
        summary.get("num_branches", 0)
    )
    return 100.0 if total == 0 else 100.0 * covered / total


def _reporting_paths(files: dict[str, Any]) -> list[str]:
    return sorted(
        path
        for path in files
        if path.startswith("app/services/report_")
        or path
        in {
            "app/api/routes/reports.py",
            "app/services/ai_request_runtime.py",
            "app/tasks/report_tasks.py",
        }
    )


def _aggregate_percentage(files: dict[str, Any], paths: list[str]) -> float:
    aggregate = {
        "covered_lines": 0,
        "covered_branches": 0,
        "num_statements": 0,
        "num_branches": 0,
    }
    for path in paths:
        summary = files[path]["summary"]
        for key in aggregate:
            aggregate[key] += int(summary.get(key, 0))
    return _percentage(aggregate)


def main(coverage_path: Path | None = None) -> int:
    resolved_path = coverage_path or Path(
        sys.argv[1] if len(sys.argv) > 1 else "backend/coverage.json"
    )
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        files = payload["files"]
        totals = payload["totals"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Coverage gate could not read {resolved_path}: {exc}", file=sys.stderr)
        return 2

    violations: list[str] = []
    total_coverage = _percentage(totals)
    if total_coverage < MINIMUM_TOTAL_COVERAGE:
        violations.append(
            f"overall coverage {total_coverage:.2f}% "
            f"is below {MINIMUM_TOTAL_COVERAGE:.2f}%"
        )

    reporting_paths = _reporting_paths(files)
    if not reporting_paths:
        violations.append("reporting source files are missing from the coverage report")
    else:
        reporting_coverage = _aggregate_percentage(files, reporting_paths)
        if reporting_coverage < MINIMUM_REPORTING_COVERAGE:
            violations.append(
                f"reporting coverage {reporting_coverage:.2f}% "
                f"is below {MINIMUM_REPORTING_COVERAGE:.2f}%"
            )

    for path, minimum in CRITICAL_MODULE_MINIMUMS.items():
        file_data = files.get(path)
        if file_data is None:
            violations.append(f"critical module {path} is missing from coverage")
            continue
        covered = _percentage(file_data["summary"])
        if covered < minimum:
            violations.append(
                f"{path} coverage {covered:.2f}% is below {minimum:.2f}%"
            )

    if violations:
        print("Coverage quality gate failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    print(
        f"Coverage quality gate passed: overall {total_coverage:.2f}%, "
        f"reporting {_aggregate_percentage(files, reporting_paths):.2f}%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
