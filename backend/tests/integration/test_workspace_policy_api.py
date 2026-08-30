from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import workspace as workspace_routes
from app.api.routes.workspace import router as workspace_router
from app.core.api_errors import install_api_error_handlers
from app.core.security import generate_api_token
from app.core.token_scopes import (
    SCOPE_READ_WORKSPACE,
    SCOPE_WRITE_WORKSPACE_PREFERENCES,
)
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.iam import (
    IAMPolicyState,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.user import User
from app.models.workspace import WorkspaceRolePolicy, WorkspaceUserPreference
from app.services.authorization import authorization_context_for_user
from app.services import workspace_policy as workspace_policy_service
from app.services.workspace_policy import (
    WORKSPACE_MODULES,
    WorkspaceSnapshotUnavailable,
    default_role_modules,
    effective_workspace,
    workspace_registry_response,
)


@pytest.fixture()
def workspace_client(db_session: Session):
    application = FastAPI()
    install_api_error_handlers(application)

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get(
            "X-Request-ID", f"workspace-test-{uuid.uuid4()}"
        )
        return await call_next(request)

    application.include_router(workspace_router, prefix="/v1")

    def override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as test_client:
        yield test_client


def _role_policy_payload(
    policy: dict,
    *,
    expected_revision: int | None = None,
    module_changes: dict[str, dict[str, object]] | None = None,
) -> dict:
    changes = module_changes or {}
    return {
        "expected_revision": (
            policy["revision"] if expected_revision is None else expected_revision
        ),
        "landing_module_id": policy["landing_module_id"],
        "modules": [
            {**module, **changes.get(module["module_id"], {})}
            for module in policy["modules"]
        ],
        "dashboard_panel_ids": policy["dashboard_panel_ids"],
    }


def _workspace_token(
    db: Session, user_id: uuid.UUID, scopes: list[str]
) -> dict[str, str]:
    value, prefix, token_hash = generate_api_token()
    db.add(
        ApiToken(
            user_id=user_id,
            name="workspace-scoped",
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.flush()
    return {"Authorization": f"Bearer {value}"}


def test_workspace_registry_and_defaults_preserve_current_navigation(
    workspace_client,
    db_session,
    seed_users,
    auth_headers,
):
    registry_response = workspace_client.get(
        "/v1/workspace/modules", headers=auth_headers["admin"]
    )
    assert registry_response.status_code == 200
    registry = registry_response.json()
    assert {module["id"] for module in registry["modules"]} == {
        module.id for module in WORKSPACE_MODULES
    }
    assert all(module["route"].startswith("/") for module in registry["modules"])
    assert {panel["id"] for panel in registry["dashboard_panels"]} == {
        "rss",
        "alerts",
        "notes",
        "daily_brief",
    }

    policies_response = workspace_client.get(
        "/v1/workspace/role-policies", headers=auth_headers["admin"]
    )
    assert policies_response.status_code == 200
    policies = {policy["role"]: policy for policy in policies_response.json()}
    assert set(policies) == {"admin", "analyst", "viewer"}
    assert all(module["visible"] for module in policies["admin"]["modules"])

    expected_primary = {
        "primary.dashboard",
        "primary.alerts",
        "primary.investigations",
        "primary.feeds",
        "primary.stats",
        "primary.export",
        "primary.reporting",
        "primary.settings",
    }
    viewer_visible = {
        module["module_id"]
        for module in policies["viewer"]["modules"]
        if module["visible"]
    }
    assert expected_primary <= viewer_visible
    assert {
        "settings.account",
        "settings.tokens",
        "settings.integrations",
        "settings.integrations.webhooks",
    } <= viewer_visible
    assert "settings.integrations.smtp" not in viewer_visible

    invalid_role = workspace_client.get(
        "/v1/workspace/role-policies/operator", headers=auth_headers["admin"]
    )
    assert invalid_role.status_code == 404
    assert invalid_role.json()["error"]["code"] == "workspace_role_invalid"

    read_only_headers = _workspace_token(
        db_session,
        seed_users["admin"].id,
        [SCOPE_READ_WORKSPACE],
    )
    assert (
        workspace_client.get(
            "/v1/workspace/effective", headers=read_only_headers
        ).status_code
        == 200
    )
    denied_write = workspace_client.put(
        "/v1/workspace/role-policies/viewer",
        headers=read_only_headers,
        json=_role_policy_payload(policies["viewer"]),
    )
    assert denied_write.status_code == 403
    assert denied_write.json()["error"]["code"] == "permission_denied"


def test_personal_preference_scope_cannot_change_organization_policy(
    workspace_client,
    db_session,
    seed_users,
):
    preference_headers = _workspace_token(
        db_session,
        seed_users["admin"].id,
        [SCOPE_READ_WORKSPACE, SCOPE_WRITE_WORKSPACE_PREFERENCES],
    )
    preferences = workspace_client.get(
        "/v1/workspace/preferences", headers=preference_headers
    )
    assert preferences.status_code == 200
    updated_preferences = workspace_client.put(
        "/v1/workspace/preferences",
        headers=preference_headers,
        json={
            "expected_revision": preferences.json()["revision"],
            "landing_module_id": "primary.dashboard",
            "modules": [{"module_id": "primary.stats", "visible": False}],
            "dashboard_panel_ids": ["rss"],
        },
    )
    assert updated_preferences.status_code == 200

    policy = workspace_client.get(
        "/v1/workspace/role-policies/admin", headers=preference_headers
    ).json()
    denied_policy_write = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=preference_headers,
        json=_role_policy_payload(policy),
    )
    assert denied_policy_write.status_code == 403
    assert denied_policy_write.json()["error"]["code"] == "permission_denied"


def test_workspace_mutations_are_revisioned_effective_and_audited(
    workspace_client,
    db_session,
    seed_users,
    auth_headers,
):
    headers = {**auth_headers["admin"], "X-Request-ID": "workspace-policy-test"}
    policy = workspace_client.get(
        "/v1/workspace/role-policies/admin", headers=headers
    ).json()
    update_payload = _role_policy_payload(
        policy,
        module_changes={"primary.feeds": {"visible": False}},
    )
    updated_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=update_payload,
    )
    assert updated_response.status_code == 200
    assert updated_response.headers["X-Current-Revision"] == "2"
    updated_policy = updated_response.json()
    assert updated_policy["revision"] == 2

    stale_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json={**update_payload, "expected_revision": 1},
    )
    assert stale_response.status_code == 409
    assert stale_response.headers["X-Current-Revision"] == "2"
    assert (
        stale_response.json()["error"]["code"] == "workspace_policy_revision_conflict"
    )

    preference_response = workspace_client.put(
        "/v1/workspace/preferences",
        headers=headers,
        json={
            "expected_revision": 0,
            "landing_module_id": "primary.dashboard",
            "modules": [{"module_id": "primary.stats", "visible": False, "order": 1}],
            "dashboard_panel_ids": ["rss", "notes"],
        },
    )
    assert preference_response.status_code == 200
    assert preference_response.headers["X-Current-Revision"] == "1"

    stale_preference_response = workspace_client.put(
        "/v1/workspace/preferences",
        headers=headers,
        json={
            "expected_revision": 0,
            "landing_module_id": "primary.dashboard",
            "modules": [],
            "dashboard_panel_ids": ["rss"],
        },
    )
    assert stale_preference_response.status_code == 409
    assert stale_preference_response.headers["X-Current-Revision"] == "1"
    assert (
        stale_preference_response.json()["error"]["code"]
        == "workspace_preference_revision_conflict"
    )

    effective_response = workspace_client.get(
        "/v1/workspace/effective", headers=headers
    )
    assert effective_response.status_code == 200
    effective = effective_response.json()
    modules = {module["id"]: module for module in effective["modules"]}
    assert modules["primary.dashboard"]["visible"] is True
    assert modules["primary.feeds"]["visible"] is False
    assert "policy_hidden" in modules["primary.feeds"]["reasons"]
    assert modules["primary.stats"]["visible"] is False
    assert "preference_hidden" in modules["primary.stats"]["reasons"]
    assert modules["settings.ai"]["visible"] is False
    assert "feature_unavailable" in modules["settings.ai"]["reasons"]
    assert effective["landing_module_id"] == "primary.dashboard"
    assert effective["dashboard_panel_ids"] == ["rss", "notes"]

    mandatory_stats_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=_role_policy_payload(
            updated_policy,
            module_changes={"primary.stats": {"optional": False}},
        ),
    )
    assert mandatory_stats_response.status_code == 200
    mandatory_effective = workspace_client.get(
        "/v1/workspace/effective", headers=headers
    ).json()
    mandatory_modules = {
        module["id"]: module for module in mandatory_effective["modules"]
    }
    assert mandatory_modules["primary.stats"]["visible"] is True
    assert mandatory_modules["primary.stats"]["order"] == 40
    assert "preference_hidden" not in mandatory_modules["primary.stats"]["reasons"]
    assert (
        "ignored_non_optional_preference:primary.stats"
        in mandatory_effective["warnings"]
    )

    fixed_module_response = workspace_client.put(
        "/v1/workspace/preferences",
        headers=headers,
        json={
            "expected_revision": 1,
            "landing_module_id": "primary.dashboard",
            "modules": [{"module_id": "primary.dashboard", "visible": False}],
            "dashboard_panel_ids": ["rss"],
        },
    )
    assert fixed_module_response.status_code == 409
    assert (
        fixed_module_response.json()["error"]["code"]
        == "workspace_module_not_customizable"
    )

    db_session.expire_all()
    audit_rows = db_session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action.in_(
                {
                    "workspace.role_policy.update",
                    "workspace.preferences.update",
                }
            )
        )
        .order_by(AuditLog.created_at)
    ).all()
    assert sum(row.success for row in audit_rows) == 3
    assert sum(not row.success for row in audit_rows) == 3
    assert all(row.actor_principal_type == "user" for row in audit_rows)
    assert all(row.actor_principal_id == seed_users["admin"].id for row in audit_rows)
    assert all(row.credential_kind == "api_token" for row in audit_rows)
    assert all(row.credential_id is not None for row in audit_rows)
    assert all(row.request_id == "workspace-policy-test" for row in audit_rows)
    assert {
        row.metadata_json.get("reason") for row in audit_rows if not row.success
    } == {
        "workspace_policy_revision_conflict",
        "workspace_preference_revision_conflict",
        "workspace_module_not_customizable",
    }


def test_workspace_rejects_untrusted_input_and_preserves_future_ids(
    workspace_client,
    db_session,
    auth_headers,
):
    headers = auth_headers["admin"]
    row = db_session.get(WorkspaceRolePolicy, "admin")
    row.modules_json = {
        **row.modules_json,
        "future.timeline": {
            "visible": True,
            "optional": True,
            "order": 500,
            "mobile_priority": 500,
        },
    }
    row.landing_module_id = "future.timeline"
    row.dashboard_panel_ids_json = [*row.dashboard_panel_ids_json, "future-map"]
    db_session.commit()

    policy_response = workspace_client.get(
        "/v1/workspace/role-policies/admin", headers=headers
    )
    assert policy_response.status_code == 200
    policy = policy_response.json()
    assert policy["unknown_module_ids"] == ["future.timeline"]
    assert policy["unknown_dashboard_panel_ids"] == ["future-map"]
    assert "unknown_landing_module:future.timeline" in policy["warnings"]

    update_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=_role_policy_payload(policy),
    )
    assert update_response.status_code == 200
    db_session.expire_all()
    preserved = db_session.get(WorkspaceRolePolicy, "admin")
    assert "future.timeline" in preserved.modules_json
    assert preserved.landing_module_id == "future.timeline"
    assert "future-map" in preserved.dashboard_panel_ids_json

    bad_payload = _role_policy_payload(update_response.json())
    bad_payload["modules"].append(
        {
            "module_id": "attacker.component",
            "visible": True,
            "optional": True,
            "order": 999,
            "mobile_priority": 999,
        }
    )
    unknown_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=bad_payload,
    )
    assert unknown_response.status_code == 422
    assert unknown_response.json()["error"]["code"] == "workspace_unknown_module"

    incomplete_payload = _role_policy_payload(update_response.json())
    incomplete_payload["modules"] = incomplete_payload["modules"][:-1]
    incomplete_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=incomplete_payload,
    )
    assert incomplete_response.status_code == 422
    assert (
        incomplete_response.json()["error"]["code"]
        == "workspace_policy_module_set_incomplete"
    )

    unknown_panel_payload = _role_policy_payload(update_response.json())
    unknown_panel_payload["dashboard_panel_ids"] = ["rss", "remote-widget"]
    unknown_panel_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=unknown_panel_payload,
    )
    assert unknown_panel_response.status_code == 422
    assert (
        unknown_panel_response.json()["error"]["code"]
        == "workspace_unknown_dashboard_panel"
    )

    hidden_parent_payload = _role_policy_payload(
        update_response.json(),
        module_changes={"primary.settings": {"visible": False}},
    )
    hidden_parent_payload["landing_module_id"] = "settings.account"
    hidden_parent_response = workspace_client.put(
        "/v1/workspace/role-policies/admin",
        headers=headers,
        json=hidden_parent_payload,
    )
    assert hidden_parent_response.status_code == 422
    assert (
        hidden_parent_response.json()["error"]["code"]
        == "workspace_landing_module_unavailable"
    )

    extra_field_response = workspace_client.put(
        "/v1/workspace/preferences",
        headers=headers,
        json={
            "expected_revision": 0,
            "landing_module_id": None,
            "modules": [],
            "dashboard_panel_ids": None,
            "component": "ArbitraryComponent",
        },
    )
    assert extra_field_response.status_code == 422
    assert extra_field_response.json()["error"]["code"] == "validation_error"

    reset_response = workspace_client.post(
        "/v1/workspace/role-policies/admin/reset",
        headers=headers,
        json={"expected_revision": update_response.json()["revision"]},
    )
    assert reset_response.status_code == 200
    reset_policy = reset_response.json()
    assert reset_policy["unknown_module_ids"] == []
    assert reset_policy["unknown_dashboard_panel_ids"] == []
    assert reset_policy["landing_module_id"] == "primary.dashboard"


def test_workspace_registry_matches_frontend_routes_and_panel_ids():
    registry = workspace_registry_response()
    expected_routes = {
        "primary.dashboard": "/",
        "primary.alerts": "/alerts",
        "primary.investigations": "/investigations",
        "primary.feeds": "/feeds",
        "primary.stats": "/stats",
        "primary.export": "/export",
        "primary.reporting": "/reporting",
        "primary.settings": "/settings",
        "settings.account": "/settings/account",
        "settings.tokens": "/settings/tokens",
        "settings.ai": "/settings/ai",
        "settings.tagging": "/settings/tagging",
        "settings.identity": "/settings/identity",
        "settings.users": "/settings/users",
        "settings.audit": "/settings/audit-logs",
        "settings.operations": "/settings/operations",
        "settings.integrations": "/settings/integrations",
        "settings.integrations.webhooks": "/settings/integrations/webhooks",
        "settings.integrations.smtp": "/settings/integrations/smtp",
    }
    modules = {module.id: module for module in registry.modules}
    assert len(modules) == len(registry.modules)
    assert {module.route for module in registry.modules} == set(
        expected_routes.values()
    )
    assert {module_id: module.route for module_id, module in modules.items()} == (
        expected_routes
    )
    assert all(
        module.parent_id is None or module.parent_id in modules
        for module in registry.modules
    )
    assert len({panel.id for panel in registry.dashboard_panels}) == len(
        registry.dashboard_panels
    )
    assert {panel.id for panel in registry.dashboard_panels} == {
        "rss",
        "alerts",
        "notes",
        "daily_brief",
    }

    tagging = modules["settings.tagging"]
    assert tagging.required_permission == "read:tagging"
    assert tagging.required_permissions == ["read:tagging"]
    assert modules["primary.alerts"].required_permissions == [
        "read:alerts",
        "read:items",
    ]
    daily_brief = next(
        panel for panel in registry.dashboard_panels if panel.id == "daily_brief"
    )
    assert daily_brief.required_permission == "read:items"
    assert daily_brief.required_permissions == ["read:items"]


def test_workspace_resets_are_revisioned_atomic_and_audited(
    workspace_client,
    db_session,
    seed_users,
    auth_headers,
):
    headers = {**auth_headers["admin"], "X-Request-ID": "workspace-reset-test"}
    policy = workspace_client.get(
        "/v1/workspace/role-policies/viewer", headers=headers
    ).json()
    configured = workspace_client.put(
        "/v1/workspace/role-policies/viewer",
        headers=headers,
        json=_role_policy_payload(
            policy,
            module_changes={"primary.feeds": {"visible": False}},
        ),
    ).json()
    preferences = workspace_client.put(
        "/v1/workspace/preferences",
        headers=headers,
        json={
            "expected_revision": 0,
            "landing_module_id": "primary.dashboard",
            "modules": [{"module_id": "primary.stats", "visible": False}],
            "dashboard_panel_ids": ["rss"],
        },
    ).json()

    stale_policy = workspace_client.post(
        "/v1/workspace/role-policies/viewer/reset",
        headers=headers,
        json={"expected_revision": configured["revision"] - 1},
    )
    assert stale_policy.status_code == 409
    assert stale_policy.json()["error"]["code"] == (
        "workspace_policy_revision_conflict"
    )
    reset_policy = workspace_client.post(
        "/v1/workspace/role-policies/viewer/reset",
        headers=headers,
        json={"expected_revision": configured["revision"]},
    )
    assert reset_policy.status_code == 200
    assert reset_policy.headers["X-Current-Revision"] == "3"
    reset_policy_body = reset_policy.json()
    assert reset_policy_body["modules"] == [
        {
            "module_id": module_id,
            **values,
        }
        for module_id, values in default_role_modules("viewer").items()
    ]

    stale_preferences = workspace_client.post(
        "/v1/workspace/preferences/reset",
        headers=headers,
        json={"expected_revision": preferences["revision"] - 1},
    )
    assert stale_preferences.status_code == 409
    assert stale_preferences.json()["error"]["code"] == (
        "workspace_preference_revision_conflict"
    )
    reset_preferences = workspace_client.post(
        "/v1/workspace/preferences/reset",
        headers=headers,
        json={"expected_revision": preferences["revision"]},
    )
    assert reset_preferences.status_code == 200
    assert reset_preferences.headers["X-Current-Revision"] == "0"
    assert reset_preferences.json()["revision"] == 0
    assert db_session.get(WorkspaceUserPreference, seed_users["admin"].id) is None

    db_session.expire_all()
    audits = db_session.scalars(
        select(AuditLog)
        .where(
            AuditLog.request_id == "workspace-reset-test",
            AuditLog.action.in_(
                {"workspace.role_policy.reset", "workspace.preferences.reset"}
            ),
        )
        .order_by(AuditLog.created_at)
    ).all()
    assert [(row.action, row.success) for row in audits] == [
        ("workspace.role_policy.reset", False),
        ("workspace.role_policy.reset", True),
        ("workspace.preferences.reset", False),
        ("workspace.preferences.reset", True),
    ]


@pytest.mark.parametrize("user_key", ["analyst", "viewer"])
def test_workspace_legacy_roles_support_scoped_tokens_and_attenuation(
    workspace_client,
    db_session,
    seed_users,
    user_key,
):
    user = seed_users[user_key]
    preference_headers = _workspace_token(
        db_session,
        user.id,
        [SCOPE_READ_WORKSPACE, SCOPE_WRITE_WORKSPACE_PREFERENCES],
    )
    assert (
        workspace_client.get(
            "/v1/workspace/preferences", headers=preference_headers
        ).status_code
        == 200
    )
    created = workspace_client.put(
        "/v1/workspace/preferences",
        headers=preference_headers,
        json={
            "expected_revision": 0,
            "landing_module_id": None,
            "modules": [{"module_id": "primary.stats", "order": 2}],
            "dashboard_panel_ids": None,
        },
    )
    assert created.status_code == 200, created.text

    attenuated_headers = _workspace_token(
        db_session,
        user.id,
        [SCOPE_READ_WORKSPACE],
    )
    denied = workspace_client.post(
        "/v1/workspace/preferences/reset",
        headers=attenuated_headers,
        json={"expected_revision": created.json()["revision"]},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"


def test_workspace_all_of_permissions_fail_closed_with_diagnostics(
    workspace_client,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    role = IAMRole(
        key=f"workspace-alert-reader-{uuid.uuid4().hex[:8]}",
        name="Workspace alert reader",
        description="Partial workflow permission test",
        is_system=False,
        created_by_user_id=seed_users["admin"].id,
    )
    db_session.add(role)
    db_session.flush()
    db_session.add_all(
        [
            IAMRolePermission(role_id=role.id, permission="read:alerts"),
            IAMUserRoleAssignment(
                user_id=viewer.id,
                role_id=role.id,
                source="local",
                source_key="",
                assigned_by_user_id=seed_users["admin"].id,
            ),
        ]
    )
    policy_state = db_session.get(IAMPolicyState, 1)
    policy_state.revision += 1
    viewer_policy = db_session.get(WorkspaceRolePolicy, "viewer")
    viewer_policy.dashboard_panel_ids_json = ["rss", "alerts"]
    db_session.flush()

    partial_headers = _workspace_token(
        db_session,
        viewer.id,
        [SCOPE_READ_WORKSPACE, "read:alerts"],
    )
    partial = workspace_client.get("/v1/workspace/effective", headers=partial_headers)
    assert partial.status_code == 200
    body = partial.json()
    alerts_module = next(
        module for module in body["modules"] if module["id"] == "primary.alerts"
    )
    assert alerts_module["visible"] is False
    assert alerts_module["missing_permissions"] == ["read:items"]
    assert "permission_missing" in alerts_module["reasons"]
    alerts_panel = next(
        panel for panel in body["dashboard_panels"] if panel["id"] == "alerts"
    )
    assert alerts_panel["visible"] is False
    assert alerts_panel["missing_permissions"] == ["read:items"]
    assert "alerts" not in body["dashboard_panel_ids"]

    complete_headers = _workspace_token(
        db_session,
        viewer.id,
        [SCOPE_READ_WORKSPACE, "read:alerts", "read:items"],
    )
    complete = workspace_client.get(
        "/v1/workspace/effective", headers=complete_headers
    ).json()
    complete_alerts = next(
        module for module in complete["modules"] if module["id"] == "primary.alerts"
    )
    assert complete_alerts["visible"] is True
    assert complete_alerts["missing_permissions"] == []
    assert "alerts" in complete["dashboard_panel_ids"]


def test_workspace_future_preferences_survive_cycles_then_reset(
    workspace_client,
    db_session,
    seed_users,
    auth_headers,
):
    user = seed_users["admin"]
    db_session.add(
        WorkspaceUserPreference(
            user_id=user.id,
            modules_json={
                "future.timeline": {"visible": False, "order": 700},
                "primary.stats": {"order": 3},
            },
            landing_module_id=None,
            dashboard_panel_ids_json=["rss", "future-map"],
            revision=1,
            updated_by_user_id=user.id,
        )
    )
    db_session.flush()
    headers = auth_headers["admin"]

    for expected_revision in (1, 2):
        response = workspace_client.put(
            "/v1/workspace/preferences",
            headers=headers,
            json={
                "expected_revision": expected_revision,
                "landing_module_id": None,
                "modules": [
                    {"module_id": "primary.stats", "order": expected_revision + 3}
                ],
                "dashboard_panel_ids": ["rss"],
            },
        )
        assert response.status_code == 200
        db_session.expire_all()
        stored = db_session.get(WorkspaceUserPreference, user.id)
        assert "future.timeline" in stored.modules_json
        assert "future-map" in stored.dashboard_panel_ids_json

    reset = workspace_client.post(
        "/v1/workspace/preferences/reset",
        headers=headers,
        json={"expected_revision": 3},
    )
    assert reset.status_code == 200
    db_session.expire_all()
    assert db_session.get(WorkspaceUserPreference, user.id) is None


def test_workspace_audit_failure_rolls_back_successful_mutation(
    workspace_client,
    db_session,
    auth_headers,
    monkeypatch,
):
    policy = workspace_client.get(
        "/v1/workspace/role-policies/viewer", headers=auth_headers["admin"]
    ).json()
    configured = workspace_client.put(
        "/v1/workspace/role-policies/viewer",
        headers=auth_headers["admin"],
        json=_role_policy_payload(
            policy,
            module_changes={"primary.feeds": {"visible": False}},
        ),
    ).json()

    def fail_audit(*_args, **_kwargs):
        raise SQLAlchemyError("audit storage unavailable")

    monkeypatch.setattr(workspace_routes, "_record_request_audit", fail_audit)
    response = workspace_client.post(
        "/v1/workspace/role-policies/viewer/reset",
        headers=auth_headers["admin"],
        json={"expected_revision": configured["revision"]},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "workspace_storage_unavailable"
    db_session.expire_all()
    stored = db_session.get(WorkspaceRolePolicy, "viewer")
    assert stored.revision == configured["revision"]
    assert stored.modules_json["primary.feeds"]["visible"] is False


def test_effective_workspace_retries_a_real_concurrent_policy_commit(
    database_engine,
    monkeypatch,
):
    session_factory = sessionmaker(bind=database_engine, class_=Session)
    user_id = uuid.uuid4()
    setup = session_factory()
    original_policy = setup.get(WorkspaceRolePolicy, "viewer")
    original_modules = {
        module_id: dict(values)
        for module_id, values in original_policy.modules_json.items()
    }
    original_revision = original_policy.revision
    original_updater = original_policy.updated_by_user_id
    setup.add(
        User(
            id=user_id,
            email=f"workspace-concurrency-{user_id}@example.com",
            password_hash="test-only",
            role="viewer",
            is_active=True,
            is_approved=True,
        )
    )
    setup.commit()
    setup.close()

    snapshot_loaded = Barrier(2)
    writer_committed = Barrier(2)
    original_revision_pair = workspace_policy_service._workspace_revision_pair
    first_verification = True

    def coordinated_revision_pair(db, *, role, user_id):
        nonlocal first_verification
        if first_verification:
            first_verification = False
            snapshot_loaded.wait(timeout=5)
            writer_committed.wait(timeout=5)
        return original_revision_pair(db, role=role, user_id=user_id)

    monkeypatch.setattr(
        workspace_policy_service,
        "_workspace_revision_pair",
        coordinated_revision_pair,
    )
    result: dict[str, object] = {}

    def read_effective_workspace():
        reader = session_factory()
        try:
            user = reader.get(User, user_id)
            authorization = authorization_context_for_user(reader, user)
            result["workspace"] = effective_workspace(
                reader,
                user=user,
                authorization=authorization,
                feature_flags={
                    "ai_enabled": False,
                    "ai_daily_brief_enabled": False,
                },
            )
        except Exception as exc:  # pragma: no cover - asserted in the parent thread
            result["error"] = exc
        finally:
            reader.close()

    thread = Thread(target=read_effective_workspace)
    thread.start()
    writer = session_factory()
    try:
        snapshot_loaded.wait(timeout=5)
        policy = writer.get(WorkspaceRolePolicy, "viewer")
        changed_modules = {
            module_id: dict(values) for module_id, values in policy.modules_json.items()
        }
        changed_modules["primary.feeds"]["visible"] = False
        policy.modules_json = changed_modules
        policy.revision += 1
        writer.commit()
        writer_committed.wait(timeout=5)
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert "error" not in result
        workspace = result["workspace"]
        assert workspace.policy_revision == original_revision + 1
        feeds = next(
            module for module in workspace.modules if module.id == "primary.feeds"
        )
        assert feeds.visible is False
    finally:
        if thread.is_alive():
            writer_committed.abort()
            thread.join(timeout=5)
        cleanup = session_factory()
        cleanup_policy = cleanup.get(WorkspaceRolePolicy, "viewer")
        cleanup_policy.modules_json = original_modules
        cleanup_policy.revision = original_revision
        cleanup_policy.updated_by_user_id = original_updater
        cleanup_user = cleanup.get(User, user_id)
        if cleanup_user is not None:
            cleanup.delete(cleanup_user)
        cleanup.commit()
        cleanup.close()
        writer.close()


def test_effective_workspace_fails_closed_after_repeated_revision_churn(
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["viewer"]
    authorization = authorization_context_for_user(db_session, user)

    def always_changed(*_args, **_kwargs):
        return -1, -1

    monkeypatch.setattr(
        workspace_policy_service,
        "_workspace_revision_pair",
        always_changed,
    )
    with pytest.raises(WorkspaceSnapshotUnavailable) as exc_info:
        effective_workspace(
            db_session,
            user=user,
            authorization=authorization,
            feature_flags={
                "ai_enabled": False,
                "ai_daily_brief_enabled": False,
            },
        )
    assert exc_info.value.code == "workspace_snapshot_unavailable"
    assert exc_info.value.status_code == 503
