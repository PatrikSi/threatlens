"""Keep application orchestration below the HTTP composition layer."""

import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parents[2] / "app"


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            dynamic_import = (
                isinstance(function, ast.Name) and function.id == "__import__"
                or isinstance(function, ast.Attribute) and function.attr == "import_module"
            )
            if dynamic_import and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                yield node.args[0].value


@pytest.mark.parametrize("layer", ["services", "tasks"])
def test_services_and_workers_do_not_depend_on_http_composition(layer):
    violations = []
    for path in sorted((APP / layer).rglob("*.py")):
        imports = _imports(ast.parse(path.read_text(), filename=str(path)))
        forbidden = sorted({module for module in imports if (
            module == "app.main" or module.startswith("app.main.")
            or module == "app.api" or module.startswith("app.api.")
        )})
        if forbidden:
            violations.append(f"{path.relative_to(APP)}: {', '.join(forbidden)}")
    assert not violations, "Move shared behavior below the route layer:\n" + "\n".join(violations)


@pytest.mark.parametrize("source", [
    "import app.api.routes.items", "from app.api.routes import items",
    "from app import main", "importlib.import_module('app.main')",
])
def test_boundary_guard_recognizes_direct_and_dynamic_imports(source):
    assert any(name in {"app.main", "app.api.routes", "app.api.routes.items"} for name in _imports(ast.parse(source)))
