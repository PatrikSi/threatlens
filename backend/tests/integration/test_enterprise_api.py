import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.security import generate_api_token
from app.core.token_scopes import DEFAULT_API_TOKEN_SCOPES
from app.models.api_token import ApiToken
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.saved_view import SavedView
from app.models.tag import ItemTag, Tag, TagFeedbackEvent
from app.models.user import User
from app.core.security import get_password_hash
from app.services import auth_rate_limit
from app.services.feed_storage import feed_url_digest
from app.services.feed_probe import FeedProbeResult
from app.services.auth_rate_limit import LoginThrottleState


@pytest.fixture(autouse=True)
def _stub_feed_task_dispatch(monkeypatch):
    monkeypatch.setattr("app.api.routes.feeds.celery_app.send_task", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _reset_auth_emergency_state():
    for email in ("admin@example.com", "analyst@example.com", "viewer@example.com", "pending@example.com"):
        for ip in ("testclient", "203.0.113.10", "203.0.113.11"):
            auth_rate_limit._emergency_clear_login_failures(email, ip)
            auth_rate_limit._emergency_clear_password_verification_failures(email, ip)
            auth_rate_limit._emergency_clear_self_registration_attempts(email, ip)
    yield
    for email in ("admin@example.com", "analyst@example.com", "viewer@example.com", "pending@example.com"):
        for ip in ("testclient", "203.0.113.10", "203.0.113.11"):
            auth_rate_limit._emergency_clear_login_failures(email, ip)
            auth_rate_limit._emergency_clear_password_verification_failures(email, ip)
            auth_rate_limit._emergency_clear_self_registration_attempts(email, ip)


class _UnavailableRedis:
    def ttl(self, _key: str):
        raise auth_rate_limit.redis.RedisError("redis unavailable")

    def incr(self, _key: str):
        raise auth_rate_limit.redis.RedisError("redis unavailable")

    def expire(self, _key: str, _seconds: int):
        raise auth_rate_limit.redis.RedisError("redis unavailable")

    def set(self, _key: str, _value: str, ex: int, nx: bool):
        raise auth_rate_limit.redis.RedisError("redis unavailable")

    def delete(self, *_keys: str):
        raise auth_rate_limit.redis.RedisError("redis unavailable")


def _session_cookie_value(client: TestClient) -> str:
    session_token = client.cookies.get("threatlens_session")
    assert session_token
    return session_token


def _saved_view_query_payload(*, query: str = "exchange") -> dict:
    return {
        "schema_version": 1,
        "version": 6,
        "rss_filters": {
            "selected_feed_ids": ["feed-1"],
            "selected_tags": ["vendor:microsoft"],
            "q": query,
            "read_status": "unread",
            "star_status": "all",
            "view_mode": "compact",
            "page_size": 25,
            "time_range": "7d",
            "custom_since_date": "",
            "custom_until_date": "",
            "rolling_days": "7",
            "sort": "published_at_desc",
        },
        "alert_filters": {
            "selected_alert_ids": [],
            "selected_categories": [],
            "q": "",
            "view_mode": "expanded",
            "page_size": 25,
            "time_range": "7d",
            "custom_since_date": "",
            "custom_until_date": "",
            "rolling_days": "7",
            "sort": "published_at_desc",
        },
        "windows": [
            {
                "id": "rss-1",
                "type": "rss",
                "title": "RSS Panel 1",
                "snap": "full",
                "rect": {"x": 0, "y": 0, "width": 1120, "height": 680},
                "controls_collapsed": False,
                "scratch_note": "",
                "time_override": None,
                "rss_filters": {
                    "selected_feed_ids": ["feed-1"],
                    "selected_tags": ["vendor:microsoft"],
                    "q": query,
                    "read_status": "unread",
                    "star_status": "all",
                    "view_mode": "compact",
                    "page": 1,
                    "page_size": 25,
                    "sort": "published_at_desc",
                    "show_advanced_filters": True,
                },
                "alert_filters": None,
                "selected_daily_brief_id": None,
            }
        ],
        "ui": {
            "show_advanced_filters": True,
        },
    }


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


def test_saved_view_endpoints_persist_versioned_payloads(client: TestClient, auth_headers, db_session, seed_users):
    _ = seed_users

    create_response = client.post(
        "/views",
        json={
            "name": "Ops layout",
            "query_json": _saved_view_query_payload(query="exchange"),
        },
        headers=auth_headers["viewer"],
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["query_json"]["schema_version"] == 1
    assert created["query_json"]["rss_filters"]["q"] == "exchange"
    assert created["query_json"]["windows"][0]["rss_filters"]["show_advanced_filters"] is True

    stored = db_session.get(SavedView, uuid.UUID(created["id"]))
    assert stored is not None
    assert stored.query_json["schema_version"] == 1

    update_response = client.patch(
        f"/views/{created['id']}",
        json={
            "query_json": _saved_view_query_payload(query="cve-2026"),
        },
        headers=auth_headers["viewer"],
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["query_json"]["rss_filters"]["q"] == "cve-2026"

    invalid_update = client.patch(
        f"/views/{created['id']}",
        json={
            "query_json": {
                "schema_version": 1,
                "version": 6,
                "rss_filters": {},
                "alert_filters": {},
                "windows": [],
                "ui": {"show_advanced_filters": False},
            }
        },
        headers=auth_headers["viewer"],
    )
    assert invalid_update.status_code == 422


def test_saved_view_listing_normalizes_legacy_payloads(client: TestClient, auth_headers, db_session, seed_users):
    viewer = seed_users["viewer"]
    legacy_view = SavedView(
        user_id=viewer.id,
        name="Legacy layout",
        query_json={
            "filters": {
                "selected_feed_ids": ["feed-legacy"],
                "q": "legacy-search",
                "read_status": "read",
            },
            "panel_rect": {
                "x": 10,
                "y": 20,
                "width": 620,
                "height": 420,
            },
        },
    )
    db_session.add(legacy_view)
    db_session.commit()

    response = client.get("/views", headers=auth_headers["viewer"])
    assert response.status_code == 200

    payload = next(entry for entry in response.json() if entry["id"] == str(legacy_view.id))
    assert payload["query_json"]["schema_version"] == 1
    assert payload["query_json"]["rss_filters"]["selected_feed_ids"] == ["feed-legacy"]
    assert payload["query_json"]["rss_filters"]["q"] == "legacy-search"
    assert payload["query_json"]["windows"][0]["type"] == "rss"
    assert payload["query_json"]["windows"][0]["rect"]["width"] == 620


def test_saved_view_listing_drops_non_search_time_overrides_from_legacy_windows(
    client: TestClient, auth_headers, db_session, seed_users
):
    viewer = seed_users["viewer"]
    legacy_view = SavedView(
        user_id=viewer.id,
        name="Legacy mixed layout",
        query_json={
            "windows": [
                {
                    "id": "notes-1",
                    "type": "notes",
                    "title": "Notes Panel 1",
                    "snap": "full",
                    "rect": {
                        "x": 0,
                        "y": 0,
                        "width": 620,
                        "height": 420,
                    },
                    "controls_collapsed": False,
                    "scratch_note": "Track pivots",
                    "time_override": {
                        "time_range": "7d",
                        "custom_since_date": "",
                        "custom_until_date": "",
                        "rolling_days": "7",
                    },
                    "selected_daily_brief_id": "should-clear",
                },
                {
                    "id": "brief-1",
                    "type": "daily_brief",
                    "title": "Daily Brief Panel 1",
                    "snap": "right",
                    "rect": {
                        "x": 620,
                        "y": 0,
                        "width": 620,
                        "height": 420,
                    },
                    "controls_collapsed": False,
                    "scratch_note": "",
                    "time_override": {
                        "time_range": "30d",
                        "custom_since_date": "",
                        "custom_until_date": "",
                        "rolling_days": "30",
                    },
                    "selected_daily_brief_id": "brief-snapshot-1",
                },
            ],
            "ui": {"show_advanced_filters": False},
        },
    )
    db_session.add(legacy_view)
    db_session.commit()

    response = client.get("/views", headers=auth_headers["viewer"])
    assert response.status_code == 200

    payload = next(entry for entry in response.json() if entry["id"] == str(legacy_view.id))
    assert payload["query_json"]["windows"][0]["time_override"] is None
    assert payload["query_json"]["windows"][0]["selected_daily_brief_id"] is None
    assert payload["query_json"]["windows"][1]["type"] == "daily_brief"
    assert payload["query_json"]["windows"][1]["time_override"] is None
    assert payload["query_json"]["windows"][1]["selected_daily_brief_id"] == "brief-snapshot-1"


def test_login_rate_limit_returns_429(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.auth.check_login_throttle",
        lambda _email, _ip: LoginThrottleState(blocked=True, retry_after_seconds=60),
    )

    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "60"


def test_login_succeeds_when_throttle_backend_is_unavailable(client: TestClient, monkeypatch, seed_users):
    _ = seed_users
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())

    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert response.status_code == 200
    assert response.json() == {"token_type": "session_cookie", "csrf_token": response.json()["csrf_token"]}
    assert response.json()["csrf_token"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_login_uses_local_emergency_throttle_when_backend_is_unavailable(
    client: TestClient,
    monkeypatch,
    seed_users,
):
    _ = seed_users
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    first_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "WrongPass123!"},
    )
    assert first_response.status_code == 401

    second_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "WrongPass123!"},
    )
    assert second_response.status_code == 429
    assert second_response.headers.get("retry-after") is not None


def test_successful_login_clears_local_emergency_throttle_when_backend_is_unavailable(
    client: TestClient,
    monkeypatch,
    seed_users,
):
    _ = seed_users
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableRedis())
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 2)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    first_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "WrongPass123!"},
    )
    assert first_response.status_code == 401

    success_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert success_response.status_code == 200

    third_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "WrongPass123!"},
    )
    assert third_response.status_code == 401
    assert third_response.json()["detail"] == "Invalid email or password"


def test_registration_settings_endpoint_reflects_config(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.auth.get_settings",
        lambda: SimpleNamespace(allow_self_registration=True, ai_enabled=True),
    )

    response = client.get("/auth/registration-settings")
    assert response.status_code == 200
    assert response.json() == {"allow_self_registration": True, "ai_enabled": True}


def test_register_creates_pending_user_when_enabled(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.auth.get_settings",
        lambda: SimpleNamespace(allow_self_registration=True),
    )

    response = client.post(
        "/auth/register",
        json={"email": "pending@example.com", "password": "PendingPass123!"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["email"] == "pending@example.com"
    assert payload["role"] == "viewer"
    assert payload["is_active"] is True
    assert payload["is_approved"] is False


def test_register_is_throttled_after_anonymous_attempts(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.auth.get_settings",
        lambda: SimpleNamespace(allow_self_registration=True),
    )
    monkeypatch.setattr("app.api.routes.auth.resolve_client_ip", lambda _request: "203.0.113.10")
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    first_response = client.post(
        "/auth/register",
        json={"email": "pending@example.com", "password": "PendingPass123!"},
    )
    assert first_response.status_code == 200

    second_response = client.post(
        "/auth/register",
        json={"email": "pending-second@example.com", "password": "PendingPass123!"},
    )
    assert second_response.status_code == 429
    assert second_response.headers.get("retry-after") == "120"
    assert second_response.json()["detail"] == "Too many self-registration attempts. Try again later."


def test_login_rejects_pending_user_with_clear_message(client: TestClient, db_session):
    pending_user = User(
        id=uuid.uuid4(),
        email="pending@example.com",
        password_hash=get_password_hash("PendingPass123!"),
        role="viewer",
        is_active=True,
        is_approved=False,
    )
    db_session.add(pending_user)
    db_session.commit()

    response = client.post(
        "/auth/login",
        json={"email": "pending@example.com", "password": "PendingPass123!"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Your account is pending admin approval."


def test_login_with_invalid_password_hash_returns_401(client: TestClient, db_session):
    corrupted_user = User(
        id=uuid.uuid4(),
        email="brokenhash@example.com",
        password_hash="not-a-valid-passlib-hash",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    db_session.add(corrupted_user)
    db_session.commit()

    response = client.post(
        "/auth/login",
        json={"email": "brokenhash@example.com", "password": "SomePassword123!"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_change_password_with_invalid_stored_hash_returns_400(client: TestClient, db_session, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    user = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None
    user.password_hash = "not-a-valid-passlib-hash"
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/auth/change-password",
        json={"current_password": "AdminPass123!", "new_password": "AdminPass456!"},
        headers={"x-csrf-token": csrf_token},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect"


def test_change_password_invalidates_existing_cookie_sessions(client: TestClient, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    change_response = client.post(
        "/auth/change-password",
        json={"current_password": "AdminPass123!", "new_password": "AdminPass456!"},
        headers={"x-csrf-token": csrf_token},
    )
    assert change_response.status_code == 200

    stale_session_response = client.get("/auth/me")
    assert stale_session_response.status_code == 401
    assert stale_session_response.json()["detail"] == "Invalid credentials"

    old_password_login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert old_password_login.status_code == 401

    new_password_login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass456!"},
    )
    assert new_password_login.status_code == 200


def test_change_password_current_password_check_is_throttled(
    client: TestClient, seed_users, monkeypatch: pytest.MonkeyPatch
):
    _ = seed_users
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    first_response = client.post(
        "/auth/change-password",
        json={"current_password": "WrongPass123!", "new_password": "NewAdminPass123!"},
        headers={"x-csrf-token": csrf_token},
    )
    assert first_response.status_code == 400
    assert first_response.json()["detail"] == "Current password is incorrect"

    second_response = client.post(
        "/auth/change-password",
        json={"current_password": "WrongPass123!", "new_password": "NewAdminPass123!"},
        headers={"x-csrf-token": csrf_token},
    )
    assert second_response.status_code == 429
    assert (
        second_response.json()["detail"]
        == "Too many failed current password verification attempts. Try again later."
    )


def test_change_password_revokes_existing_api_tokens(client: TestClient, auth_headers):
    token_response = client.post(
        "/tokens",
        json={"name": "password-reset-token", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers=auth_headers["admin"],
    )
    assert token_response.status_code == 201
    token_value = token_response.json()["token"]

    change_response = client.post(
        "/auth/change-password",
        json={"current_password": "AdminPass123!", "new_password": "AdminPass456!"},
        headers=auth_headers["admin"],
    )
    assert change_response.status_code == 200

    stale_token_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_value}"})
    assert stale_token_response.status_code == 401


def test_admin_password_reset_revokes_existing_api_tokens(client: TestClient, auth_headers, db_session):
    viewer = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert viewer is not None

    token_response = client.post(
        "/tokens",
        json={"name": "viewer-reset-token", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers=auth_headers["viewer"],
    )
    assert token_response.status_code == 201
    token_value = token_response.json()["token"]

    update_response = client.patch(
        f"/users/{viewer.id}",
        json={"password": "ViewerPass456!"},
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200

    db_session.refresh(viewer)
    assert viewer.auth_token_version == 1
    active_tokens = db_session.scalars(select(ApiToken).where(ApiToken.user_id == viewer.id, ApiToken.revoked_at.is_(None))).all()
    assert active_tokens == []

    stale_token_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_value}"})
    assert stale_token_response.status_code == 401

    old_password_login = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert old_password_login.status_code == 401

    new_password_login = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass456!"},
    )
    assert new_password_login.status_code == 200


def test_logout_clears_auth_cookies_without_requiring_a_valid_session(client: TestClient):
    response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    set_cookie = response.headers.get("set-cookie", "")
    assert "threatlens_session=" in set_cookie
    assert "threatlens_csrf=" in set_cookie


def test_logout_with_cookie_session_requires_csrf_header(client: TestClient, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]
    session_token = _session_cookie_value(client)

    denied_logout = client.post("/auth/logout")
    assert denied_logout.status_code == 403

    allowed_logout = client.post("/auth/logout", headers={"x-csrf-token": csrf_token})
    assert allowed_logout.status_code == 200
    assert allowed_logout.json() == {"status": "ok"}

    client.cookies.set("threatlens_session", session_token)
    stale_session_response = client.get("/auth/me")
    assert stale_session_response.status_code == 401


def test_login_session_jwt_in_authorization_header_is_not_a_supported_api_credential(client: TestClient, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    token = _session_cookie_value(client)

    bearer_response = client.get("/feeds", headers={"Authorization": f"Bearer {token}"})
    assert bearer_response.status_code == 401
    assert bearer_response.json()["detail"] == "Bearer auth requires a scoped API token"

    auth_me_bearer_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert auth_me_bearer_response.status_code == 401
    assert auth_me_bearer_response.json()["detail"] == "Bearer auth requires a scoped API token"

    cookie_response = client.get("/feeds")
    assert cookie_response.status_code == 200


def test_browser_session_requires_password_step_up_to_create_api_token(client: TestClient, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    missing_step_up_response = client.post(
        "/tokens",
        json={"name": "browser-session-token", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers={"x-csrf-token": csrf_token},
    )
    assert missing_step_up_response.status_code == 403
    assert (
        missing_step_up_response.json()["detail"]
        == "Browser sessions must confirm the current password before creating API tokens"
    )

    wrong_password_response = client.post(
        "/tokens",
        json={
            "name": "browser-session-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "current_password": "WrongPass123!",
        },
        headers={"x-csrf-token": csrf_token},
    )
    assert wrong_password_response.status_code == 400
    assert wrong_password_response.json()["detail"] == "Current password is incorrect"


def test_browser_session_current_password_step_up_is_throttled(
    client: TestClient, seed_users, monkeypatch: pytest.MonkeyPatch
):
    _ = seed_users
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_max_attempts", 1)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_window_seconds", 60)
    monkeypatch.setattr(auth_rate_limit.settings, "auth_login_lockout_seconds", 120)

    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    first_response = client.post(
        "/tokens",
        json={
            "name": "browser-session-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "current_password": "WrongPass123!",
        },
        headers={"x-csrf-token": csrf_token},
    )
    assert first_response.status_code == 400
    assert first_response.json()["detail"] == "Current password is incorrect"

    second_response = client.post(
        "/tokens",
        json={
            "name": "browser-session-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "current_password": "WrongPass123!",
        },
        headers={"x-csrf-token": csrf_token},
    )
    assert second_response.status_code == 429
    assert (
        second_response.json()["detail"]
        == "Too many failed current password verification attempts. Try again later."
    )


def test_browser_session_can_create_api_token_after_password_step_up(client: TestClient, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    token_response = client.post(
        "/tokens",
        json={
            "name": "browser-session-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "current_password": "AdminPass123!",
        },
        headers={"x-csrf-token": csrf_token},
    )
    assert token_response.status_code == 201
    token_value = token_response.json()["token"]

    access_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_value}"})
    assert access_response.status_code == 200


def test_browser_session_token_creation_rejects_scopes_outside_role_envelope(
    client: TestClient, db_session, seed_users
):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    token_response = client.post(
        "/tokens",
        json={
            "name": "viewer-feed-writer",
            "expires_in_days": 30,
            "scopes": ["write:feeds"],
            "current_password": "ViewerPass123!",
        },
        headers={"x-csrf-token": csrf_token},
    )
    assert token_response.status_code == 403
    assert "Requested token scopes exceed the permissions allowed for your role" in token_response.json()["detail"]
    assert "write:feeds" in token_response.json()["detail"]

    viewer = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert viewer is not None
    viewer_tokens = db_session.scalars(select(ApiToken).where(ApiToken.user_id == viewer.id)).all()
    assert viewer_tokens == []


def test_cookie_session_auth_rejects_inactive_user(client: TestClient, db_session, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert login_response.status_code == 200
    user = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert user is not None
    user.is_active = False
    db_session.add(user)
    db_session.commit()

    response = client.get("/auth/me")
    assert response.status_code == 403
    assert response.json()["detail"] == "Account is inactive"


def test_items_list_rejects_invalid_sort_value(client: TestClient, auth_headers):
    response = client.get("/items?sort=bogus", headers=auth_headers["viewer"])

    assert response.status_code == 422


def test_admin_unapproval_invalidates_existing_jwt_session(client: TestClient, auth_headers, db_session):
    viewer = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert viewer is not None

    update_response = client.patch(
        f"/users/{viewer.id}",
        json={"is_approved": False},
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200

    db_session.refresh(viewer)
    assert viewer.auth_token_version == 1

    stale_session_response = client.get("/auth/me", headers=auth_headers["viewer"])
    assert stale_session_response.status_code == 401
    assert stale_session_response.json()["detail"] == "Invalid credentials"

    login_response = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert login_response.status_code == 403
    assert login_response.json()["detail"] == "Your account is pending admin approval."


def test_login_ignores_untrusted_x_forwarded_for(client: TestClient, monkeypatch, seed_users):
    _ = seed_users
    captured: dict[str, str] = {}

    def _check(_email: str, ip: str):
        captured["ip"] = ip
        return LoginThrottleState(blocked=False)

    monkeypatch.setattr("app.api.routes.auth.check_login_throttle", _check)

    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
        headers={"x-forwarded-for": "203.0.113.44"},
    )
    assert response.status_code == 200
    assert captured["ip"] != "203.0.113.44"


def test_cookie_session_requires_csrf_for_mutation(client: TestClient, seed_users):
    _ = seed_users
    login_response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200

    denied_create = client.post(
        "/feeds",
        json={
            "name": "Cookie Feed",
            "url": "https://example.com/cookie-feed.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
    )
    assert denied_create.status_code == 403

    allowed_create = client.post(
        "/feeds",
        json={
            "name": "Cookie Feed",
            "url": "https://example.com/cookie-feed.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers={"x-csrf-token": csrf_token},
    )
    assert allowed_create.status_code == 201


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


def test_refresh_feed_queues_force_fetch(client: TestClient, auth_headers, db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Refreshable Feed",
        url="https://example.com/refresh.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()

    captured: dict[str, object] = {}

    def _send_task(name: str, args=None, kwargs=None):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.api.routes.feeds.celery_app.send_task", _send_task)

    response = client.post(
        f"/feeds/{feed.id}/refresh",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
    assert captured == {
        "name": "app.tasks.feed_tasks.fetch_feed",
        "args": [str(feed.id)],
        "kwargs": {"force": True},
    }


def test_refresh_feed_returns_503_when_broker_publish_fails(client: TestClient, auth_headers, db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Refreshable Feed",
        url="https://example.com/refresh.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add(feed)
    db_session.commit()

    monkeypatch.setattr(
        "app.api.routes.feeds.celery_app.send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    response = client.post(
        f"/feeds/{feed.id}/refresh",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Task queue is temporarily unavailable. Try again later."


def test_retry_article_fetch_queues_manual_repair(client: TestClient, auth_headers, db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Retry Feed",
        url="https://example.com/retry.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="retry-article-item",
        url="https://example.com/articles/retry-article-item",
        canonical_url="https://example.com/articles/retry-article-item",
        title="Retry article",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="retry-article-item",
        content_hash="9" * 64,
        status="error",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    captured: list[str] = []

    class _FakeTask:
        id = "article-retry-123"

    def _fake_delay(item_id: str):
        captured.append(item_id)
        return _FakeTask()

    monkeypatch.setattr("app.api.routes.items.fetch_article.delay", _fake_delay)

    response = client.post(
        f"/items/{item.id}/retry-article-fetch",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
    assert captured == [str(item.id)]


def test_retry_article_fetch_returns_503_when_broker_publish_fails(client: TestClient, auth_headers, db_session, monkeypatch):
    feed = Feed(
        id=uuid.uuid4(),
        name="Retry Feed",
        url="https://example.com/retry.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="retry-article-fail-item",
        url="https://example.com/articles/retry-article-fail-item",
        canonical_url="https://example.com/articles/retry-article-fail-item",
        title="Retry article",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="retry-article-fail-item",
        content_hash="a" * 64,
        status="error",
    )
    db_session.add_all([feed, item])
    db_session.commit()

    monkeypatch.setattr(
        "app.api.routes.items.fetch_article.delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    response = client.post(
        f"/items/{item.id}/retry-article-fetch",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Task queue is temporarily unavailable. Try again later."


def test_admin_can_list_users_for_user_directory(client: TestClient, auth_headers):
    response = client.get("/users", headers=auth_headers["admin"])
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) >= 1
    assert "email" in payload[0]
    assert "is_approved" in payload[0]


def test_users_list_tolerates_legacy_invalid_email_values(client: TestClient, auth_headers, db_session):
    legacy_user = User(
        id=uuid.uuid4(),
        email="admin",
        password_hash=get_password_hash("LegacyPass123!"),
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    db_session.add(legacy_user)
    db_session.commit()

    response = client.get("/users", headers=auth_headers["admin"])
    assert response.status_code == 200
    emails = [user["email"] for user in response.json()]
    assert "admin" in emails


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

    response = client.post("/feeds/metadata", json={"url": "https://example.com/feed.xml"}, headers=auth_headers["analyst"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Detected Feed"
    assert payload["feed_type"] == "rss20"


def test_feed_metadata_endpoint_requires_operator_role(client: TestClient, auth_headers):
    response = client.post("/feeds/metadata", json={"url": "https://example.com/feed.xml"}, headers=auth_headers["viewer"])
    assert response.status_code == 403


def test_health_live_endpoint(client: TestClient):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_ready_endpoint_requires_worker_health(client: TestClient, monkeypatch, auth_headers):
    fresh_heartbeat = datetime.now(timezone.utc).isoformat()

    class _RedisClient:
        def ping(self):
            return True

        def get(self, key):
            _ = key
            return fresh_heartbeat

    class _Inspector:
        def ping(self):
            return {}

    monkeypatch.setattr("app.api.routes.health.redis.Redis.from_url", lambda *_args, **_kwargs: _RedisClient())
    monkeypatch.setattr("app.api.routes.health.celery_app.control.inspect", lambda timeout: _Inspector())

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ok": False}

    admin_response = client.get("/health/ready", headers=auth_headers["admin"])
    assert admin_response.status_code == 503
    assert admin_response.json() == {
        "ok": False,
        "db": True,
        "redis": True,
        "worker": False,
        "beat": True,
    }


def test_health_ready_endpoint_requires_beat_health(client: TestClient, monkeypatch, auth_headers):
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    class _RedisClient:
        def ping(self):
            return True

        def get(self, key):
            _ = key
            return stale_heartbeat

    class _Inspector:
        def ping(self):
            return {"celery@worker-1": {"ok": "pong"}}

    monkeypatch.setattr("app.api.routes.health.redis.Redis.from_url", lambda *_args, **_kwargs: _RedisClient())
    monkeypatch.setattr("app.api.routes.health.celery_app.control.inspect", lambda timeout: _Inspector())

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ok": False}

    admin_response = client.get("/health/ready", headers=auth_headers["admin"])
    assert admin_response.status_code == 503
    assert admin_response.json() == {
        "ok": False,
        "db": True,
        "redis": True,
        "worker": True,
        "beat": False,
    }


def test_health_worker_endpoint_reports_ok(client: TestClient, auth_headers, monkeypatch):
    class _Inspector:
        def ping(self):
            return {"celery@worker-1": {"ok": "pong"}}

    monkeypatch.setattr(
        "app.api.routes.health.celery_app.control.inspect",
        lambda timeout: _Inspector(),
    )

    response = client.get("/health/worker", headers=auth_headers["admin"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["workers"]["celery@worker-1"] == "pong"


def test_health_worker_endpoint_hides_worker_details_from_public(client: TestClient, monkeypatch):
    class _Inspector:
        def ping(self):
            return {"celery@worker-1": {"ok": "pong"}}

    monkeypatch.setattr(
        "app.api.routes.health.celery_app.control.inspect",
        lambda timeout: _Inspector(),
    )

    response = client.get("/health/worker")

    assert response.status_code == 401


def test_health_beat_endpoint_reports_stale_when_heartbeat_old(client: TestClient, auth_headers, monkeypatch):
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    class _RedisClient:
        def get(self, key):
            _ = key
            return stale_heartbeat

    monkeypatch.setattr("app.api.routes.health.redis.Redis.from_url", lambda *_args, **_kwargs: _RedisClient())

    response = client.get("/health/beat", headers=auth_headers["admin"])
    assert response.status_code == 503
    payload = response.json()
    assert payload["ok"] is False


def test_health_beat_endpoint_hides_internal_details_from_public(client: TestClient, monkeypatch):
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    class _RedisClient:
        def get(self, key):
            _ = key
            return stale_heartbeat

    monkeypatch.setattr("app.api.routes.health.redis.Redis.from_url", lambda *_args, **_kwargs: _RedisClient())

    response = client.get("/health/beat")

    assert response.status_code == 401


def test_health_notifications_endpoint_reports_stale_queue(client: TestClient, auth_headers, db_session, seed_users):
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=viewer.id,
        name="Health webhook",
        url_template="https://hooks.example.com/health",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all(
        [
            webhook,
            NotificationWebhookDelivery(
                id=uuid.uuid4(),
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="pending",
                attempt_count=0,
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/health",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                response_body_preview=None,
                error=None,
                attempted_at=datetime.now(timezone.utc) - timedelta(minutes=6),
            ),
            NotificationWebhookDelivery(
                id=uuid.uuid4(),
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="sending",
                attempt_count=1,
                claimed_at=datetime.now(timezone.utc) - timedelta(minutes=3),
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/health",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                response_body_preview=None,
                error=None,
                attempted_at=datetime.now(timezone.utc) - timedelta(minutes=3),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/health/notifications", headers=auth_headers["admin"])

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "critical"
    assert payload["pending_deliveries"] == 1
    assert payload["sending_deliveries"] == 1
    assert payload["stale_sending_deliveries"] == 1


def test_health_notifications_endpoint_hides_queue_counts_from_public(client: TestClient, db_session, seed_users):
    viewer = seed_users["viewer"]
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=viewer.id,
        name="Health webhook",
        url_template="https://hooks.example.com/health",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all(
        [
            webhook,
            NotificationWebhookDelivery(
                id=uuid.uuid4(),
                webhook_id=webhook.id,
                user_id=viewer.id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="pending",
                attempt_count=0,
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/health",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                response_body_preview=None,
                error=None,
                attempted_at=datetime.now(timezone.utc) - timedelta(minutes=6),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/health/notifications")

    assert response.status_code == 401


def test_feed_list_does_not_backfill_metadata(client: TestClient, auth_headers, monkeypatch):
    create_response = client.post(
        "/feeds",
        json={
            "name": "Legacy Feed",
            "url": "https://example.com/legacy.xml",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201

    probe_called = False

    def _probe(_url):
        nonlocal probe_called
        probe_called = True
        return FeedProbeResult(
            name="Detected Legacy",
            description="Backfilled description",
            site_url="https://example.com",
            language="en",
            etag="etag-legacy",
            last_modified="Wed, 26 Feb 2026 00:00:00 GMT",
            resolved_url="https://example.com/legacy.xml",
            feed_type="rss20",
        )

    monkeypatch.setattr(
        "app.api.routes.feeds.probe_feed_metadata",
        _probe,
    )

    list_response = client.get("/feeds", headers=auth_headers["viewer"])
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["name"] == "Legacy Feed"
    assert payload[0]["description"] is None
    assert payload[0]["site_url"] is None
    assert payload[0]["language"] is None
    assert payload[0]["etag"] is None
    assert probe_called is False


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


def test_feed_import_still_succeeds_when_backfill_enqueue_fails(client: TestClient, auth_headers, db_session, monkeypatch):
    def _raise_send_task(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("app.api.routes.feeds.celery_app.send_task", _raise_send_task)

    import_response = client.post(
        "/feeds/import",
        json={
            "overwrite_existing": False,
            "feeds": [
                {
                    "name": "Queued Later",
                    "url": "https://example.com/queued-later.xml",
                    "enabled": True,
                    "fetch_mode": "interval",
                    "fetch_interval_seconds": 600,
                }
            ],
        },
        headers=auth_headers["admin"],
    )
    assert import_response.status_code == 200
    payload = import_response.json()
    assert payload["created"] == 1
    assert any("scheduler will retry later" in error for error in payload["errors"])

    feed = db_session.scalar(select(Feed).where(Feed.url_digest == feed_url_digest("https://example.com/queued-later.xml")))
    assert feed is not None


def test_feed_import_redacts_secret_urls_in_fallback_names_and_exports(client: TestClient, auth_headers, db_session):
    import_response = client.post(
        "/feeds/import",
        json={
            "overwrite_existing": False,
            "feeds": [
                {
                    "url": "https://alice:secret@example.com/imported.xml?token=alpha&source=partner",
                    "enabled": True,
                    "fetch_mode": "interval",
                    "fetch_interval_seconds": 1800,
                }
            ],
        },
        headers=auth_headers["admin"],
    )
    assert import_response.status_code == 200
    assert import_response.json()["created"] == 1

    feed = db_session.scalar(
        select(Feed).where(
            Feed.url_digest
            == feed_url_digest("https://alice:secret@example.com/imported.xml?token=alpha&source=partner")
        )
    )
    assert feed is not None
    assert feed.name == "https://example.com/imported.xml?token=REDACTED&source=partner"

    list_response = client.get("/feeds", headers=auth_headers["viewer"])
    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "https://example.com/imported.xml?token=REDACTED&source=partner"
    assert list_response.json()[0]["url"] == "https://example.com/imported.xml?token=REDACTED&source=partner"

    export_response = client.get("/feeds/export", headers=auth_headers["admin"])
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload["feeds"][0]["name"] == "https://example.com/imported.xml?token=REDACTED&source=partner"
    assert export_payload["feeds"][0]["url"] == "https://example.com/imported.xml?token=REDACTED&source=partner"


def test_feed_create_normalizes_default_ports_and_rejects_equivalent_duplicates(client: TestClient, auth_headers):
    first_response = client.post(
        "/feeds",
        json={
            "name": "Normalized Feed",
            "url": "https://example.com:443/path/feed.xml",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert first_response.status_code == 201
    assert first_response.json()["url"] == "https://example.com/path/feed.xml"

    second_response = client.post(
        "/feeds",
        json={
            "name": "Duplicate Feed",
            "url": "https://example.com/path/feed.xml/",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert second_response.status_code == 400
    assert second_response.json()["detail"] == "Feed URL already exists"


def test_feed_create_still_succeeds_when_backfill_enqueue_fails(client: TestClient, auth_headers, db_session, monkeypatch):
    def _raise_send_task(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("app.api.routes.feeds.celery_app.send_task", _raise_send_task)

    response = client.post(
        "/feeds",
        json={
            "name": "Backfill Later",
            "url": "https://example.com/backfill-later.xml",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Backfill Later"

    feed = db_session.scalar(select(Feed).where(Feed.url_digest == feed_url_digest("https://example.com/backfill-later.xml")))
    assert feed is not None


def test_feed_create_keeps_query_distinct_for_authenticated_feeds(client: TestClient, auth_headers, db_session):
    first_response = client.post(
        "/feeds",
        json={
            "name": "Token Feed A",
            "url": "https://example.com/path/feed.xml?token=alpha",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert first_response.status_code == 201
    assert first_response.json()["url"] == "https://example.com/path/feed.xml?token=REDACTED"

    second_response = client.post(
        "/feeds",
        json={
            "name": "Token Feed B",
            "url": "https://example.com/path/feed.xml?token=beta",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert second_response.status_code == 201
    assert second_response.json()["url"] == "https://example.com/path/feed.xml?token=REDACTED"

    first_feed = db_session.scalar(select(Feed).where(Feed.name == "Token Feed A"))
    second_feed = db_session.scalar(select(Feed).where(Feed.name == "Token Feed B"))
    assert first_feed is not None
    assert second_feed is not None
    assert first_feed.url == "https://example.com/path/feed.xml?token=alpha"
    assert second_feed.url == "https://example.com/path/feed.xml?token=beta"


def test_feed_create_keeps_userinfo_distinct_for_authenticated_feeds(client: TestClient, auth_headers, db_session):
    first_response = client.post(
        "/feeds",
        json={
            "name": "Credential Feed A",
            "url": "https://alice:secret@example.com/path/feed.xml",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert first_response.status_code == 201
    assert first_response.json()["url"] == "https://example.com/path/feed.xml"

    second_response = client.post(
        "/feeds",
        json={
            "name": "Credential Feed B",
            "url": "https://bob:secret@example.com/path/feed.xml",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert second_response.status_code == 201
    assert second_response.json()["url"] == "https://example.com/path/feed.xml"

    first_feed = db_session.scalar(select(Feed).where(Feed.name == "Credential Feed A"))
    second_feed = db_session.scalar(select(Feed).where(Feed.name == "Credential Feed B"))
    assert first_feed is not None
    assert second_feed is not None
    assert first_feed.url == "https://alice:secret@example.com/path/feed.xml"
    assert second_feed.url == "https://bob:secret@example.com/path/feed.xml"


def test_feed_create_redacts_secret_urls_in_read_surfaces_and_audit_logs(client: TestClient, auth_headers, db_session):
    create_response = client.post(
        "/feeds",
        json={
            "url": "https://alice:secret@example.com/path/feed.xml?token=alpha&source=partner",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201
    create_payload = create_response.json()
    assert create_payload["name"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"
    assert create_payload["url"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"

    feed = db_session.scalar(select(Feed).where(Feed.id == create_payload["id"]))
    assert feed is not None
    assert feed.url == "https://alice:secret@example.com/path/feed.xml?token=alpha&source=partner"
    stored_url = db_session.execute(
        text("select url from feeds where id = :feed_id"),
        {"feed_id": str(feed.id)},
    ).scalar_one()
    assert stored_url != "https://alice:secret@example.com/path/feed.xml?token=alpha&source=partner"
    assert stored_url.startswith("enc:v1:")

    list_response = client.get("/feeds", headers=auth_headers["viewer"])
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload[0]["name"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"
    assert list_payload[0]["url"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"

    export_response = client.get("/feeds/export", headers=auth_headers["admin"])
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload["feeds"][0]["name"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"
    assert export_payload["feeds"][0]["url"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"

    audit_response = client.get("/audit-logs?action=feeds.create&page_size=10", headers=auth_headers["admin"])
    assert audit_response.status_code == 200
    audit_payload = audit_response.json()
    matching_log = next(log for log in audit_payload["logs"] if log["resource_id"] == create_payload["id"])
    assert matching_log["metadata_json"]["name"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"
    assert matching_log["metadata_json"]["url"] == "https://example.com/path/feed.xml?token=REDACTED&source=partner"


def test_feed_update_redacts_secret_urls_in_response_and_preserves_storage(client: TestClient, auth_headers, db_session):
    create_response = client.post(
        "/feeds",
        json={
            "url": "https://alice:secret@example.com/path/update.xml?token=alpha",
            "enabled": True,
            "fetch_mode": "interval",
            "fetch_interval_seconds": 1800,
        },
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201
    feed_id = create_response.json()["id"]

    update_response = client.patch(
        f"/feeds/{feed_id}",
        json={"description": "Updated description"},
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["name"] == "https://example.com/path/update.xml?token=REDACTED"
    assert update_payload["url"] == "https://example.com/path/update.xml?token=REDACTED"

    feed = db_session.scalar(select(Feed).where(Feed.id == feed_id))
    assert feed is not None
    assert feed.url == "https://alice:secret@example.com/path/update.xml?token=alpha"


def test_feed_import_overwrite_preserves_existing_metadata_when_fields_are_omitted(client: TestClient, auth_headers, db_session):
    feed = Feed(
        id=uuid.uuid4(),
        name="Curated Feed",
        url="https://example.com/feed.xml",
        description="Curated description",
        site_url="https://example.com",
        language="en",
        enabled=False,
        fetch_mode="schedule",
        fetch_interval_seconds=7200,
        schedule_cron="0 * * * *",
    )
    db_session.add(feed)
    db_session.commit()

    response = client.post(
        "/feeds/import",
        json={
            "overwrite_existing": True,
            "feeds": [
                {
                    "url": "https://example.com:443/feed.xml/",
                }
            ],
        },
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 1

    db_session.refresh(feed)
    assert feed.url == "https://example.com/feed.xml"
    assert feed.name == "Curated Feed"
    assert feed.description == "Curated description"
    assert feed.site_url == "https://example.com"
    assert feed.language == "en"
    assert feed.enabled is False
    assert feed.fetch_mode == "schedule"
    assert feed.fetch_interval_seconds == 7200
    assert feed.schedule_cron == "0 * * * *"


def test_items_include_classification_fields(client: TestClient, auth_headers, db_session):
    feed = Feed(name="Classified Feed", url="https://example.com/classified.xml", enabled=True, fetch_interval_seconds=1800)
    db_session.add(feed)
    db_session.flush()

    item = Item(
        feed_id=feed.id,
        source_guid="guid-1",
        url="https://example.com/post",
        title="Classified Item",
        summary="Summary text",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe-guid-1",
        content_hash="a" * 64,
        status="content_fetched",
        last_error=None,
    )
    db_session.add(item)
    db_session.flush()

    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=["supply_chain"],
        confidence=0.87,
        scores_json={"vulnerability": 7.0, "supply_chain": 3.0},
        matched_terms_json={"vulnerability": ["cve"]},
        source_hash="b" * 64,
    )
    db_session.add(classification)
    db_session.commit()

    list_response = client.get("/items", headers=auth_headers["admin"])
    assert list_response.status_code == 200
    listed = list_response.json()["items"][0]
    assert listed["classification"] == "vulnerability"

    detail_response = client.get(f"/items/{item.id}", headers=auth_headers["admin"])
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["classification"]["primary_category"] == "vulnerability"
    assert detail["classification"]["confidence"] == 0.87


def test_item_search_escapes_sql_wildcards(client: TestClient, auth_headers, db_session):
    feed = Feed(name="Wildcard Feed", url="https://example.com/wildcards.xml", enabled=True, fetch_interval_seconds=1800)
    db_session.add(feed)
    db_session.flush()

    matching_item = Item(
        feed_id=feed.id,
        source_guid="wild-1",
        url="https://example.com/articles/percent",
        title="Coverage reached 100%",
        summary="Contains a literal percent sign.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="wild-1",
        content_hash="d" * 64,
        status="new",
    )
    non_matching_item = Item(
        feed_id=feed.id,
        source_guid="wild-2",
        url="https://example.com/articles/plain",
        title="Coverage reached 100 percent",
        summary="No wildcard characters here.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="wild-2",
        content_hash="e" * 64,
        status="new",
    )
    db_session.add_all([matching_item, non_matching_item])
    db_session.commit()

    response = client.get("/items?page=1&page_size=50&q=%25", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    titles = [entry["title"] for entry in payload["items"]]
    assert titles == ["Coverage reached 100%"]


def test_item_graph_endpoint_returns_related_nodes(client: TestClient, auth_headers, db_session):
    feed = Feed(name="Graph Feed", url="https://example.com/graph.xml", enabled=True, fetch_interval_seconds=1800)
    db_session.add(feed)
    db_session.flush()

    root_item = Item(
        feed_id=feed.id,
        source_guid="graph-root",
        url="https://example.com/root",
        title="CVE-2026-9999 vulnerability report",
        summary="Patch Tuesday update",
        published_at=datetime.now(timezone.utc),
        dedupe_key="graph-root",
        content_hash="c" * 64,
        status="content_fetched",
        last_error=None,
    )
    related_item = Item(
        feed_id=feed.id,
        source_guid="graph-related",
        url="https://example.com/related",
        title="Patch Tuesday highlights CVE-2026-8888",
        summary="vulnerability and updates",
        published_at=datetime.now(timezone.utc),
        dedupe_key="graph-related",
        content_hash="d" * 64,
        status="content_fetched",
        last_error=None,
    )
    db_session.add_all([root_item, related_item])
    db_session.flush()

    cve_ioc = IOC(type="cve", value_raw="CVE-2026-9999", value_norm="CVE-2026-9999")
    ip_ioc = IOC(type="ipv4", value_raw="203.0.113.77", value_norm="203.0.113.77")
    db_session.add_all([cve_ioc, ip_ioc])
    db_session.flush()

    db_session.add_all(
        [
            ItemIOC(item_id=root_item.id, ioc_id=cve_ioc.id, source_section="title", occurrences=1, confidence=1.0),
            ItemIOC(item_id=root_item.id, ioc_id=ip_ioc.id, source_section="article", occurrences=2, confidence=1.0),
            ItemIOC(item_id=related_item.id, ioc_id=cve_ioc.id, source_section="title", occurrences=1, confidence=1.0),
        ]
    )
    db_session.commit()

    response = client.get(f"/items/{root_item.id}/graph", headers=auth_headers["admin"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_node_id"] == f"item:{root_item.id}"
    assert any(node["type"] == "item" and node["metadata"]["item_id"] == str(root_item.id) for node in payload["nodes"])
    assert any(node["type"] == "cve" for node in payload["nodes"])
    assert any(edge["relation"] == "mentions" for edge in payload["edges"])
    assert any(edge["relation"] == "observed_in" for edge in payload["edges"])

    pivot_response = client.get(
        f"/items/{root_item.id}/graph?focus_node_id=ioc:{cve_ioc.id}",
        headers=auth_headers["admin"],
    )
    assert pivot_response.status_code == 200
    pivot_payload = pivot_response.json()
    assert pivot_payload["focus_node_id"] == f"ioc:{cve_ioc.id}"
    pivot_item_ids = {node["metadata"].get("item_id") for node in pivot_payload["nodes"] if node["type"] == "item"}
    assert str(root_item.id) in pivot_item_ids
    assert str(related_item.id) in pivot_item_ids


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


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "viewer"},
        {"is_active": False},
        {"is_approved": False},
    ],
)
def test_last_active_approved_admin_cannot_remove_own_admin_access(
    client: TestClient,
    auth_headers,
    db_session,
    payload,
):
    admin = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert admin is not None

    response = client.patch(
        f"/users/{admin.id}",
        json=payload,
        headers=auth_headers["admin"],
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "At least one active approved admin user is required"


def test_admin_can_remove_own_admin_role_when_another_active_approved_admin_exists(client: TestClient, auth_headers, db_session):
    create_response = client.post(
        "/users",
        json={
            "email": "second.admin@example.com",
            "password": "AdminPass987!",
            "role": "admin",
            "is_active": True,
            "is_approved": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 201

    admin = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert admin is not None

    update_response = client.patch(
        f"/users/{admin.id}",
        json={"role": "viewer"},
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200
    assert update_response.json()["role"] == "viewer"

    db_session.refresh(admin)
    assert admin.auth_token_version == 1

    stale_token_response = client.get("/feeds", headers=auth_headers["admin"])
    assert stale_token_response.status_code == 401
    assert stale_token_response.json()["detail"] == "Invalid credentials"


def test_role_promotion_invalidates_existing_browser_session_and_api_token(client: TestClient, db_session, seed_users, auth_headers):
    viewer = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert viewer is not None

    login_response = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert login_response.status_code == 200

    viewer_token = _issue_api_token(
        db_session,
        viewer,
        name="viewer-reader",
        scopes=["read:feeds"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    update_response = client.patch(
        f"/users/{viewer.id}",
        json={"role": "analyst"},
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200
    assert update_response.json()["role"] == "analyst"

    db_session.refresh(viewer)
    assert viewer.auth_token_version == 1

    stale_session_response = client.get("/auth/me")
    assert stale_session_response.status_code == 401
    assert stale_session_response.json()["detail"] == "Invalid credentials"

    stale_token_response = client.get("/feeds", headers={"Authorization": f"Bearer {viewer_token}"})
    assert stale_token_response.status_code == 401
    assert stale_token_response.json()["detail"] == "Invalid credentials"

    relogin_response = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert relogin_response.status_code == 200
    assert relogin_response.json()["csrf_token"]



def test_admin_password_reset_invalidates_existing_jwt_session(client: TestClient, auth_headers, db_session):
    viewer = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert viewer is not None

    reset_response = client.patch(
        f"/users/{viewer.id}",
        json={"password": "ViewerPass456!"},
        headers=auth_headers["admin"],
    )
    assert reset_response.status_code == 200

    db_session.refresh(viewer)
    assert viewer.auth_token_version == 1

    stale_session_response = client.get("/auth/me", headers=auth_headers["viewer"])
    assert stale_session_response.status_code == 401
    assert stale_session_response.json()["detail"] == "Invalid credentials"

    old_password_login = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass123!"},
    )
    assert old_password_login.status_code == 401

    new_password_login = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPass456!"},
    )
    assert new_password_login.status_code == 200


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
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.headers["pragma"] == "no-cache"
    token_payload = token_response.json()

    access_response = client.get("/feeds", headers={"Authorization": f"Bearer {token_payload['token']}"})
    assert access_response.status_code == 200

    tokens_response = client.get("/tokens", headers=auth_headers["admin"])
    assert tokens_response.status_code == 200
    token_id = next(token["id"] for token in tokens_response.json() if token["name"] == "ci-token")

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


def _issue_api_token(db_session, user: User, *, name: str, scopes: list[str], expires_at: datetime) -> str:
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=name,
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=expires_at,
        )
    )
    db_session.flush()
    db_session.commit()
    return token_value


def test_api_token_cannot_delegate_broader_scopes_than_parent_token(client: TestClient, db_session, seed_users):
    admin = seed_users["admin"]
    parent_token = _issue_api_token(
        db_session,
        admin,
        name="token-delegator",
        scopes=["write:tokens"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    escalated_response = client.post(
        "/tokens",
        json={"name": "escalated-admin", "expires_in_days": 30, "scopes": ["admin:*"]},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert escalated_response.status_code == 403
    assert "subset of their own scopes" in escalated_response.json()["detail"]


def test_api_token_can_delegate_subset_of_parent_scopes(client: TestClient, db_session, seed_users):
    admin = seed_users["admin"]
    parent_token = _issue_api_token(
        db_session,
        admin,
        name="token-delegator",
        scopes=["write:tokens", "read:feeds"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    child_response = client.post(
        "/tokens",
        json={"name": "feed-reader-child", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert child_response.status_code == 201

    child_token = child_response.json()["token"]
    access_response = client.get("/feeds", headers={"Authorization": f"Bearer {child_token}"})
    assert access_response.status_code == 200


def test_api_token_auth_rejects_unapproved_user(client: TestClient, auth_headers, db_session):
    viewer = db_session.scalar(select(User).where(User.email == "viewer@example.com"))
    assert viewer is not None

    token_response = client.post(
        "/tokens",
        json={"name": "viewer-token", "expires_in_days": 30, "scopes": ["read:feeds"]},
        headers=auth_headers["viewer"],
    )
    assert token_response.status_code == 201
    token_payload = token_response.json()

    allowed_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token_payload['token']}"})
    assert allowed_response.status_code == 200

    update_response = client.patch(
        f"/users/{viewer.id}",
        json={"is_approved": False},
        headers=auth_headers["admin"],
    )
    assert update_response.status_code == 200

    denied_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token_payload['token']}"})
    assert denied_response.status_code == 401
    assert denied_response.json()["detail"] == "Invalid credentials"


def test_api_token_creation_only_uses_default_scopes_when_scope_field_is_omitted(client: TestClient, auth_headers):
    default_response = client.post(
        "/tokens",
        json={"name": "default-scope-token", "expires_in_days": 30},
        headers=auth_headers["admin"],
    )
    assert default_response.status_code == 201

    empty_scope_response = client.post(
        "/tokens",
        json={"name": "empty-scope-token", "expires_in_days": 30, "scopes": []},
        headers=auth_headers["admin"],
    )
    assert empty_scope_response.status_code == 422

    tokens_response = client.get("/tokens", headers=auth_headers["admin"])
    assert tokens_response.status_code == 200
    tokens_by_name = {entry["name"]: entry for entry in tokens_response.json()}

    assert tokens_by_name["default-scope-token"]["scopes"] == list(DEFAULT_API_TOKEN_SCOPES)
    assert "empty-scope-token" not in tokens_by_name


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
    assert created["scopes"] == ["read:feeds", "read:items", "read:stats", "read:alerts"]


def test_token_rejects_explicit_empty_scope_list(client: TestClient, auth_headers):
    create_response = client.post(
        "/tokens",
        json={"name": "no-scope-token", "expires_in_days": 30, "scopes": []},
        headers=auth_headers["admin"],
    )
    assert create_response.status_code == 422
    assert "omit the field" in create_response.text


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


def test_audit_log_export_endpoint(client: TestClient, auth_headers):
    create_feed = client.post(
        "/feeds",
        json={
            "name": "AuditExport",
            "url": "https://example.com/audit-export.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_feed.status_code == 201

    create_feed_two = client.post(
        "/feeds",
        json={
            "name": "AuditExportTwo",
            "url": "https://example.com/audit-export-two.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_feed_two.status_code == 201

    export_response = client.get("/audit-logs/export?action=feeds.create&limit=1", headers=auth_headers["admin"])
    assert export_response.status_code == 200
    payload = export_response.json()
    assert "exported_at" in payload
    assert payload["total"] >= 1
    assert payload["truncated"] is True
    assert len(payload["logs"]) == 1
    assert payload["logs"][0]["action"] == "feeds.create"


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


def test_stats_overview_daily_volume_and_feed_share_use_publication_time(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Stats Publication Feed",
            "url": "https://example.com/stats-publication.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="stats-old-published",
                url="https://example.com/stats/old-published",
                canonical_url="https://example.com/stats/old-published",
                title="Old publication date",
                summary="outside selected window",
                published_at=now - timedelta(days=45),
                first_seen_at=now - timedelta(hours=2),
                dedupe_key="test:stats-old-published",
                content_hash="1" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="stats-new-published",
                url="https://example.com/stats/new-published",
                canonical_url="https://example.com/stats/new-published",
                title="Recent publication date",
                summary="inside selected window",
                published_at=now - timedelta(days=2),
                first_seen_at=now - timedelta(hours=1),
                dedupe_key="test:stats-new-published",
                content_hash="2" * 64,
                status="content_fetched",
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/stats/overview?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert sum(point["count"] for point in payload["daily_volume"]) == 1
    assert payload["feed_breakdown"][0]["items_in_window"] == 1


def test_stats_overview_daily_volume_uses_first_seen_when_published_missing(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Stats Missing Published Feed",
            "url": "https://example.com/stats-missing-published.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc)
    db_session.add(
        Item(
            id=uuid.uuid4(),
            feed_id=feed_id,
            source_guid="stats-missing-published",
            url="https://example.com/stats/missing-published",
            canonical_url="https://example.com/stats/missing-published",
            title="Missing publication timestamp",
            summary="should count via first_seen_at fallback",
            published_at=None,
            first_seen_at=now - timedelta(days=1),
            dedupe_key="test:stats-missing-published",
            content_hash="3" * 64,
            status="content_fetched",
        )
    )
    db_session.commit()

    response = client.get(f"/stats/overview?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert sum(point["count"] for point in payload["daily_volume"]) == 1
    assert payload["feed_breakdown"][0]["items_in_window"] == 1


def test_stats_feed_timeseries_returns_daily_points(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Timeseries Feed",
            "url": "https://example.com/timeseries.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="timeseries-1",
                url="https://example.com/timeseries/1",
                canonical_url="https://example.com/timeseries/1",
                title="Timeseries Item 1",
                summary="day one",
                published_at=now - timedelta(days=2),
                first_seen_at=now - timedelta(days=2),
                dedupe_key="test:timeseries-1",
                content_hash="7" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="timeseries-2",
                url="https://example.com/timeseries/2",
                canonical_url="https://example.com/timeseries/2",
                title="Timeseries Item 2",
                summary="day zero",
                published_at=now - timedelta(days=1),
                first_seen_at=now - timedelta(days=1),
                dedupe_key="test:timeseries-2",
                content_hash="8" * 64,
                status="content_fetched",
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/stats/feed-timeseries?days=7&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 7
    assert len(payload["series"]) == 1
    assert payload["series"][0]["feed_id"] == str(feed_id)
    assert len(payload["series"][0]["points"]) == 7
    assert sum(point["count"] for point in payload["series"][0]["points"]) >= 2


def test_stats_feed_timeseries_includes_more_than_eight_feeds_without_filter(client: TestClient, auth_headers, db_session):
    now = datetime.now(timezone.utc)
    created_feed_ids: list[uuid.UUID] = []

    for index in range(9):
        feed = Feed(
            name=f"Timeseries Feed {index}",
            url=f"https://example.com/timeseries-{index}.xml",
            enabled=True,
            fetch_interval_seconds=1800,
        )
        db_session.add(feed)
        db_session.flush()
        created_feed_ids.append(feed.id)

        db_session.add(
            Item(
                id=uuid.uuid4(),
                feed_id=feed.id,
                source_guid=f"timeseries-default-{index}",
                url=f"https://example.com/timeseries/default/{index}",
                canonical_url=f"https://example.com/timeseries/default/{index}",
                title=f"Timeseries default {index}",
                summary="in-window activity",
                published_at=now - timedelta(days=1),
                first_seen_at=now - timedelta(days=1),
                dedupe_key=f"test:timeseries-default-{index}",
                content_hash=f"{index + 1:064x}",
                status="content_fetched",
            )
        )

    db_session.commit()

    response = client.get("/stats/feed-timeseries?days=7", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    returned_feed_ids = {series["feed_id"] for series in payload["series"]}

    for feed_id in created_feed_ids:
        assert str(feed_id) in returned_feed_ids


def test_stats_feed_timeseries_falls_back_to_first_seen_when_published_missing(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Timeseries Missing Published Feed",
            "url": "https://example.com/timeseries-missing-published.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc)
    db_session.add(
        Item(
            id=uuid.uuid4(),
            feed_id=feed_id,
            source_guid="timeseries-missing-published",
            url="https://example.com/timeseries-missing-published/1",
            canonical_url="https://example.com/timeseries-missing-published/1",
            title="No publication timestamp",
            summary="count should use first_seen_at",
            published_at=None,
            first_seen_at=now - timedelta(days=1),
            dedupe_key="test:timeseries-missing-published",
            content_hash="b" * 64,
            status="content_fetched",
        )
    )
    db_session.commit()

    response = client.get(f"/stats/feed-timeseries?days=7&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["series"]) == 1
    assert sum(point["count"] for point in payload["series"][0]["points"]) == 1


def test_stats_feed_timeseries_uses_publication_date_not_ingestion_date(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Timeseries Publication Feed",
            "url": "https://example.com/timeseries-publication.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="timeseries-publication-old",
                url="https://example.com/timeseries-publication/old",
                canonical_url="https://example.com/timeseries-publication/old",
                title="Old publication date",
                summary="published outside window",
                published_at=now - timedelta(days=20),
                first_seen_at=now - timedelta(days=1),
                dedupe_key="test:timeseries-publication-old",
                content_hash="9" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="timeseries-publication-new",
                url="https://example.com/timeseries-publication/new",
                canonical_url="https://example.com/timeseries-publication/new",
                title="New publication date",
                summary="published inside window",
                published_at=now - timedelta(days=1),
                first_seen_at=now - timedelta(days=20),
                dedupe_key="test:timeseries-publication-new",
                content_hash="a" * 64,
                status="content_fetched",
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/stats/feed-timeseries?days=7&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["series"]) == 1
    assert sum(point["count"] for point in payload["series"][0]["points"]) == 1


def test_stats_activity_heatmap_endpoint(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Heatmap Feed",
            "url": "https://example.com/heatmap.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="heatmap-1",
                url="https://example.com/heatmap/1",
                canonical_url="https://example.com/heatmap/1",
                title="Heatmap recent one",
                summary="recent one",
                published_at=now - timedelta(hours=1),
                first_seen_at=now - timedelta(minutes=10),
                dedupe_key="test:heatmap-1",
                content_hash="b" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="heatmap-2",
                url="https://example.com/heatmap/2",
                canonical_url="https://example.com/heatmap/2",
                title="Heatmap recent two",
                summary="recent two",
                published_at=now - timedelta(hours=1),
                first_seen_at=now - timedelta(minutes=5),
                dedupe_key="test:heatmap-2",
                content_hash="c" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="heatmap-3",
                url="https://example.com/heatmap/3",
                canonical_url="https://example.com/heatmap/3",
                title="Heatmap mid-window",
                summary="mid-window",
                published_at=now - timedelta(days=3, hours=2),
                first_seen_at=now - timedelta(minutes=2),
                dedupe_key="test:heatmap-3",
                content_hash="d" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="heatmap-old",
                url="https://example.com/heatmap/old",
                canonical_url="https://example.com/heatmap/old",
                title="Heatmap old",
                summary="outside window",
                published_at=now - timedelta(days=10),
                first_seen_at=now - timedelta(minutes=1),
                dedupe_key="test:heatmap-old",
                content_hash="e" * 64,
                status="content_fetched",
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/stats/activity-heatmap?days=7&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 7
    assert payload["bucket_unit"] == "hour"
    assert len(payload["bucket_labels"]) == 24
    assert len(payload["rows"]) == 7
    assert all(len(day["counts"]) == 24 for day in payload["rows"])
    assert payload["max_count"] >= 2
    assert sum(sum(day["counts"]) for day in payload["rows"]) == 3

    response_30 = client.get(f"/stats/activity-heatmap?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])
    assert response_30.status_code == 200
    payload_30 = response_30.json()
    assert payload_30["window_days"] == 30
    assert payload_30["bucket_unit"] == "day"
    assert payload_30["bucket_labels"] == ["Daily"]
    assert len(payload_30["rows"]) == 30
    assert all(len(day["counts"]) == 1 for day in payload_30["rows"])
    assert payload_30["max_count"] >= 2
    assert sum(sum(day["counts"]) for day in payload_30["rows"]) == 4


def test_stats_signal_radar_endpoint(client: TestClient, auth_headers, db_session):
    feed_one_response = client.post(
        "/feeds",
        json={
            "name": "Radar Feed One",
            "url": "https://example.com/radar-one.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    feed_two_response = client.post(
        "/feeds",
        json={
            "name": "Radar Feed Two",
            "url": "https://example.com/radar-two.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_one_response.status_code == 201
    assert feed_two_response.status_code == 201

    feed_one_id = uuid.UUID(feed_one_response.json()["id"])
    feed_two_id = uuid.UUID(feed_two_response.json()["id"])
    now = datetime.now(timezone.utc)

    item_one = Item(
        id=uuid.uuid4(),
        feed_id=feed_one_id,
        source_guid="radar-1",
        url="https://example.com/radar/1",
        canonical_url="https://example.com/radar/1",
        title="Radar one",
        summary="vulnerability",
        published_at=now - timedelta(days=1),
        first_seen_at=now - timedelta(days=1),
        dedupe_key="test:radar-1",
        content_hash="f" * 64,
        status="content_fetched",
    )
    item_two = Item(
        id=uuid.uuid4(),
        feed_id=feed_one_id,
        source_guid="radar-2",
        url="https://example.com/radar/2",
        canonical_url="https://example.com/radar/2",
        title="Radar two",
        summary="vulnerability",
        published_at=now - timedelta(days=2),
        first_seen_at=now - timedelta(days=2),
        dedupe_key="test:radar-2",
        content_hash="1" * 64,
        status="content_fetched",
    )
    item_three = Item(
        id=uuid.uuid4(),
        feed_id=feed_one_id,
        source_guid="radar-3",
        url="https://example.com/radar/3",
        canonical_url="https://example.com/radar/3",
        title="Radar three",
        summary="apt campaign",
        published_at=now - timedelta(days=3),
        first_seen_at=now - timedelta(days=3),
        dedupe_key="test:radar-3",
        content_hash="2" * 64,
        status="content_fetched",
    )
    item_other_feed = Item(
        id=uuid.uuid4(),
        feed_id=feed_two_id,
        source_guid="radar-4",
        url="https://example.com/radar/4",
        canonical_url="https://example.com/radar/4",
        title="Radar other feed",
        summary="technology ai",
        published_at=now - timedelta(days=1),
        first_seen_at=now - timedelta(days=1),
        dedupe_key="test:radar-4",
        content_hash="3" * 64,
        status="content_fetched",
    )
    item_old = Item(
        id=uuid.uuid4(),
        feed_id=feed_one_id,
        source_guid="radar-old",
        url="https://example.com/radar/old",
        canonical_url="https://example.com/radar/old",
        title="Radar old",
        summary="outside window",
        published_at=now - timedelta(days=45),
        first_seen_at=now - timedelta(days=45),
        dedupe_key="test:radar-old",
        content_hash="4" * 64,
        status="content_fetched",
    )
    db_session.add_all([item_one, item_two, item_three, item_other_feed, item_old])
    db_session.flush()

    db_session.add_all(
        [
            ItemClassification(
                item_id=item_one.id,
                primary_category="vulnerability",
                secondary_categories=[],
                confidence=0.9,
                scores_json={"vulnerability": 9.0},
                matched_terms_json={"vulnerability": ["title:cve"]},
                source_hash="1" * 64,
            ),
            ItemClassification(
                item_id=item_two.id,
                primary_category="vulnerability",
                secondary_categories=[],
                confidence=0.88,
                scores_json={"vulnerability": 8.8},
                matched_terms_json={"vulnerability": ["summary:vuln"]},
                source_hash="2" * 64,
            ),
            ItemClassification(
                item_id=item_three.id,
                primary_category="apt_campaign",
                secondary_categories=[],
                confidence=0.82,
                scores_json={"apt_campaign": 8.2},
                matched_terms_json={"apt_campaign": ["title:apt"]},
                source_hash="3" * 64,
            ),
            ItemClassification(
                item_id=item_other_feed.id,
                primary_category="technology_ai",
                secondary_categories=[],
                confidence=0.8,
                scores_json={"technology_ai": 8.0},
                matched_terms_json={"technology_ai": ["summary:ai"]},
                source_hash="4" * 64,
            ),
            ItemClassification(
                item_id=item_old.id,
                primary_category="incident_breach",
                secondary_categories=[],
                confidence=0.7,
                scores_json={"incident_breach": 7.0},
                matched_terms_json={"incident_breach": ["summary:breach"]},
                source_hash="5" * 64,
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/stats/signal-radar?days=7&feed_ids={feed_one_id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 7
    assert payload["total"] == 3
    assert payload["max_count"] == 2

    by_category = {entry["category"]: entry for entry in payload["axes"]}
    assert by_category["vulnerability"]["count"] == 2
    assert by_category["apt_campaign"]["count"] == 1
    assert by_category["technology_ai"]["count"] == 0
    assert by_category["vulnerability"]["pct"] > by_category["apt_campaign"]["pct"]


def test_stats_widgets_share_the_same_day_aligned_window(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Boundary Feed",
            "url": "https://example.com/boundary.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    now = datetime.now(timezone.utc)
    outside_day_aligned_window = now - timedelta(days=30) + timedelta(hours=1)
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="boundary-window-item",
        url="https://example.com/boundary/item",
        canonical_url="https://example.com/boundary/item",
        title="Boundary item",
        summary="Should stay outside the selected 30-day UTC calendar window.",
        published_at=outside_day_aligned_window,
        first_seen_at=outside_day_aligned_window,
        dedupe_key="test:boundary-window-item",
        content_hash="6" * 64,
        status="content_fetched",
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        ItemClassification(
            item_id=item.id,
            primary_category="vulnerability",
            secondary_categories=[],
            confidence=0.91,
            scores_json={"vulnerability": 9.1},
            matched_terms_json={"vulnerability": ["title:boundary"]},
            source_hash="7" * 64,
        )
    )
    db_session.commit()

    overview = client.get(f"/stats/overview?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])
    feed_timeseries = client.get(f"/stats/feed-timeseries?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])
    heatmap = client.get(f"/stats/activity-heatmap?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])
    radar = client.get(f"/stats/signal-radar?days=30&feed_ids={feed_id}", headers=auth_headers["viewer"])

    assert overview.status_code == 200
    assert feed_timeseries.status_code == 200
    assert heatmap.status_code == 200
    assert radar.status_code == 200

    overview_payload = overview.json()
    feed_timeseries_payload = feed_timeseries.json()
    heatmap_payload = heatmap.json()
    radar_payload = radar.json()

    assert overview_payload["window_start_at"] == feed_timeseries_payload["window_start_at"]
    assert overview_payload["window_start_at"] == heatmap_payload["window_start_at"]
    assert overview_payload["window_start_at"] == radar_payload["window_start_at"]
    assert overview_payload["window_end_at"] >= overview_payload["window_start_at"]
    assert feed_timeseries_payload["window_end_at"] >= feed_timeseries_payload["window_start_at"]
    assert heatmap_payload["window_end_at"] >= heatmap_payload["window_start_at"]
    assert radar_payload["window_end_at"] >= radar_payload["window_start_at"]
    assert sum(point["count"] for point in overview_payload["daily_volume"]) == 0
    assert overview_payload["feed_breakdown"][0]["items_in_window"] == 0
    assert sum(point["count"] for point in feed_timeseries_payload["series"][0]["points"]) == 0
    assert sum(sum(day["counts"]) for day in heatmap_payload["rows"]) == 0
    assert radar_payload["total"] == 0


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


def test_items_support_tag_filters(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "TagFilterFeed",
            "url": "https://example.com/tag-filter.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    item_one = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="tag-item-one",
        url="https://example.com/tag-one",
        canonical_url="https://example.com/tag-one",
        title="Tag Item One",
        summary="critical only",
        published_at=datetime.now(timezone.utc),
        dedupe_key="test:tag-item-one",
        content_hash="1" * 64,
        status="new",
    )
    item_two = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="tag-item-two",
        url="https://example.com/tag-two",
        canonical_url="https://example.com/tag-two",
        title="Tag Item Two",
        summary="malware only",
        published_at=datetime.now(timezone.utc),
        dedupe_key="test:tag-item-two",
        content_hash="2" * 64,
        status="new",
    )
    item_three = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="tag-item-three",
        url="https://example.com/tag-three",
        canonical_url="https://example.com/tag-three",
        title="Tag Item Three",
        summary="critical and malware",
        published_at=datetime.now(timezone.utc),
        dedupe_key="test:tag-item-three",
        content_hash="3" * 64,
        status="new",
    )
    db_session.add_all([item_one, item_two, item_three])
    db_session.flush()

    critical_tag = Tag(name="critical")
    malware_tag = Tag(name="malware")
    db_session.add_all([critical_tag, malware_tag])
    db_session.flush()

    db_session.add_all(
        [
            ItemTag(item_id=item_one.id, tag_id=critical_tag.id),
            ItemTag(item_id=item_two.id, tag_id=malware_tag.id),
            ItemTag(item_id=item_three.id, tag_id=critical_tag.id),
            ItemTag(item_id=item_three.id, tag_id=malware_tag.id),
        ]
    )
    db_session.commit()

    legacy_response = client.get("/items?page=1&page_size=50&tag=critical", headers=auth_headers["viewer"])
    assert legacy_response.status_code == 200
    assert legacy_response.json()["total"] == 2

    any_response = client.get("/items?page=1&page_size=50&tags=critical,malware", headers=auth_headers["viewer"])
    assert any_response.status_code == 200
    assert any_response.json()["total"] == 3

    all_response = client.get(
        "/items?page=1&page_size=50&tags=critical,malware&tags_mode=all",
        headers=auth_headers["viewer"],
    )
    assert all_response.status_code == 200
    assert all_response.json()["total"] == 1


def test_set_item_tags_rejects_duplicate_tag_ids(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "DuplicateTagFeed",
            "url": "https://example.com/duplicate-tag.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="dup-tag-item",
        url="https://example.com/dup-tag-item",
        canonical_url="https://example.com/dup-tag-item",
        title="Duplicate tag target",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dup-tag-item",
        content_hash="9" * 64,
        status="new",
    )
    tag = Tag(name="triage")
    db_session.add_all([item, tag])
    db_session.commit()

    response = client.post(
        f"/items/{item.id}/tags",
        json={"tag_ids": [str(tag.id), str(tag.id)]},
        headers=auth_headers["admin"],
    )
    assert response.status_code == 422


def test_list_tags_only_returns_tags_attached_to_items(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "VisibleTagFeed",
            "url": "https://example.com/visible-tag.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="visible-tag-item",
        url="https://example.com/visible-tag-item",
        canonical_url="https://example.com/visible-tag-item",
        title="Visible tag target",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="visible-tag-item",
        content_hash="a" * 64,
        status="new",
    )
    active_tag = Tag(name="active_tag")
    orphan_tag = Tag(name="campaign:legacy")
    db_session.add_all([item, active_tag, orphan_tag])
    db_session.flush()
    db_session.add(ItemTag(item_id=item.id, tag_id=active_tag.id, source="rule", confidence=0.8, rules_version="tagging_v2"))
    db_session.commit()

    response = client.get("/tags", headers=auth_headers["viewer"])
    assert response.status_code == 200

    names = [entry["name"] for entry in response.json()]
    assert "active_tag" in names
    assert "campaign:legacy" not in names


def test_set_item_tags_records_feedback_and_metadata(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "TagFeedbackFeed",
            "url": "https://example.com/tag-feedback.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="tag-feedback-item",
        url="https://example.com/tag-feedback-item",
        canonical_url="https://example.com/tag-feedback-item",
        title="Tag feedback target",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="tag-feedback-item",
        content_hash="8" * 64,
        status="new",
    )
    old_tag = Tag(name="oldtag")
    new_tag = Tag(name="newtag")
    db_session.add_all([item, old_tag, new_tag])
    db_session.flush()
    db_session.add(ItemTag(item_id=item.id, tag_id=old_tag.id, source="rule", confidence=0.66, rules_version="legacy"))
    db_session.commit()

    response = client.post(
        f"/items/{item.id}/tags",
        json={"tag_ids": [str(new_tag.id)]},
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200

    link = db_session.scalar(select(ItemTag).where(ItemTag.item_id == item.id, ItemTag.tag_id == new_tag.id))
    assert link is not None
    assert link.source == "manual"
    assert link.confidence == 1.0
    assert link.rules_version == "manual:v1"

    feedback_rows = db_session.execute(
        select(TagFeedbackEvent.signal_type, TagFeedbackEvent.tag_name)
        .where(TagFeedbackEvent.item_id == item.id)
        .order_by(TagFeedbackEvent.signal_type.asc(), TagFeedbackEvent.tag_name.asc())
    ).all()
    assert ("manual_add", "newtag") in feedback_rows
    assert ("manual_remove", "oldtag") in feedback_rows


def test_star_and_read_actions_record_feedback(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "StateFeedbackFeed",
            "url": "https://example.com/state-feedback.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="state-feedback-item",
        url="https://example.com/state-feedback-item",
        canonical_url="https://example.com/state-feedback-item",
        title="State feedback target",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="state-feedback-item",
        content_hash="7" * 64,
        status="new",
    )
    tag = Tag(name="triage")
    db_session.add_all([item, tag])
    db_session.flush()
    db_session.add(ItemTag(item_id=item.id, tag_id=tag.id, source="rule", confidence=0.7, rules_version="tagging_v2"))
    db_session.commit()

    star_response = client.post(
        f"/items/{item.id}/star",
        json={"is_starred": True},
        headers=auth_headers["admin"],
    )
    assert star_response.status_code == 200

    read_response = client.post(
        f"/items/{item.id}/read",
        json={"is_read": True},
        headers=auth_headers["admin"],
    )
    assert read_response.status_code == 200

    feedback_rows = db_session.execute(
        select(TagFeedbackEvent.signal_type, TagFeedbackEvent.tag_name).where(TagFeedbackEvent.item_id == item.id)
    ).all()
    assert ("star", "triage") in feedback_rows
    assert ("read", "triage") in feedback_rows


def test_item_detail_returns_tag_details_and_suggestions(client: TestClient, auth_headers, db_session):
    feed_response = client.post(
        "/feeds",
        json={
            "name": "Securelist Threat Research",
            "url": "https://securelist.com/feed/",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert feed_response.status_code == 201
    feed_id = uuid.UUID(feed_response.json()["id"])

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed_id,
        source_guid="detail-tags-item",
        url="https://example.com/detail-tags-item",
        canonical_url="https://example.com/detail-tags-item",
        title="Mustang Panda campaign observed in targeted attacks",
        summary="Threat intelligence update",
        published_at=datetime.now(timezone.utc),
        dedupe_key="detail-tags-item",
        content_hash="6" * 64,
        status="content_fetched",
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="apt_campaign",
        secondary_categories=["threat_intelligence_research"],
        confidence=0.82,
        scores_json={"apt_campaign": 4.2},
        matched_terms_json={"apt_campaign": ["title:campaign"]},
        source_hash="abc123",
        rules_version="v2",
        classified_at=datetime.now(timezone.utc),
    )
    existing_tag = Tag(name="apt_campaign")
    db_session.add_all([item, classification, existing_tag])
    db_session.flush()
    db_session.add(ItemTag(item_id=item.id, tag_id=existing_tag.id, source="rule", confidence=0.82, rules_version="tagging_v2"))
    db_session.commit()

    detail_response = client.get(f"/items/{item.id}", headers=auth_headers["viewer"])
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["tag_details"]
    assert payload["tag_details"][0]["source"] == "rule"
    assert payload["tag_details"][0]["rules_version"] == "tagging_v2"
    suggestion_names = {entry["name"] for entry in payload["tag_suggestions"]}
    assert suggestion_names == {"threat_intelligence_research"}

    suggestions_response = client.get(f"/items/{item.id}/tag-suggestions", headers=auth_headers["viewer"])
    assert suggestions_response.status_code == 200
    assert suggestions_response.json()["item_id"] == str(item.id)


def test_alert_interest_crud_and_matching(client: TestClient, auth_headers, db_session):
    create_feed = client.post(
        "/feeds",
        json={
            "name": "AlertFeed",
            "url": "https://example.com/alerts.xml",
            "fetch_interval_seconds": 1800,
            "enabled": True,
        },
        headers=auth_headers["admin"],
    )
    assert create_feed.status_code == 201
    feed_id = uuid.UUID(create_feed.json()["id"])

    db_session.add_all(
        [
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="alert-item-1",
                url="https://example.com/alerts/1",
                canonical_url="https://example.com/alerts/1",
                title="Microsoft releases patch for Exchange",
                summary="Patch bundle addresses multiple vulnerabilities.",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:alert-item-1",
                content_hash="4" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="alert-item-2",
                url="https://example.com/alerts/2",
                canonical_url="https://example.com/alerts/2",
                title="APT29 campaign expands against cloud providers",
                summary="Cozy Bear activity targets credential theft.",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:alert-item-2",
                content_hash="5" * 64,
                status="content_fetched",
            ),
            Item(
                id=uuid.uuid4(),
                feed_id=feed_id,
                source_guid="alert-item-3",
                url="https://example.com/alerts/3",
                canonical_url="https://example.com/alerts/3",
                title="General threat roundup",
                summary="No specific actor or vendor details.",
                published_at=datetime.now(timezone.utc),
                dedupe_key="test:alert-item-3",
                content_hash="6" * 64,
                status="content_fetched",
            ),
        ]
    )
    db_session.commit()

    vendor_alert = client.post(
        "/alerts",
        json={"name": "Microsoft Vendors", "category": "vendor", "keywords": ["Microsoft", "Exchange"], "enabled": True},
        headers=auth_headers["viewer"],
    )
    assert vendor_alert.status_code == 201
    vendor_id = vendor_alert.json()["id"]

    apt_alert = client.post(
        "/alerts",
        json={"name": "APT29", "category": "apt_group", "keywords": ["apt29", "cozy bear"], "enabled": True},
        headers=auth_headers["viewer"],
    )
    assert apt_alert.status_code == 201
    apt_id = apt_alert.json()["id"]

    list_response = client.get("/alerts", headers=auth_headers["viewer"])
    assert list_response.status_code == 200
    assert len(list_response.json()) == 2

    matches_response = client.get("/alerts/matches?page=1&page_size=25", headers=auth_headers["viewer"])
    assert matches_response.status_code == 200
    payload = matches_response.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 2
    assert any(match["category"] == "vendor" for item in payload["items"] for match in item["matches"])
    assert any(match["category"] == "apt_group" for item in payload["items"] for match in item["matches"])

    category_filtered = client.get("/alerts/matches?categories=apt_group", headers=auth_headers["viewer"])
    assert category_filtered.status_code == 200
    filtered_payload = category_filtered.json()
    assert filtered_payload["total"] == 1
    assert filtered_payload["items"][0]["title"].lower().startswith("apt29")

    preview_response = client.post(
        "/alerts/preview",
        json={"name": "Preview", "category": "vendor", "keywords": ["microsoft", "exchange"], "limit": 3},
        headers=auth_headers["viewer"],
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["total"] == 1
    assert len(preview_payload["items"]) == 1
    assert preview_payload["items"][0]["title"].lower().startswith("microsoft")
    assert preview_payload["items"][0]["matches"][0]["matched_keywords"] == ["microsoft", "exchange"]

    id_filtered = client.get(f"/alerts/matches?alert_ids={vendor_id}", headers=auth_headers["viewer"])
    assert id_filtered.status_code == 200
    id_payload = id_filtered.json()
    assert id_payload["total"] == 1
    assert id_payload["items"][0]["title"].lower().startswith("microsoft")

    disable_response = client.patch(
        f"/alerts/{apt_id}",
        json={"enabled": False},
        headers=auth_headers["viewer"],
    )
    assert disable_response.status_code == 200

    matches_without_disabled = client.get("/alerts/matches", headers=auth_headers["viewer"])
    assert matches_without_disabled.status_code == 200
    without_disabled_payload = matches_without_disabled.json()
    assert without_disabled_payload["total"] == 1
    assert without_disabled_payload["items"][0]["title"].lower().startswith("microsoft")

    delete_response = client.delete(f"/alerts/{vendor_id}", headers=auth_headers["viewer"])
    assert delete_response.status_code == 204
