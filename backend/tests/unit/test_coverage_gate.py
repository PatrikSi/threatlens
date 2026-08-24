from __future__ import annotations

import importlib.util
import json
from pathlib import Path


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


def test_coverage_gate_rejects_invalid_document(tmp_path, capsys):
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text("{}", encoding="utf-8")

    assert check_coverage.main(coverage_path) == 2
    assert "could not read" in capsys.readouterr().err
