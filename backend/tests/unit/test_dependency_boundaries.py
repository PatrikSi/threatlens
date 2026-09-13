"""Keep application orchestration below the HTTP composition layer."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parents[2] / "app"


def _imports(tree, *, package="app.services"):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            dynamic_import = (
                isinstance(function, ast.Name) and function.id == "__import__"
                or isinstance(function, ast.Attribute) and function.attr == "import_module"
            )
            if dynamic_import and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                name = node.args[0].value
                if name.startswith(".") and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    name = resolve_name(name, node.args[1].value)
                yield name


@pytest.mark.parametrize("layer", ["services", "tasks"])
def test_services_and_workers_do_not_depend_on_http_composition(layer):
    violations = []
    for path in sorted((APP / layer).rglob("*.py")):
        package = ".".join(("app", *path.relative_to(APP).parent.parts))
        imports = _imports(ast.parse(path.read_text(), filename=str(path)), package=package)
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
    "from ..api.routes import items", "from .. import main",
    "importlib.import_module('..api.routes', 'app.services')",
])
def test_boundary_guard_recognizes_direct_and_dynamic_imports(source):
    assert any(name in {"app.main", "app.api.routes", "app.api.routes.items"} for name in _imports(ast.parse(source)))


def test_feed_dependency_contracts_do_not_import_orchestration_or_celery():
    path = APP / "tasks/feed_task_dependencies.py"
    imports = set(_imports(ast.parse(path.read_text()), package="app.tasks"))
    assert not any(name.startswith(("app.services", "app.tasks", "app.api", "celery")) for name in imports)
