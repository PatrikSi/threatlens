from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.core.data_policy_route_manifest import (
    ROUTE_GOVERNANCE_MANIFEST,
    RouteGovernanceManifest,
    RouteOperation,
    route_governance_manifest_digest,
)


class RouteGovernanceManifestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RouteGovernanceAttestation:
    manifest_version: int
    manifest_sha256: str
    canonical_prefix: str
    declared_operation_count: int
    validated_operation_count: int
    request_context_operation_count: int
    governance_class_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _DiscoveredOperation:
    operation: RouteOperation
    has_data_access_context: bool


_installed_attestation: RouteGovernanceAttestation | None = None
_attestation_lock = Lock()


def install_route_governance_attestation(
    attestation: RouteGovernanceAttestation,
) -> None:
    """Install the startup-validated route contract exactly once per process."""

    global _installed_attestation
    with _attestation_lock:
        if _installed_attestation is None:
            _installed_attestation = attestation
            return
        if _installed_attestation != attestation:
            raise RouteGovernanceManifestError(
                "The installed data-policy route attestation is immutable."
            )


def installed_route_governance_attestation() -> RouteGovernanceAttestation | None:
    """Return immutable evidence installed after application route validation."""

    return _installed_attestation


def iter_effective_api_routes(application: FastAPI) -> Iterator[Any]:
    """Yield concrete routes from both eager and FastAPI lazy router mounts."""

    for route in application.routes:
        if isinstance(route, APIRoute):
            yield route
            continue

        route_contexts = getattr(route, "effective_route_contexts", None)
        if not callable(route_contexts):
            continue
        for route_context in route_contexts():
            if isinstance(getattr(route_context, "original_route", None), APIRoute):
                yield route_context


def validate_route_governance_manifest(
    application: FastAPI,
    *,
    manifest: RouteGovernanceManifest = ROUTE_GOVERNANCE_MANIFEST,
    data_access_dependency: Callable[..., object] | None = None,
) -> RouteGovernanceAttestation:
    if data_access_dependency is None:
        from app.api.deps import get_data_access_context

        data_access_dependency = get_data_access_context
    errors = _manifest_definition_errors(manifest)
    discovered = _discover_operations(
        application,
        canonical_prefix=manifest.canonical_prefix,
        data_access_dependency=data_access_dependency,
    )
    errors.extend(_application_duplicate_errors(discovered))

    expected_entries = {entry.operation: entry for entry in manifest.entries}
    actual_by_operation = {item.operation: item for item in discovered}
    expected_operations = set(expected_entries)
    actual_operations = set(actual_by_operation)

    unmanifested = actual_operations - expected_operations
    missing = expected_operations - actual_operations
    if unmanifested:
        errors.append(
            "unmanifested application operations: " + _display_operations(unmanifested)
        )
    if missing:
        errors.append(
            "manifest operations missing from application: "
            + _display_operations(missing)
        )

    comparable = expected_operations & actual_operations
    expected_context = {
        operation
        for operation in comparable
        if expected_entries[operation].governance_class
        in manifest.request_context_classes
    }
    actual_context = {
        operation
        for operation in comparable
        if actual_by_operation[operation].has_data_access_context
    }
    missing_context = expected_context - actual_context
    unexpected_context = actual_context - expected_context
    if missing_context:
        errors.append(
            "routes missing get_data_access_context: "
            + _display_operations(missing_context)
        )
    if unexpected_context:
        errors.append(
            "routes unexpectedly depend on get_data_access_context: "
            + _display_operations(unexpected_context)
        )

    if errors:
        raise RouteGovernanceManifestError(
            "Data-policy route governance manifest validation failed: "
            + "; ".join(errors)
        )

    class_counts = Counter(entry.governance_class.value for entry in manifest.entries)
    return RouteGovernanceAttestation(
        manifest_version=manifest.version,
        manifest_sha256=route_governance_manifest_digest(manifest),
        canonical_prefix=manifest.canonical_prefix,
        declared_operation_count=len(manifest.entries),
        validated_operation_count=len(discovered),
        request_context_operation_count=len(actual_context),
        governance_class_counts=tuple(sorted(class_counts.items())),
    )


def _discover_operations(
    application: FastAPI,
    *,
    canonical_prefix: str,
    data_access_dependency: Callable[..., object],
) -> tuple[_DiscoveredOperation, ...]:
    discovered: list[_DiscoveredOperation] = []
    for route in iter_effective_api_routes(application):
        path_format = str(getattr(route, "path_format", "") or "")
        if not _is_canonical_path(path_format, canonical_prefix):
            continue
        raw_path = str(getattr(route, "path", "") or path_format)
        normalized_raw_path = raw_path if raw_path != path_format else None
        route_name = str(getattr(route, "name", "") or "")
        endpoint_identity = _endpoint_identity(route)
        has_context = _has_recursive_dependency(route, data_access_dependency)
        for method in sorted(getattr(route, "methods", ()) or ()):
            discovered.append(
                _DiscoveredOperation(
                    operation=RouteOperation(
                        method=str(method).upper(),
                        path_format=path_format,
                        route_name=route_name,
                        endpoint_identity=endpoint_identity,
                        raw_path=normalized_raw_path,
                    ),
                    has_data_access_context=has_context,
                )
            )
    return tuple(discovered)


def _endpoint_identity(route: Any) -> str:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        endpoint = getattr(getattr(route, "original_route", None), "endpoint", None)
    module_name = getattr(endpoint, "__module__", None)
    qualified_name = getattr(endpoint, "__qualname__", None)
    if not isinstance(module_name, str) or not isinstance(qualified_name, str):
        return ""
    if not module_name or not qualified_name:
        return ""
    return f"{module_name}.{qualified_name}"


def _has_recursive_dependency(route: Any, target: Callable[..., object]) -> bool:
    root = getattr(route, "dependant", None)
    if root is None:
        return False
    stack = [root]
    visited: set[int] = set()
    while stack:
        dependant = stack.pop()
        identity = id(dependant)
        if identity in visited:
            continue
        visited.add(identity)
        if getattr(dependant, "call", None) is target:
            return True
        stack.extend(getattr(dependant, "dependencies", ()) or ())
    return False


def _manifest_definition_errors(
    manifest: RouteGovernanceManifest,
) -> list[str]:
    errors: list[str] = []
    if manifest.version < 1:
        errors.append("manifest version must be positive")
    if (
        not manifest.canonical_prefix.startswith("/")
        or manifest.canonical_prefix == "/"
        or manifest.canonical_prefix.endswith("/")
    ):
        errors.append("manifest canonical prefix must be an absolute non-root path")
    if not manifest.entries:
        errors.append("manifest has no operations")

    for entry in manifest.entries:
        operation = entry.operation
        if not operation.method or operation.method != operation.method.upper():
            errors.append(f"manifest method is not canonical: {operation.display()}")
        if not _is_canonical_path(
            operation.path_format,
            manifest.canonical_prefix,
        ):
            errors.append(
                f"manifest path is outside canonical prefix: {operation.display()}"
            )
        if operation.raw_path is not None and not _is_canonical_path(
            operation.raw_path,
            manifest.canonical_prefix,
        ):
            errors.append(
                f"manifest raw path is outside canonical prefix: {operation.display()}"
            )
        if not operation.route_name:
            errors.append(f"manifest route name is empty: {operation.display()}")
        if (
            not operation.endpoint_identity
            or operation.endpoint_identity != operation.endpoint_identity.strip()
            or "." not in operation.endpoint_identity
        ):
            errors.append(
                f"manifest endpoint identity is not canonical: {operation.display()}"
            )

    operations = [entry.operation for entry in manifest.entries]
    errors.extend(
        _duplicate_errors(
            operations,
            label="manifest duplicate operation",
            key=lambda operation: operation,
        )
    )
    errors.extend(
        _duplicate_errors(
            operations,
            label="manifest duplicate method/path",
            key=lambda operation: (operation.method, operation.path_format),
        )
    )
    errors.extend(
        _duplicate_errors(
            operations,
            label="manifest duplicate route name",
            key=lambda operation: operation.route_name,
        )
    )
    return errors


def _application_duplicate_errors(
    discovered: tuple[_DiscoveredOperation, ...],
) -> list[str]:
    operations = [item.operation for item in discovered]
    errors = _duplicate_errors(
        operations,
        label="application duplicate operation",
        key=lambda operation: operation,
    )
    errors.extend(
        _duplicate_errors(
            operations,
            label="application duplicate method/path",
            key=lambda operation: (operation.method, operation.path_format),
        )
    )
    errors.extend(
        _duplicate_errors(
            operations,
            label="application duplicate route name",
            key=lambda operation: operation.route_name,
        )
    )
    return errors


def _duplicate_errors(
    operations: Iterable[RouteOperation],
    *,
    label: str,
    key: Callable[[RouteOperation], object],
) -> list[str]:
    grouped: dict[object, list[RouteOperation]] = {}
    for operation in operations:
        grouped.setdefault(key(operation), []).append(operation)
    duplicates = [values for values in grouped.values() if len(values) > 1]
    if not duplicates:
        return []
    rendered = " | ".join(
        _display_operations(values)
        for values in sorted(
            duplicates,
            key=lambda values: tuple(sorted(item.display() for item in values)),
        )
    )
    return [f"{label}: {rendered}"]


def _is_canonical_path(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _display_operations(operations: Iterable[RouteOperation]) -> str:
    return ", ".join(
        operation.display()
        for operation in sorted(
            operations,
            key=lambda operation: (
                operation.path_format,
                operation.method,
                operation.route_name,
                operation.endpoint_identity,
                operation.raw_path or "",
            ),
        )
    )


__all__ = [
    "RouteGovernanceAttestation",
    "RouteGovernanceManifestError",
    "install_route_governance_attestation",
    "installed_route_governance_attestation",
    "iter_effective_api_routes",
    "validate_route_governance_manifest",
]
