from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "check_coverage.py"
_SPEC = importlib.util.spec_from_file_location("check_coverage", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_coverage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_coverage)


def _summary(covered: int, total: int) -> dict[str, int]:
    return {
        "covered_lines": covered,
        "missing_lines": total - covered,
        "num_statements": total,
        "covered_branches": 0,
        "missing_branches": 0,
        "num_branches": 0,
    }


def _write_coverage(path: Path, *, critical_covered: int = 95) -> None:
    files = {
        module: {"summary": _summary(critical_covered, 100)}
        for module in check_coverage.CRITICAL_MODULE_MINIMUMS
    }
    files["app/api/routes/reports.py"] = {"summary": _summary(70, 100)}
    path.write_text(
        json.dumps(
            {
                "totals": _summary(800, 1_000),
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def test_coverage_gate_accepts_current_floors(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path)

    assert check_coverage.main(coverage_path) == 0
    assert "Coverage quality gate passed" in capsys.readouterr().out


def test_coverage_gate_reports_critical_module_regression(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path, critical_covered=50)

    assert check_coverage.main(coverage_path) == 1
    error = capsys.readouterr().err
    assert "Coverage quality gate failed" in error
    assert "app/services/report_dispatch.py" in error


def test_coverage_gate_reports_feed_coordination_regression(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path)
    payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    payload["files"]["app/tasks/feed_task_coordination.py"]["summary"] = (
        _summary(50, 100)
    )
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")

    assert check_coverage.main(coverage_path) == 1
    assert "app/tasks/feed_task_coordination.py" in capsys.readouterr().err


def test_coverage_gate_reports_ai_request_runtime_regression(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path)
    payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    payload["files"]["app/services/ai_request_runtime.py"]["summary"] = _summary(
        50, 100
    )
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")

    assert check_coverage.main(coverage_path) == 1
    assert "app/services/ai_request_runtime.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    "module",
    [
        "app/db/budgets.py",
        "app/services/data_access_retention.py",
        "app/services/lifecycle_dependencies.py",
        "app/services/lifecycle_execution.py",
        "app/services/lifecycle_permission_pruning.py",
        "app/services/lifecycle_pruning.py",
        "app/services/lifecycle_targets.py",
        "app/services/processing_access.py",
        "app/services/processing_dispatch.py",
        "app/services/processing_queries.py",
        "app/services/processing_recovery.py",
        "app/services/processing_worker.py",
        "app/tasks/lifecycle_tasks.py",
        "app/tasks/processing_tasks.py",
    ],
)
def test_coverage_gate_rejects_hardening_branch_regressions(
    tmp_path, capsys, module
):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path)
    payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    # Full line coverage must not hide uncovered branches in critical modules.
    payload["files"][module]["summary"] = {
        **_summary(100, 100),
        "covered_branches": 0,
        "missing_branches": 100,
        "num_branches": 100,
    }
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")

    assert check_coverage.main(coverage_path) == 1
    assert f"{module} coverage 50.00% is below" in capsys.readouterr().err


def test_coverage_gate_rejects_missing_database_budget_module(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path)
    payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    module = "app/db/budgets.py"
    del payload["files"][module]
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")

    assert check_coverage.main(coverage_path) == 1
    assert f"critical module {module} is missing from coverage" in capsys.readouterr().err


def test_coverage_gate_rejects_invalid_document(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text("{}", encoding="utf-8")

    assert check_coverage.main(coverage_path) == 2
    assert "could not read" in capsys.readouterr().err


def test_coverage_gate_includes_extracted_schedule_dispatcher(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    _write_coverage(coverage_path)
    payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    module = "app/tasks/report_schedule_tasks.py"
    payload["files"][module] = {"summary": _summary(65, 100)}
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")

    assert check_coverage.main(coverage_path) == 1
    assert f"{module} coverage 65.00% is below 66.00%" in capsys.readouterr().err
    assert module in check_coverage._reporting_paths(payload["files"])
