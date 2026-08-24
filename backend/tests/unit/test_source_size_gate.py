from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "check-source-size.py"
_SPEC = importlib.util.spec_from_file_location("check_source_size", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_source_size = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_source_size)


def _configure_source_root(tmp_path, monkeypatch):
    source_root = tmp_path / "backend" / "app"
    source_root.mkdir(parents=True)
    monkeypatch.setattr(check_source_size, "ROOT", tmp_path)
    monkeypatch.setattr(check_source_size, "SOURCE_ROOTS", (source_root,))
    return source_root


def test_source_size_gate_accepts_bounded_source(tmp_path, monkeypatch, capsys):
    source_root = _configure_source_root(tmp_path, monkeypatch)
    (source_root / "bounded.py").write_text("value = 1\n", encoding="utf-8")

    assert check_source_size.main() == 0
    assert "passed for 1 production files" in capsys.readouterr().out


def test_source_size_gate_reports_file_and_physical_line_limits(
    tmp_path,
    monkeypatch,
    capsys,
):
    source_root = _configure_source_root(tmp_path, monkeypatch)
    long_line = "x" * (check_source_size.MAX_PHYSICAL_LINE_LENGTH + 1)
    lines = [long_line] + ["value = 1"] * check_source_size.DEFAULT_MAX_LINES
    (source_root / "compressed.py").write_text("\n".join(lines), encoding="utf-8")

    assert check_source_size.main() == 1
    error = capsys.readouterr().err
    assert "1201 lines (limit 1200)" in error
    assert "compressed.py:1: 301 characters" in error
