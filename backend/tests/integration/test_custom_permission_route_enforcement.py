from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog


def test_ungranted_viewer_remains_denied_tag_creation(
    client, auth_headers, db_session, seed_users
):
    response = client.post(
        "/tags",
        headers=auth_headers["viewer"],
        json={"name": f"ungranted-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert response.json()["error"]["context"]["missing_permissions"] == ["write:tags"]
    denial = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "authorization.permission_denied",
            AuditLog.actor_user_id == seed_users["viewer"].id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert denial is not None
    assert denial.success is False
    assert denial.resource_id == "/tags"
    assert denial.metadata_json["missing_permissions"] == ["write:tags"]


def test_custom_role_grant_reaches_route_and_token_scope_still_attenuates(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    role_response = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": f"tag-writer-{uuid.uuid4().hex}",
            "name": "Tag writer",
            "permissions": ["write:tags"],
        },
    )
    assert role_response.status_code == 201
    role = role_response.json()

    assignment_response = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={
            "role_id": role["id"],
            "expected_role_revision": role["revision"],
        },
    )
    assert assignment_response.status_code == 201

    allowed = client.post(
        "/tags",
        headers=auth_headers["viewer"],
        json={"name": f"custom-grant-{uuid.uuid4().hex}"},
    )
    assert allowed.status_code == 201

    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=seed_users["viewer"].id,
            name="attenuated-tag-reader",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=["read:tags"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()

    denied = client.post(
        "/tags",
        headers={"Authorization": f"Bearer {token_value}"},
        json={"name": f"attenuated-{uuid.uuid4().hex}"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    assert denied.json()["error"]["context"]["missing_permissions"] == ["write:tags"]


def test_custom_audit_reader_can_query_but_not_mutate_iam(
    client, auth_headers, seed_users
):
    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": f"audit-reader-{uuid.uuid4().hex}",
            "name": "Audit reader",
            "permissions": ["read:audit"],
        },
    ).json()
    assigned = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={"role_id": role["id"], "expected_role_revision": role["revision"]},
    )
    assert assigned.status_code == 201

    assert client.get("/audit-logs", headers=auth_headers["viewer"]).status_code == 200
    denied = client.post(
        "/iam/roles",
        headers=auth_headers["viewer"],
        json={"key": "not-allowed", "name": "Not allowed", "permissions": []},
    )
    assert denied.status_code == 403


def test_delegated_iam_manager_cannot_mint_or_self_assign_new_permissions(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    manager_response = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": f"iam-manager-{uuid.uuid4().hex}",
            "name": "Delegated IAM manager",
            "permissions": ["write:iam"],
        },
    )
    assert manager_response.status_code == 201, manager_response.text
    manager_role = manager_response.json()
    audit_response = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": f"delegation-target-{uuid.uuid4().hex}",
            "name": "Delegation target",
            "permissions": ["read:audit"],
        },
    )
    assert audit_response.status_code == 201, audit_response.text
    audit_role = audit_response.json()
    assigned = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={
            "role_id": manager_role["id"],
            "expected_role_revision": manager_role["revision"],
        },
    )
    assert assigned.status_code == 201

    minted = client.post(
        "/iam/roles",
        headers=auth_headers["viewer"],
        json={
            "key": f"self-escalation-{uuid.uuid4().hex}",
            "name": "Self escalation",
            "permissions": ["read:audit"],
        },
    )
    assert minted.status_code == 403
    assert minted.json()["error"]["code"] == "iam_delegation_denied"
    assert minted.json()["error"]["context"]["missing_permissions"] == ["read:audit"]

    self_assigned = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["viewer"],
        json={
            "role_id": audit_role["id"],
            "expected_role_revision": audit_role["revision"],
        },
    )
    assert self_assigned.status_code == 403
    assert self_assigned.json()["error"]["code"] == "iam_delegation_denied"

    rejection = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "iam.user_role.assign",
            AuditLog.actor_user_id == seed_users["viewer"].id,
            AuditLog.success.is_(False),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert rejection is not None
    assert rejection.metadata_json["reason"] == "iam_delegation_denied"
    assert rejection.metadata_json["missing_permissions"] == ["read:audit"]


def test_sealed_role_denial_is_audited_with_route_context(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    denied = client.get(
        "/alerts/occurrences/evaluations", headers=auth_headers["viewer"]
    )

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "alert_evaluation_admin_required"
    assert denied.json()["error"]["context"]["required_legacy_roles"] == ["admin"]
    rejection = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "authorization.role_denied",
            AuditLog.actor_user_id == seed_users["viewer"].id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert rejection is not None
    assert rejection.resource_id == "/alerts/occurrences/evaluations"
    assert rejection.metadata_json["required_legacy_roles"] == ["admin"]
    assert rejection.metadata_json["actual_legacy_role"] == "viewer"
