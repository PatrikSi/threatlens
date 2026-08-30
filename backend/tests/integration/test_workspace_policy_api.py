from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.workspace import (
    SCOPE_WRITE_WORKSPACE_PREFERENCES,
    router as workspace_router,
)
from app.core.api_errors import install_api_error_handlers
from app.core.security import generate_api_token
from app.core.token_scopes import SCOPE_READ_WORKSPACE
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.workspace import WorkspaceRolePolicy
from app.services.workspace_policy import (
    WORKSPACE_MODULES,
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


def test_workspace_registry_matches_frontend_routes_and_panel_ids():
    repository_root = Path(__file__).resolve().parents[3]
    route_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            repository_root / "web/src/App.tsx",
            repository_root / "web/src/components/AppShell.tsx",
            repository_root / "web/src/pages/SettingsLayout.tsx",
        )
    )
    panel_source = (repository_root / "web/src/types/savedViews.ts").read_text(
        encoding="utf-8"
    )
    registry = workspace_registry_response()

    for module in registry.modules:
        assert module.route in route_source, module.id
    for panel in registry.dashboard_panels:
        assert f"'{panel.id}'" in panel_source, panel.id

    tagging = next(
        module for module in registry.modules if module.id == "settings.tagging"
    )
    assert tagging.required_permission == "read:tagging"
