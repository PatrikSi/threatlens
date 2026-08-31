from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog


def test_custom_role_direct_and_group_assignments_are_additive(
    client, auth_headers, seed_users
):
    roles_response = client.get("/iam/roles", headers=auth_headers["admin"])
    assert roles_response.status_code == 200
    assert {role["key"] for role in roles_response.json()} >= {
        "admin",
        "analyst",
        "viewer",
    }
    built_in_viewer = next(
        role for role in roles_response.json() if role["key"] == "viewer"
    )
    assert built_in_viewer["id"]
    assert built_in_viewer["assignment_count"] >= 1

    role_response = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "integration-operator",
            "name": "Integration operator",
            "description": "Manages outbound connectors.",
            "permissions": ["read:integrations", "write:integrations"],
        },
    )
    assert role_response.status_code == 201
    role = role_response.json()
    assert role["permissions"] == ["read:integrations", "write:integrations"]

    viewer_id = str(seed_users["viewer"].id)
    assignment_response = client.post(
        f"/iam/users/{viewer_id}/role-assignments",
        headers=auth_headers["admin"],
        json={"role_id": role["id"]},
    )
    assert assignment_response.status_code == 201
    assignment = assignment_response.json()

    effective_response = client.get(
        f"/iam/users/{viewer_id}/effective", headers=auth_headers["admin"]
    )
    assert effective_response.status_code == 200
    effective = effective_response.json()
    assert effective["legacy_role"] == "viewer"
    assert effective["account_eligible"] is True
    assert next(item for item in effective["roles"] if item["key"] == "viewer")["id"]
    assert "write:integrations" in effective["permissions"]
    assert any(item["key"] == "integration-operator" for item in effective["roles"])

    remove_response = client.delete(
        f"/iam/users/{viewer_id}/role-assignments/{assignment['id']}",
        headers=auth_headers["admin"],
    )
    assert remove_response.status_code == 204

    group_response = client.post(
        "/iam/groups",
        headers=auth_headers["admin"],
        json={
            "key": "connector-team",
            "name": "Connector team",
            "description": "Outbound delivery operators.",
        },
    )
    assert group_response.status_code == 201
    group = group_response.json()

    member_response = client.post(
        f"/iam/groups/{group['id']}/members",
        headers=auth_headers["admin"],
        json={"user_id": viewer_id, "expected_group_revision": group["revision"]},
    )
    assert member_response.status_code == 201

    group_role_response = client.post(
        f"/iam/groups/{group['id']}/role-assignments",
        headers=auth_headers["admin"],
        json={
            "role_id": role["id"],
            "expected_group_revision": group["revision"] + 1,
            "expected_role_revision": role["revision"],
        },
    )
    assert group_role_response.status_code == 201
    group_after_role = group_role_response.json()

    group_role_assignments = client.get(
        f"/iam/groups/{group['id']}/role-assignments",
        headers=auth_headers["admin"],
    )
    assert group_role_assignments.status_code == 200
    group_role_assignment = group_role_assignments.json()[0]
    assert group_role_assignment["role_id"] == role["id"]
    assert group_role_assignment["role_revision"] == role["revision"]

    grouped_effective = client.get(
        f"/iam/users/{viewer_id}/effective", headers=auth_headers["admin"]
    ).json()
    assert "write:integrations" in grouped_effective["permissions"]
    assert "connector-team" in grouped_effective["groups"]

    remove_group_role = client.delete(
        f"/iam/groups/{group['id']}/role-assignments/{group_role_assignment['id']}?expected_group_revision={group_after_role['revision']}",
        headers=auth_headers["admin"],
    )
    assert remove_group_role.status_code == 204


def test_role_validation_revision_and_system_role_guards(client, auth_headers):
    invalid = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "unsafe-role",
            "name": "Unsafe",
            "permissions": ["*:*"],
        },
    )
    assert invalid.status_code == 422
    assert "sealed administrator" in invalid.text

    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "report-manager",
            "name": "Report manager",
            "permissions": ["read:reports", "write:reports"],
        },
    ).json()
    updated = client.patch(
        f"/iam/roles/{role['id']}",
        headers=auth_headers["admin"],
        json={"expected_revision": role["revision"], "name": "Reporting manager"},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == role["revision"] + 1

    stale = client.patch(
        f"/iam/roles/{role['id']}",
        headers=auth_headers["admin"],
        json={"expected_revision": role["revision"], "description": "stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "iam_role_revision_conflict"
    assert stale.json()["error"]["context"]["current_revision"] == role["revision"] + 1

    system_role = next(
        item
        for item in client.get("/iam/roles", headers=auth_headers["admin"]).json()
        if item["key"] == "admin"
    )
    immutable = client.patch(
        f"/iam/roles/{system_role['id']}",
        headers=auth_headers["admin"],
        json={"expected_revision": system_role["revision"], "name": "Owner"},
    )
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "iam_system_role_immutable"


def test_role_and_group_deletes_require_current_revision(
    client, auth_headers, seed_users
):
    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={"key": "delete-fence-role", "name": "Delete fence", "permissions": []},
    ).json()
    updated_role = client.patch(
        f"/iam/roles/{role['id']}",
        headers=auth_headers["admin"],
        json={"expected_revision": role["revision"], "description": "Changed"},
    ).json()

    missing_role_revision = client.delete(
        f"/iam/roles/{role['id']}", headers=auth_headers["admin"]
    )
    assert missing_role_revision.status_code == 422
    stale_role = client.delete(
        f"/iam/roles/{role['id']}?expected_revision={role['revision']}",
        headers=auth_headers["admin"],
    )
    assert stale_role.status_code == 409
    assert stale_role.json()["error"]["code"] == "iam_role_revision_conflict"
    assert (
        stale_role.json()["error"]["context"]["current_revision"]
        == updated_role["revision"]
    )
    deleted_role = client.delete(
        f"/iam/roles/{role['id']}?expected_revision={updated_role['revision']}",
        headers=auth_headers["admin"],
    )
    assert deleted_role.status_code == 204

    group = client.post(
        "/iam/groups",
        headers=auth_headers["admin"],
        json={"key": "delete-fence-group", "name": "Delete fence group"},
    ).json()
    added = client.post(
        f"/iam/groups/{group['id']}/members",
        headers=auth_headers["admin"],
        json={
            "user_id": str(seed_users["viewer"].id),
            "expected_group_revision": group["revision"],
        },
    )
    assert added.status_code == 201
    current_group = next(
        item
        for item in client.get("/iam/groups", headers=auth_headers["admin"]).json()
        if item["id"] == group["id"]
    )

    stale_member_remove = client.delete(
        f"/iam/groups/{group['id']}/members/{added.json()['id']}?expected_group_revision={group['revision']}",
        headers=auth_headers["admin"],
    )
    assert stale_member_remove.status_code == 409
    assert stale_member_remove.json()["error"]["code"] == "iam_group_revision_conflict"
    assert len(
        client.get(
            f"/iam/groups/{group['id']}/members",
            headers=auth_headers["admin"],
        ).json()
    ) == 1

    stale_group = client.delete(
        f"/iam/groups/{group['id']}?expected_revision={group['revision']}",
        headers=auth_headers["admin"],
    )
    assert stale_group.status_code == 409
    assert stale_group.json()["error"]["code"] == "iam_group_revision_conflict"
    assert (
        stale_group.json()["error"]["context"]["current_revision"]
        == current_group["revision"]
    )
    deleted_group = client.delete(
        f"/iam/groups/{group['id']}?expected_revision={current_group['revision']}",
        headers=auth_headers["admin"],
    )
    assert deleted_group.status_code == 204


def test_user_directory_honors_canonical_read_users_permission(
    client, auth_headers, seed_users
):
    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "directory-reader",
            "name": "Directory reader",
            "permissions": ["read:users"],
        },
    ).json()
    assigned = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={"role_id": role["id"], "expected_role_revision": role["revision"]},
    )
    assert assigned.status_code == 201

    directory = client.get("/users/directory", headers=auth_headers["viewer"])
    assert directory.status_code == 200
    assert {entry["email"] for entry in directory.json()["users"]} >= {
        seed_users["viewer"].email,
        seed_users["admin"].email,
    }
    assert client.get("/users", headers=auth_headers["viewer"]).status_code == 403


def test_iam_permissions_and_mutations_require_administrator(client, auth_headers):
    assert (
        client.get("/iam/effective", headers=auth_headers["viewer"]).status_code == 200
    )
    assert client.get("/iam/roles", headers=auth_headers["viewer"]).status_code == 403
    assert (
        client.get("/iam/permissions", headers=auth_headers["analyst"]).status_code
        == 403
    )


def test_iam_mutation_audit_contains_principal_and_request_context(
    client, auth_headers, db_session
):
    response = client.post(
        "/iam/roles",
        headers={**auth_headers["admin"], "X-Request-ID": "iam-audit-test"},
        json={
            "key": "audit-reader",
            "name": "Audit reader",
            "permissions": ["read:audit"],
        },
    )
    assert response.status_code == 201
    role_id = response.json()["id"]

    entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "iam.roles.create",
            AuditLog.resource_id == role_id,
        )
    )
    assert entry is not None
    assert entry.actor_principal_type == "user"
    assert entry.actor_principal_id == entry.actor_user_id
    assert entry.credential_kind == "api_token"
    assert entry.credential_id is not None
    assert entry.request_id == "iam-audit-test"
    assert entry.source_ip
    assert entry.metadata_json["after"]["key"] == "audit-reader"


def test_effective_access_is_attenuated_by_api_token_scopes(
    client, db_session, seed_users
):
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=seed_users["admin"].id,
            name="iam-read-only",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=["read:iam"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db_session.commit()
    headers = {"Authorization": f"Bearer {token_value}"}

    effective = client.get("/iam/effective", headers=headers)
    assert effective.status_code == 200
    assert effective.json()["credential_limited"] is True
    assert effective.json()["permissions"] == ["read:iam"]

    explanation = client.get(
        "/iam/effective/explain?permission=write:iam", headers=headers
    )
    assert explanation.status_code == 200
    assert explanation.json()["allowed"] is False
    assert explanation.json()["reason"] == "credential_scope_missing"
    assert client.get("/iam/roles", headers=headers).status_code == 200
    denied = client.post(
        "/iam/roles",
        headers=headers,
        json={"key": "denied-role", "name": "Denied", "permissions": []},
    )
    assert denied.status_code == 403


def test_custom_iam_manager_permission_authorizes_iam_routes(
    client, auth_headers, seed_users
):
    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "iam-manager",
            "name": "IAM manager",
            "permissions": ["read:iam", "write:iam"],
        },
    ).json()
    assigned = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={"role_id": role["id"], "expected_role_revision": role["revision"]},
    )
    assert assigned.status_code == 201

    assert client.get("/iam/roles", headers=auth_headers["viewer"]).status_code == 200
    created = client.post(
        "/iam/roles",
        headers=auth_headers["viewer"],
        json={
            "key": "viewer-created",
            "name": "Viewer created",
            "permissions": ["read:reports"],
        },
    )
    assert created.status_code == 201


def test_iam_validation_reference_errors_and_stale_assignment_revision(
    client, auth_headers, seed_users
):
    blank = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={"key": "blank-name", "name": "   ", "permissions": []},
    )
    assert blank.status_code == 422
    extra = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "misspelled",
            "name": "Misspelled",
            "permissions": [],
            "permissons": ["write:iam"],
        },
    )
    assert extra.status_code == 422

    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={"key": "revision-role", "name": "Revision", "permissions": []},
    ).json()
    updated = client.patch(
        f"/iam/roles/{role['id']}",
        headers=auth_headers["admin"],
        json={
            "expected_revision": role["revision"],
            "permissions": ["read:reports"],
        },
    )
    assert updated.status_code == 200
    stale = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={"role_id": role["id"], "expected_role_revision": role["revision"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "iam_role_revision_conflict"

    missing_user = client.post(
        f"/iam/users/{uuid.uuid4()}/role-assignments",
        headers=auth_headers["admin"],
        json={"role_id": role["id"]},
    )
    assert missing_user.status_code == 404
    assert missing_user.json()["error"]["code"] == "iam_user_not_found"


def test_effective_groups_include_membership_without_role_and_inactive_user_fails_closed(
    client, auth_headers, seed_users, db_session
):
    group = client.post(
        "/iam/groups",
        headers=auth_headers["admin"],
        json={"key": "membership-only", "name": "Membership only"},
    ).json()
    added = client.post(
        f"/iam/groups/{group['id']}/members",
        headers=auth_headers["admin"],
        json={
            "user_id": str(seed_users["viewer"].id),
            "expected_group_revision": group["revision"],
        },
    )
    assert added.status_code == 201

    active = client.get(
        f"/iam/users/{seed_users['viewer'].id}/effective",
        headers=auth_headers["admin"],
    ).json()
    assert "membership-only" in active["groups"]
    assert "all-users" in active["groups"]

    seed_users["viewer"].is_active = False
    db_session.add(seed_users["viewer"])
    db_session.commit()
    inactive = client.get(
        f"/iam/users/{seed_users['viewer'].id}/effective",
        headers=auth_headers["admin"],
    ).json()
    assert inactive["account_eligible"] is False
    assert inactive["permissions"] == []
    assert "all-users" not in inactive["groups"]
    assert "membership-only" in inactive["groups"]
