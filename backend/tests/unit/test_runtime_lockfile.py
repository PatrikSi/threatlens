from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _load_lockfile_module():
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "backend" / "scripts" / "generate_runtime_lockfile.py"
    spec = spec_from_file_location("generate_runtime_lockfile", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _locked(*requirements: str):
    return {
        canonicalize_name(requirement.name): requirement
        for requirement in (Requirement(value) for value in requirements)
    }


def test_runtime_lockfile_contains_runtime_closure_without_dev_only_packages():
    repo_root = Path(__file__).resolve().parents[3]
    module = _load_lockfile_module()

    locked = module._locked_requirements(repo_root / "backend" / "requirements-lock.txt")

    assert "fastapi" in locked
    assert "starlette" in locked
    assert "pytest" not in locked
    assert "pytest-cov" not in locked
    assert "pluggy" not in locked
    assert "iniconfig" not in locked


def test_changed_direct_constraint_is_rejected_before_resolution(tmp_path):
    module = _load_lockfile_module()
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text("fastapi==0.1.0\n", encoding="utf-8")

    with pytest.raises(module.LockfileError, match="does not satisfy direct requirement fastapi==0.1.0"):
        module._validate_direct_requirements(requirements_path, _locked("fastapi==0.139.2"))


def test_matching_direct_constraint_is_accepted(tmp_path):
    module = _load_lockfile_module()
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text("fastapi>=0.139,<0.140\n", encoding="utf-8")

    module._validate_direct_requirements(requirements_path, _locked("fastapi==0.139.2"))


def test_resolved_transitive_drift_is_rejected():
    module = _load_lockfile_module()
    resolved = {
        "fastapi": ("fastapi", "0.139.2"),
        "starlette": ("starlette", "1.3.2"),
    }

    with pytest.raises(module.LockfileError, match="starlette: locked=1.3.1, resolved=1.3.2"):
        module._validate_resolved_lock(
            resolved,
            _locked("fastapi==0.139.2", "starlette==1.3.1"),
        )


def test_resolver_ignores_installed_environment_and_uses_lock_constraints(monkeypatch, tmp_path):
    module = _load_lockfile_module()
    requirements_path = tmp_path / "requirements.txt"
    constraints_path = tmp_path / "requirements-lock.txt"
    requirements_path.write_text("demo==1.0\n", encoding="utf-8")
    constraints_path.write_text("demo==1.0\n", encoding="utf-8")
    captured_command = []

    def fake_run(command, **kwargs):
        captured_command.extend(command)
        report_path = Path(command[command.index("--report") + 1])
        report_path.write_text(
            '{"install": [{"metadata": {"name": "demo", "version": "1.0"}}]}',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    resolved = module._resolve(
        requirements_path,
        constraints_path=constraints_path,
        timeout_seconds=30,
    )

    assert resolved == {"demo": ("demo", "1.0")}
    assert "--isolated" in captured_command
    assert "--dry-run" in captured_command
    assert "--ignore-installed" in captured_command
    assert captured_command[captured_command.index("--constraint") + 1] == str(constraints_path)


def test_normal_generation_preserves_validated_lock_bytes(monkeypatch, tmp_path):
    module = _load_lockfile_module()
    requirements_path = tmp_path / "requirements.txt"
    constraints_path = tmp_path / "requirements-lock.txt"
    output_path = tmp_path / "generated.txt"
    requirements_path.write_text("demo==1.0\n", encoding="utf-8")
    lock_contents = "# existing lock header\ndemo==1.0\n"
    constraints_path.write_text(lock_contents, encoding="utf-8")
    monkeypatch.setattr(module, "_resolve", lambda *args, **kwargs: {"demo": ("demo", "1.0")})

    result = module.main(
        [
            "--input",
            str(requirements_path),
            "--constraints",
            str(constraints_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert output_path.read_text(encoding="utf-8") == lock_contents
