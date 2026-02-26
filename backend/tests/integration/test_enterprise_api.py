import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.models.item import Item
from app.services.feed_probe import FeedProbeResult


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


def test_feed_create_blocks_private_network_urls(client: TestClient, auth_headers):
    response = client.post(
        "/feeds",
        json={
            "name": "PrivateFeed",
            "url": "http://127.0.0.1/private.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert response.status_code == 422


def test_feed_metadata_endpoint(client: TestClient, auth_headers, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.feeds.probe_feed_metadata",
        lambda _url: FeedProbeResult(
            name="Detected Feed",
            description="Detected description",
            site_url="https://example.com",
            language="en",
            etag="etag-123",
            last_modified="Wed, 26 Feb 2026 00:00:00 GMT",
            resolved_url="https://example.com/feed.xml",
            feed_type="rss20",
        ),
    )

    response = client.post("/feeds/metadata", json={"url": "https://example.com/feed.xml"}, headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Detected Feed"
    assert payload["feed_type"] == "rss20"


def test_feed_create_supports_schedule_mode(client: TestClient, auth_headers):
    response = client.post(
        "/feeds",
        json={
            "name": "Scheduled Feed",
            "url": "https://example.com/scheduled.xml",
            "enabled": True,
            "fetch_mode": "schedule",
            "schedule_cron": "*/30 * * * *",
        },
        headers=auth_headers["admin"],
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["fetch_mode"] == "schedule"
    assert payload["schedule_cron"] == "*/30 * * * *"


def test_feed_import_and_export(client: TestClient, auth_headers):
    import_response = client.post(
        "/feeds/import",
        json={
            "overwrite_existing": False,
            "feeds": [
                {
                    "name": "Bulk One",
                    "url": "https://example.com/bulk-one.xml",
                    "enabled": True,
                    "fetch_mode": "interval",
                    "fetch_interval_seconds": 600,
                },
                {
                    "name": "Bulk Two",
                    "url": "https://example.com/bulk-two.xml",
                    "enabled": True,
                    "fetch_mode": "schedule",
                    "schedule_cron": "0 * * * *",
                },
            ],
        },
        headers=auth_headers["admin"],
    )
    assert import_response.status_code == 200
    import_payload = import_response.json()
    assert import_payload["created"] == 2
    assert import_payload["updated"] == 0

    export_response = client.get("/feeds/export", headers=auth_headers["admin"])
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert len(export_payload["feeds"]) == 2
    assert any(feed["name"] == "Bulk One" for feed in export_payload["feeds"])


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


def test_api_token_scope_is_enforced(client: TestClient, auth_headers):
    token_response = client.post(
        "/tokens",
        json={"name": "scope-limited", "expires_in_days": 30, "scopes": ["read:items"]},
        headers=auth_headers["admin"],
    )
    assert token_response.status_code == 201
    token_payload = token_response.json()

    denied_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_payload['token']}"})
    assert denied_response.status_code == 403
    assert denied_response.json()["detail"] == "Insufficient token scope"


def test_api_token_write_scope_allows_feed_mutation(client: TestClient, auth_headers):
    token_response = client.post(
        "/tokens",
        json={"name": "feed-writer", "expires_in_days": 30, "scopes": ["write:feeds"]},
        headers=auth_headers["admin"],
    )
    assert token_response.status_code == 201
    token_payload = token_response.json()
    token_auth = {"Authorization": f"Bearer {token_payload['token']}"}

    create_response = client.post(
        "/feeds",
        json={
            "name": "ScopedFeed",
            "url": "https://example.com/scoped.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=token_auth,
    )
    assert create_response.status_code == 201

    list_response = client.get("/feeds", headers=token_auth)
    assert list_response.status_code == 200
    assert any(feed["name"] == "ScopedFeed" for feed in list_response.json())


def test_token_rejects_invalid_scope_values(client: TestClient, auth_headers):
    token_response = client.post(
        "/tokens",
        json={"name": "invalid-scope", "expires_in_days": 30, "scopes": ["drop:database"]},
        headers=auth_headers["admin"],
    )
    assert token_response.status_code == 422


def test_token_defaults_scopes_when_not_provided(client: TestClient, auth_headers):
    create_response = client.post(
        "/tokens",
        json={"name": "default-scope-token", "expires_in_days": 30},
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201

    list_response = client.get("/tokens", headers=auth_headers["admin"])
    assert list_response.status_code == 200
    created = next(token for token in list_response.json() if token["name"] == "default-scope-token")
    assert created["scopes"] == ["read:feeds", "read:items", "read:stats"]


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


def test_stats_overview_supports_feed_filters(client: TestClient, auth_headers, db_session):
    feed_one_response = client.post(
        "/feeds",
        json={
            "name": "StatsFilteredOne",
            "url": "https://example.com/stats-filter-one.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    feed_two_response = client.post(
        "/feeds",
        json={
            "name": "StatsFilteredTwo",
            "url": "https://example.com/stats-filter-two.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_one_response.status_code == 201
    assert feed_two_response.status_code == 201

    feed_one_id = feed_one_response.json()["id"]
    feed_two_id = feed_two_response.json()["id"]

    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=uuid.UUID(feed_one_id),
                source_guid="feed-one-item",
                url="https://example.com/one",
                canonical_url="https://example.com/one",
                title="Feed One Item",
                summary="alpha",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:feed-one-item",
                content_hash="a" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=uuid.UUID(feed_two_id),
                source_guid="feed-two-item",
                url="https://example.net/two",
                canonical_url="https://example.net/two",
                title="Feed Two Item",
                summary="beta",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:feed-two-item",
                content_hash="b" * 64,
                status="error",
            ),
        ]
    )
    db_session.commit()

    stats_response = client.get(f"/stats/overview?days=30&feed_ids={feed_one_id}", headers=auth_headers["viewer"])
    assert stats_response.status_code == 200
    payload = stats_response.json()
    assert payload["totals"]["items_total"] == 1
    assert len(payload["feed_breakdown"]) == 1
    assert payload["feed_breakdown"][0]["feed_id"] == feed_one_id


def test_items_support_multi_feed_filters(client: TestClient, auth_headers, db_session):
    feed_one_response = client.post(
        "/feeds",
        json={
            "name": "DashFilteredOne",
            "url": "https://example.com/dash-filter-one.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    feed_two_response = client.post(
        "/feeds",
        json={
            "name": "DashFilteredTwo",
            "url": "https://example.com/dash-filter-two.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_one_response.status_code == 201
    assert feed_two_response.status_code == 201

    feed_one_id = feed_one_response.json()["id"]
    feed_two_id = feed_two_response.json()["id"]

    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=uuid.UUID(feed_one_id),
                source_guid="dash-feed-one-item",
                url="https://dash.example.com/one",
                canonical_url="https://dash.example.com/one",
                title="Dash Feed One Item",
                summary="one",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:dash-feed-one-item",
                content_hash="c" * 64,
                status="new",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=uuid.UUID(feed_two_id),
                source_guid="dash-feed-two-item",
                url="https://dash.example.net/two",
                canonical_url="https://dash.example.net/two",
                title="Dash Feed Two Item",
                summary="two",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:dash-feed-two-item",
                content_hash="d" * 64,
                status="new",
            ),
        ]
    )
    db_session.commit()

    one_response = client.get(f"/items?page=1&page_size=50&feed_ids={feed_one_id}", headers=auth_headers["viewer"])
    assert one_response.status_code == 200
    assert one_response.json()["total"] == 1

    both_response = client.get(
        f"/items?page=1&page_size=50&feed_ids={feed_one_id},{feed_two_id}",
        headers=auth_headers["viewer"],
    )
    assert both_response.status_code == 200
    assert both_response.json()["total"] == 2
