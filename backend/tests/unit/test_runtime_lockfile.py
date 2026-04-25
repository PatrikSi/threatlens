from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_lockfile_module():
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "backend" / "scripts" / "generate_runtime_lockfile.py"
    spec = spec_from_file_location("generate_runtime_lockfile", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_lockfile_generation_ignores_installed_dev_only_packages():
    repo_root = Path(__file__).resolve().parents[3]
    module = _load_lockfile_module()

    lines = module._sorted_runtime_lines(repo_root / "backend" / "requirements.txt")
    normalized = {line.split("==", 1)[0].lower().replace("_", "-") for line in lines}

    assert "fastapi" in normalized
    assert "pytest" not in normalized
    assert "pytest-cov" not in normalized
    assert "pluggy" not in normalized
    assert "iniconfig" not in normalized
