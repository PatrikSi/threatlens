from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core.security import generate_api_token
from app.models.api_token import ApiToken


def _grant_custom_role(
    client,
    *,
    admin_headers: dict[str, str],
    user_id: uuid.UUID,
    permissions: list[str],
) -> None:
    role_response = client.post(
        "/iam/roles",
        headers=admin_headers,
        json={
            "key": f"workflow-{uuid.uuid4().hex}",
            "name": "Workflow test role",
            "permissions": permissions,
        },
    )
    assert role_response.status_code == 201, role_response.text
    role = role_response.json()

    assignment_response = client.post(
        f"/iam/users/{user_id}/role-assignments",
        headers=admin_headers,
        json={
            "role_id": role["id"],
            "expected_role_revision": role["revision"],
        },
    )
    assert assignment_response.status_code == 201, assignment_response.text


def _issue_token(db_session, *, user_id: uuid.UUID, scopes: list[str]) -> str:
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=user_id,
            name=f"workflow-enforcement-{uuid.uuid4().hex}",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()
    return token_value


def _private_template_payload(name: str) -> dict[str, object]:
    return {
        "name": name,
        "visibility": "private",
        "sections": [
            {
                "key": "executive_summary",
                "title": "Executive Summary",
                "enabled": True,
            }
        ],
    }


def test_custom_role_viewer_can_use_report_read_write_workflow_and_token_is_attenuated(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    denied_before_grant = client.post(
        "/reports/templates",
        headers=auth_headers["viewer"],
        json=_private_template_payload("Denied before grant"),
    )
    assert denied_before_grant.status_code == 403
    assert denied_before_grant.json()["error"]["code"] == "permission_denied"

    _grant_custom_role(
        client,
        admin_headers=auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        permissions=["write:reports"],
    )

    template_name = f"Custom role template {uuid.uuid4().hex}"
    created = client.post(
        "/reports/templates",
        headers=auth_headers["viewer"],
        json=_private_template_payload(template_name),
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == template_name

    listed = client.get("/reports/templates", headers=auth_headers["viewer"])
    assert listed.status_code == 200
    assert created.json()["id"] in {entry["id"] for entry in listed.json()}

    attenuated_token = _issue_token(
        db_session,
        user_id=seed_users["viewer"].id,
        scopes=["read:reports"],
    )
    denied_by_token = client.post(
        "/reports/templates",
        headers={"Authorization": f"Bearer {attenuated_token}"},
        json=_private_template_payload("Denied by token attenuation"),
    )
    assert denied_by_token.status_code == 403
    assert denied_by_token.json()["error"]["context"]["missing_permissions"] == [
        "write:reports"
    ]


def test_multi_permission_route_denies_a_missing_companion_scope(
    client,
    db_session,
    seed_users,
):
    alerts_only_token = _issue_token(
        db_session,
        user_id=seed_users["admin"].id,
        scopes=["read:alerts"],
    )

    response = client.post(
        "/alerts/preview",
        headers={"Authorization": f"Bearer {alerts_only_token}"},
        json={"category": "workflow-test", "keywords": ["signal"]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert response.json()["error"]["context"]["missing_permissions"] == ["read:items"]


def test_private_investigation_acl_still_hides_resources_from_non_members(
    client,
    auth_headers,
):
    created = client.post(
        "/investigations",
        headers=auth_headers["admin"],
        json={
            "title": f"Private investigation {uuid.uuid4().hex}",
            "description": "Authorization regression coverage.",
            "severity": "high",
            "visibility": "private",
        },
    )
    assert created.status_code == 201, created.text

    hidden = client.get(
        f"/investigations/{created.json()['id']}",
        headers=auth_headers["analyst"],
    )

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "investigation_not_found"


def test_custom_role_can_author_an_investigation_without_legacy_role_promotion(
    client,
    auth_headers,
    seed_users,
):
    _grant_custom_role(
        client,
        admin_headers=auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        permissions=["write:investigations"],
    )

    created = client.post(
        "/investigations",
        headers=auth_headers["viewer"],
        json={
            "title": f"Custom-role investigation {uuid.uuid4().hex}",
            "description": "Custom permission regression coverage.",
            "severity": "medium",
            "visibility": "private",
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["current_user_role"] == "owner"
    assert seed_users["viewer"].role == "viewer"
