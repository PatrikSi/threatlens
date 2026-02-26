from fastapi.testclient import TestClient


def test_viewer_cannot_manage_feeds(client: TestClient, auth_headers):
    response = client.post(
        "/feeds",
        json={
            "name": "Unit42",
            "url": "https://example.com/feed.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["viewer"],
    )
    assert response.status_code == 403


def test_admin_can_manage_feeds_and_analyst_can_view(client: TestClient, auth_headers):
    create_response = client.post(
        "/feeds",
        json={
            "name": "Unit42",
            "url": "https://example.com/feed.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201

    list_response = client.get("/feeds", headers=auth_headers["analyst"])
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_admin_user_management_and_rbac(client: TestClient, auth_headers):
    create_user = client.post(
        "/users",
        json={
            "email": "new.viewer@example.com",
            "password": "ViewerPass987!",
            "role": "viewer",
            "is_active": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_user.status_code == 201

    non_admin_list = client.get("/users", headers=auth_headers["analyst"])
    assert non_admin_list.status_code == 403



def test_api_token_flow(client: TestClient, auth_headers):
    create_feed = client.post(
        "/feeds",
        json={
            "name": "TokenTest",
            "url": "https://example.com/token.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_feed.status_code == 201

    token_response = client.post(
        "/tokens",
        json={"name": "ci-token", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers=auth_headers["admin"],
    )
    assert token_response.status_code == 201
    token_payload = token_response.json()

    access_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_payload['token']}"})
    assert access_response.status_code == 200

    tokens_response = client.get("/tokens", headers=auth_headers["admin"])
    assert tokens_response.status_code == 200
    token_id = tokens_response.json()[0]["id"]

    revoke_response = client.delete(f"/tokens/{token_id}", headers=auth_headers["admin"])
    assert revoke_response.status_code == 204

    denied_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_payload['token']}"})
    assert denied_response.status_code == 401


def test_audit_log_endpoint(client: TestClient, auth_headers):
    create_feed = client.post(
        "/feeds",
        json={
            "name": "AuditTest",
            "url": "https://example.com/audit.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_feed.status_code == 201

    logs_response = client.get("/audit-logs", headers=auth_headers["admin"])
    assert logs_response.status_code == 200
    logs = logs_response.json()["logs"]
    assert any(log["action"] == "feeds.create" for log in logs)


def test_stats_overview_endpoint(client: TestClient, auth_headers):
    create_feed = client.post(
        "/feeds",
        json={
            "name": "StatsFeed",
            "url": "https://example.com/stats.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_feed.status_code == 201

    stats_response = client.get("/stats/overview?days=30", headers=auth_headers["viewer"])
    assert stats_response.status_code == 200
    payload = stats_response.json()
    assert payload["window_days"] == 30
    assert "totals" in payload
    assert "feed_breakdown" in payload
