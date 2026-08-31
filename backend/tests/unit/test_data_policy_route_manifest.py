from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

from app.core.data_policy_route_attestation import (
    RouteGovernanceManifestError,
    iter_effective_api_routes,
    validate_route_governance_manifest,
)
from app.core.data_policy_route_manifest import (
    ROUTE_GOVERNANCE_MANIFEST,
    ROUTE_GOVERNANCE_MANIFEST_SHA256,
    ROUTE_GOVERNANCE_MANIFEST_VERSION,
    RouteGovernanceClass,
    RouteGovernanceEntry,
    RouteGovernanceManifest,
    RouteOperation,
    route_governance_manifest_digest,
)


def _data_access_dependency() -> object:
    return object()


def _nested_data_access_dependency(
    _context: object = Depends(_data_access_dependency),
) -> object:
    return object()


def _endpoint() -> dict[str, bool]:
    return {"ok": True}


def _replacement_endpoint() -> dict[str, bool]:
    return {"ok": True}


def _endpoint_identity(endpoint: Any) -> str:
    return f"{endpoint.__module__}.{endpoint.__qualname__}"


def _operation(
    *,
    endpoint: Any = _endpoint,
    method: str = "GET",
    path_format: str = "/v1/example",
    route_name: str | None = None,
) -> RouteOperation:
    return RouteOperation(
        method=method,
        path_format=path_format,
        route_name=route_name or endpoint.__name__,
        endpoint_identity=_endpoint_identity(endpoint),
    )


def _entry(
    *,
    endpoint: Any = _endpoint,
    method: str = "GET",
    path_format: str = "/v1/example",
    route_name: str | None = None,
    governance_class: RouteGovernanceClass = RouteGovernanceClass.CONTROL_PLANE,
) -> RouteGovernanceEntry:
    return RouteGovernanceEntry(
        operation=_operation(
            endpoint=endpoint,
            method=method,
            path_format=path_format,
            route_name=route_name,
        ),
        governance_class=governance_class,
    )


def _manifest(
    *entries: RouteGovernanceEntry,
    context_classes: frozenset[RouteGovernanceClass] | None = None,
) -> RouteGovernanceManifest:
    return RouteGovernanceManifest(
        version=1,
        canonical_prefix="/v1",
        entries=tuple(entries),
        request_context_classes=(
            context_classes
            if context_classes is not None
            else frozenset(
                {
                    RouteGovernanceClass.REQUEST_CONTEXT,
                    RouteGovernanceClass.DYNAMIC_TARGET,
                }
            )
        ),
    )


def _application(
    *routes: tuple[
        str,
        str,
        Any,
        str,
        list[Any],
    ],
    include_legacy_alias: bool = False,
) -> FastAPI:
    router = APIRouter()
    for method, path, endpoint, name, dependencies in routes:
        router.add_api_route(
            path,
            endpoint,
            methods=[method],
            name=name,
            dependencies=dependencies,
        )
    application = FastAPI()
    application.include_router(router, prefix="/v1")
    if include_legacy_alias:
        application.include_router(router, include_in_schema=False)
    return application


def _route(
    *,
    method: str = "GET",
    path: str = "/example",
    endpoint: Any = _endpoint,
    name: str | None = None,
    dependencies: list[Any] | None = None,
) -> tuple[str, str, Any, str, list[Any]]:
    return (
        method,
        path,
        endpoint,
        name or endpoint.__name__,
        dependencies or [],
    )


def test_live_manifest_is_the_exact_immutable_canonical_route_contract():
    from app.main import app

    attestation = validate_route_governance_manifest(app)

    assert ROUTE_GOVERNANCE_MANIFEST_VERSION == 1
    assert len(ROUTE_GOVERNANCE_MANIFEST.entries) == 268
    assert len({entry.operation for entry in ROUTE_GOVERNANCE_MANIFEST.entries}) == 268
    assert ROUTE_GOVERNANCE_MANIFEST_SHA256 == (
        "f0ad5ce4799de6d4c1f701241e95cc36777906d5d9ad4428869f1b4e571e9bc6"
    )
    assert attestation.manifest_sha256 == ROUTE_GOVERNANCE_MANIFEST_SHA256
    assert attestation.declared_operation_count == 268
    assert attestation.validated_operation_count == 268
    assert attestation.request_context_operation_count == 108
    assert attestation.governance_class_counts == (
        ("captured_async", 5),
        ("control_plane", 143),
        ("dynamic_target", 7),
        ("egress_fenced", 1),
        ("public", 11),
        ("request_context", 101),
    )
    with pytest.raises(FrozenInstanceError):
        attestation.manifest_version = 2  # type: ignore[misc]


def test_manifest_digest_is_deterministic_across_entry_order():
    first = _entry()
    second = _entry(
        endpoint=_replacement_endpoint,
        path_format="/v1/second",
    )

    forward = _manifest(first, second)
    reverse = _manifest(second, first)

    assert route_governance_manifest_digest(forward) == (
        route_governance_manifest_digest(reverse)
    )


def test_validator_reports_manifest_operation_missing_from_application():
    application = _application(_route())
    missing = _entry(path_format="/v1/missing")

    with pytest.raises(
        RouteGovernanceManifestError,
        match="manifest operations missing from application",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(missing),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_reports_unmanifested_application_operation():
    application = _application(
        _route(),
        _route(
            path="/extra",
            endpoint=_replacement_endpoint,
        ),
    )

    with pytest.raises(
        RouteGovernanceManifestError,
        match="unmanifested application operations",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(_entry()),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_reports_route_name_change():
    application = _application(_route(name="renamed"))

    with pytest.raises(RouteGovernanceManifestError) as caught:
        validate_route_governance_manifest(
            application,
            manifest=_manifest(_entry()),
            data_access_dependency=_data_access_dependency,
        )

    message = str(caught.value)
    assert "renamed" in message
    assert _endpoint.__name__ in message


def test_validator_reports_same_name_endpoint_replacement():
    application = _application(
        _route(
            endpoint=_replacement_endpoint,
            name=_endpoint.__name__,
        )
    )

    with pytest.raises(RouteGovernanceManifestError) as caught:
        validate_route_governance_manifest(
            application,
            manifest=_manifest(_entry()),
            data_access_dependency=_data_access_dependency,
        )

    message = str(caught.value)
    assert _endpoint_identity(_endpoint) in message
    assert _endpoint_identity(_replacement_endpoint) in message


def test_validator_reports_duplicate_manifest_operation():
    duplicate = _entry()
    application = _application(_route())

    with pytest.raises(
        RouteGovernanceManifestError,
        match="manifest duplicate operation",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(duplicate, duplicate),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_reports_duplicate_application_method_path():
    application = _application(
        _route(),
        _route(endpoint=_replacement_endpoint),
    )

    with pytest.raises(
        RouteGovernanceManifestError,
        match="application duplicate method/path",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(_entry()),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_requires_recursive_context_for_context_class():
    application = _application(_route())
    context_entry = _entry(
        governance_class=RouteGovernanceClass.REQUEST_CONTEXT,
    )

    with pytest.raises(
        RouteGovernanceManifestError,
        match="routes missing get_data_access_context",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(context_entry),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_rejects_context_on_non_context_class():
    application = _application(_route(dependencies=[Depends(_data_access_dependency)]))

    with pytest.raises(
        RouteGovernanceManifestError,
        match="routes unexpectedly depend on get_data_access_context",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(_entry()),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_accepts_nested_context_dependency():
    application = _application(
        _route(dependencies=[Depends(_nested_data_access_dependency)])
    )
    context_entry = _entry(
        governance_class=RouteGovernanceClass.REQUEST_CONTEXT,
    )

    attestation = validate_route_governance_manifest(
        application,
        manifest=_manifest(context_entry),
        data_access_dependency=_data_access_dependency,
    )

    assert attestation.request_context_operation_count == 1


def test_validator_rejects_noncanonical_endpoint_identity():
    application = _application(_route())
    malformed = replace(
        _entry(),
        operation=replace(_operation(), endpoint_identity="endpoint"),
    )

    with pytest.raises(
        RouteGovernanceManifestError,
        match="manifest endpoint identity is not canonical",
    ):
        validate_route_governance_manifest(
            application,
            manifest=_manifest(malformed),
            data_access_dependency=_data_access_dependency,
        )


def test_validator_walks_included_router_and_ignores_legacy_alias():
    application = _application(_route(), include_legacy_alias=True)

    assert not any(isinstance(route, APIRoute) for route in application.routes)
    assert len(tuple(iter_effective_api_routes(application))) == 2

    attestation = validate_route_governance_manifest(
        application,
        manifest=_manifest(_entry()),
        data_access_dependency=_data_access_dependency,
    )

    assert attestation.validated_operation_count == 1
