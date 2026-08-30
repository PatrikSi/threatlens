from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.auth_session import AuthSession
from app.models.oidc import ExternalIdentity, OIDCProvider


def test_user_directory_is_paginated_searchable_and_backward_compatible(
    client: TestClient,
    auth_headers,
    seed_users,
):
    _ = seed_users
    first_page = client.get(
        "/users/directory",
        params={"limit": 2},
        headers=auth_headers["admin"],
    )

    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["total"] == 3
    assert first_page.json()["limit"] == 2
    assert first_page.json()["offset"] == 0
    assert first_page.json()["has_more"] is True
    assert len(first_page.json()["users"]) == 2

    second_page = client.get(
        "/users/directory",
        params={"limit": 2, "offset": 2},
        headers=auth_headers["admin"],
    )
    assert second_page.status_code == 200
    assert second_page.json()["has_more"] is False
    assert len(second_page.json()["users"]) == 1

    search = client.get(
        "/users/directory",
        params={"q": "ADMIN@EXAMPLE", "role": "admin"},
        headers=auth_headers["admin"],
    )
    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["users"][0]["email"] == "admin@example.com"

    legacy = client.get("/users", headers=auth_headers["admin"])
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert len(legacy.json()) == 3


def test_user_directory_rejects_invalid_filters_and_non_admin_access(
    client: TestClient,
    auth_headers,
    seed_users,
):
    _ = seed_users
    invalid = client.get(
        "/users/directory",
        params={"provisioning_source": "unknown"},
        headers=auth_headers["admin"],
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == (
        "Invalid user-directory provisioning source filter"
    )

    denied = client.get(
        "/users/directory",
        headers=auth_headers["analyst"],
    )
    assert denied.status_code == 403


def test_user_directory_excludes_sessions_from_an_old_auth_generation(
    client: TestClient,
    db_session: Session,
    auth_headers,
    seed_users,
):
    admin = seed_users["admin"]
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            AuthSession(
                user_id=admin.id,
                token_hash="a" * 64,
                auth_token_version=admin.auth_token_version,
                auth_method="local",
                authenticated_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(hours=1),
                absolute_expires_at=now + timedelta(days=1),
            ),
            AuthSession(
                user_id=admin.id,
                token_hash="b" * 64,
                auth_token_version=admin.auth_token_version + 1,
                auth_method="local",
                authenticated_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(hours=1),
                absolute_expires_at=now + timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    response = client.get(
        "/users/directory",
        params={"q": admin.email},
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["users"][0]["active_session_count"] == 1


def test_user_directory_distinguishes_disabled_linked_oidc_identity(
    client: TestClient,
    db_session: Session,
    auth_headers,
    seed_users,
):
    analyst = seed_users["analyst"]
    provider = OIDCProvider(
        system_key="primary",
        name="Company SSO",
        enabled=False,
        issuer_url="https://idp.example.com",
        client_id="threatlens",
        public_base_url="https://threatlens.example.com",
        scopes=["openid", "email"],
    )
    db_session.add(provider)
    db_session.flush()
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=analyst.id,
            issuer=provider.issuer_url,
            subject="analyst-subject",
            email_at_link=analyst.email,
        )
    )
    db_session.commit()

    response = client.get(
        "/users/directory",
        params={"q": analyst.email},
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200, response.text
    account = response.json()["users"][0]
    assert account["identity_linked"] is True
    assert account["sso_sign_in_available"] is False
    assert account["oidc_identity_status"] == "linked_unavailable"
    assert account["role_managed_by"] == "local"
