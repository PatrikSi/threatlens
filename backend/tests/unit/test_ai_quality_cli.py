"""Offline preparation cannot silently skip an explicitly requested quality gate."""

import importlib.util
from pathlib import Path
import sys

import pytest


def _script(name):
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location("quality_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("extra", [["--gate"], ["--thresholds", "policy.json"],
                                  ["--predictions", "captured.jsonl"]])
def test_prepare_rejects_ignored_scoring_options(tmp_path, monkeypatch, extra):
    output = tmp_path / "inputs.jsonl"
    monkeypatch.setattr(sys, "argv", ["evaluate_ai_quality.py", "--prepare", "--output", str(output), *extra])
    with pytest.raises(SystemExit) as exc:
        _script("evaluate_ai_quality.py").main()
    assert exc.value.code == 2
    assert not output.exists()
