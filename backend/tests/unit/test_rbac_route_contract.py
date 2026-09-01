from app.core.permissions import is_known_permission
from app.core.rbac import ALL_ROLES
from app.main import (
    API_SERVICE_PREFIX,
    _collect_route_token_scopes,
    _iter_effective_api_routes,
    app,
)


PERMISSION_FREE_OPERATIONS = frozenset(
    {
        ("GET", "/v1/auth/registration-settings"),
        ("POST", "/v1/auth/register"),
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/mfa/verify"),
        ("GET", "/v1/auth/me"),
        ("POST", "/v1/auth/change-password"),
        ("POST", "/v1/auth/logout"),
        ("GET", "/v1/auth/security/sessions"),
        ("DELETE", "/v1/auth/security/sessions/{session_id}"),
        ("POST", "/v1/auth/security/sessions/revoke-others"),
        ("POST", "/v1/auth/security/reauthenticate"),
        ("GET", "/v1/auth/security/mfa"),
        ("POST", "/v1/auth/security/mfa/enroll"),
        ("POST", "/v1/auth/security/mfa/confirm"),
        ("DELETE", "/v1/auth/security/mfa/enrollment"),
        ("POST", "/v1/auth/security/mfa/recovery-codes"),
        ("DELETE", "/v1/auth/security/mfa"),
        ("GET", "/v1/auth/oidc/settings"),
        ("GET", "/v1/auth/oidc/login"),
        ("POST", "/v1/auth/oidc/link"),
        ("POST", "/v1/auth/oidc/reauth"),
        ("GET", "/v1/auth/oidc/callback"),
        ("GET", "/v1/auth/oidc/account"),
        ("DELETE", "/v1/auth/oidc/account"),
        ("GET", "/v1/health"),
        ("GET", "/v1/health/ready"),
        ("GET", "/v1/health/live"),
        ("GET", "/v1/iam/effective"),
        ("GET", "/v1/iam/effective/explain"),
        ("POST", "/v1/iam/elevations/{elevation_id}/close"),
        ("POST", "/v1/iam/action-approvals/{approval_id}/cancel"),
    }
)

ADMIN_ONLY_OPERATIONS = frozenset(
    {
        ("GET", "/v1/auth/oidc/provider"),
        ("PUT", "/v1/auth/oidc/provider"),
        ("POST", "/v1/auth/oidc/provider/test"),
        ("GET", "/v1/auth/oidc/access-policy"),
        ("POST", "/v1/auth/oidc/access-policy"),
        ("PUT", "/v1/auth/oidc/access-policy"),
        ("DELETE", "/v1/auth/oidc/access-policy"),
        ("POST", "/v1/auth/oidc/access-policy/mapping-sets"),
        ("PUT", "/v1/auth/oidc/access-policy/mapping-sets/{mapping_set_id}"),
        ("DELETE", "/v1/auth/oidc/access-policy/mapping-sets/{mapping_set_id}"),
        ("GET", "/v1/reports/schedules"),
        ("POST", "/v1/reports/schedules"),
        ("PUT", "/v1/reports/schedules/{schedule_id}"),
        ("POST", "/v1/reports/schedules/{schedule_id}/run"),
        ("DELETE", "/v1/reports/schedules/{schedule_id}"),
        ("POST", "/v1/alerts/occurrences/reconciliation/preview"),
        ("POST", "/v1/alerts/occurrences/reconciliation/apply"),
        ("GET", "/v1/alerts/occurrences/evaluations"),
        ("GET", "/v1/alerts/occurrences/evaluations/{request_id}"),
        ("GET", "/v1/alerts/occurrences/evaluations/{request_id}/activity"),
        ("POST", "/v1/alerts/occurrences/evaluations/{request_id}/replay"),
        ("GET", "/v1/users"),
        ("GET", "/v1/users/{user_id}"),
        ("POST", "/v1/users"),
        ("PATCH", "/v1/users/{user_id}"),
        ("POST", "/v1/users/{user_id}/mfa/reset"),
        ("GET", "/v1/ai/settings"),
        ("PUT", "/v1/ai/settings"),
        ("POST", "/v1/ai/test-connection"),
        ("GET", "/v1/ai/usage"),
        ("POST", "/v1/ai/daily-brief/generate"),
        ("POST", "/v1/ai/daily-brief/queue"),
        ("POST", "/v1/ai/daily-brief/backfill"),
        ("POST", "/v1/ai/reprocess"),
        ("GET", "/v1/ai/ops/overview"),
        ("GET", "/v1/ai/ops/live"),
        ("GET", "/v1/ai/ops/runs"),
        ("GET", "/v1/ai/ops/runs/{run_id}"),
        ("POST", "/v1/ai/ops/runs/{run_id}/cancel"),
        ("GET", "/v1/ai/ops/manual-actions"),
        ("GET", "/v1/ai/ops/prompt-history"),
        ("GET", "/v1/ai/daily-briefs/{brief_id}/sources"),
        ("GET", "/v1/health/worker"),
        ("GET", "/v1/health/beat"),
        ("GET", "/v1/health/notifications"),
        ("GET", "/v1/health/encrypted-data"),
    }
)

OPERATOR_ONLY_OPERATIONS = frozenset(
    {
        ("POST", "/v1/notifications/webhooks"),
        ("PATCH", "/v1/notifications/webhooks/{webhook_id}"),
        ("DELETE", "/v1/notifications/webhooks/{webhook_id}"),
        (
            "POST",
            "/v1/notifications/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
        ),
        ("POST", "/v1/notifications/webhooks/test"),
    }
)


def test_every_canonical_route_has_a_known_permission_or_explicit_exception():
    observed_permission_free: set[tuple[str, str]] = set()

    for route in _iter_effective_api_routes(app):
        if not route.path.startswith(API_SERVICE_PREFIX):
            continue
        scopes = _collect_route_token_scopes(route)
        for method in route.methods:
            operation = (method, route.path)
            if scopes:
                assert all(is_known_permission(scope) for scope in scopes), operation
            else:
                observed_permission_free.add(operation)

    assert observed_permission_free == PERMISSION_FREE_OPERATIONS


def test_every_legacy_role_gate_is_paired_with_a_canonical_permission():
    observed: dict[tuple[str, str], tuple[str, ...]] = {}
    for route in _iter_effective_api_routes(app):
        if not route.path.startswith(API_SERVICE_PREFIX):
            continue
        required_roles = _collect_dependency_metadata(
            route.dependant, "_threatlens_required_roles"
        )
        if not required_roles:
            continue
        assert set(required_roles) <= set(ALL_ROLES), route.path
        assert _collect_route_token_scopes(route), route.path
        for method in route.methods:
            observed[(method, route.path)] = required_roles

    assert observed == {
        **{operation: ("admin",) for operation in ADMIN_ONLY_OPERATIONS},
        **{operation: ("admin", "analyst") for operation in OPERATOR_ONLY_OPERATIONS},
    }


def _collect_dependency_metadata(dependant, attribute: str) -> tuple[str, ...]:
    values: list[str] = []

    def walk(current) -> None:
        call = getattr(current, "call", None)
        values.extend(getattr(call, attribute, ()) or ())
        for dependency in current.dependencies:
            walk(dependency)

    walk(dependant)
    return tuple(dict.fromkeys(values))
