from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core.security import generate_api_token
from app.models.api_token import ApiToken


def test_ungranted_viewer_remains_denied_tag_creation(client, auth_headers):
    response = client.post(
        "/tags",
        headers=auth_headers["viewer"],
        json={"name": f"ungranted-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert response.json()["error"]["context"]["missing_permissions"] == ["write:tags"]


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
