"""Real consent, PKCE exchange, audience isolation and revocation boundaries."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest
from starlette.requests import Request
from sqlalchemy import select

from app.api.mcp_context import resolve_mcp_read_context
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.models.api_token import ApiToken
from app.models.mcp_oauth import MCPOAuthCode


@pytest.fixture()
def oauth_env(client, auth_headers, monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://threatlens.example")
    get_settings.cache_clear()
    result = client.post(
        "/mcp/oauth/clients",
        json={
            "name": "Analyst client",
            "redirect_uris": ["http://127.0.0.1:8123/callback"],
        },
        headers=auth_headers["admin"],
    )
    assert result.status_code == 201, result.text
    verifier = "x" * 43
    request = {
        "client_id": result.json()["client_id"],
        "redirect_uri": "http://127.0.0.1:8123/callback",
        "resource": "https://threatlens.example/api/v1/mcp",
        "response_type": "code",
        "state": "a-state-at-least-16-chars",
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        )
        .decode()
        .rstrip("="),
        "scope": "read:mcp read:items",
    }
    login = client.post(
        "/auth/login",
        json={"email": "analyst@example.com", "password": "AnalystPass123!"},
    )
    assert login.status_code == 200, login.text
    headers = {"X-CSRF-Token": client.cookies.get("threatlens_csrf")}
    return request, verifier, headers


def authorize(client, env):
    request, verifier, headers = env
    result = client.post(
        "/mcp/oauth/authorize",
        json={**request, "approve": True, "current_password": "AnalystPass123!"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    callback = parse_qs(urlsplit(result.json()["redirect_uri"]).query)
    assert callback["state"] == [request["state"]] and callback["iss"] == [
        "https://threatlens.example"
    ]
    return {
        "grant_type": "authorization_code",
        "code": callback["code"][0],
        "client_id": request["client_id"],
        "redirect_uri": request["redirect_uri"],
        "resource": request["resource"],
        "code_verifier": verifier,
    }


def mcp_request(token):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/mcp",
            "query_string": b"",
            "headers": [(b"authorization", ("Bearer " + token).encode())],
        }
    )


def test_oauth_pkce_is_single_use_and_tokens_have_only_mcp_audience(
    client, oauth_env, db_session
):
    exchange = authorize(client, oauth_env)
    result = client.post("/mcp/oauth/token", data=exchange)
    assert result.status_code == 200, result.text
    token = result.json()["access_token"]
    assert token.startswith("tlmcp_") and result.json()["expires_in"] == 900
    assert result.headers["cache-control"] == "no-store"
    assert client.post("/mcp/oauth/token", data=exchange).status_code == 400
    assert (
        client.get("/items", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )
    context = resolve_mcp_read_context(
        mcp_request(token), db_session, cursor_secret=b"a" * 32
    )
    assert context.authorization.has("read:mcp")
    assert not context.authorization.has("write:items")
    db_session.rollback()


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_verifier", "y" * 43),
        ("resource", "https://elsewhere.example/mcp"),
        ("redirect_uri", "http://127.0.0.1:8124/callback"),
        ("client_id", str(uuid.uuid4())),
    ],
)
def test_oauth_exchange_rejects_binding_mismatches(client, oauth_env, field, value):
    exchange = authorize(client, oauth_env)
    bad = client.post("/mcp/oauth/token", data={**exchange, field: value})
    assert bad.status_code == 400, bad.text
    assert "access_token" not in bad.json()
    assert client.post("/mcp/oauth/token", data=exchange).status_code == 200


def test_oauth_requires_browser_consent_csrf_and_step_up(
    client, oauth_env, auth_headers
):
    request, _, headers = oauth_env
    assert (
        client.post(
            "/mcp/oauth/authorize",
            json={**request, "approve": True, "current_password": "AnalystPass123!"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/mcp/oauth/authorize", json={**request, "approve": True}, headers=headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/mcp/oauth/authorize",
            json={**request, "approve": True},
            headers=auth_headers["analyst"],
        ).status_code
        == 403
    )
    denied = client.post(
        "/mcp/oauth/authorize", json={**request, "approve": False}, headers=headers
    )
    assert denied.status_code == 200, denied.text
    assert parse_qs(urlsplit(denied.json()["redirect_uri"]).query)["error"] == [
        "access_denied"
    ]
    invalid = client.post(
        "/mcp/oauth/consent-preview",
        json={**request, "redirect_uri": "https://evil.example"},
        headers=headers,
    )
    assert invalid.status_code == 400


def test_revoked_client_and_expired_code_fail_closed(
    client, oauth_env, auth_headers, db_session
):
    exchange = authorize(client, oauth_env)
    row = db_session.scalar(
        select(MCPOAuthCode).where(
            MCPOAuthCode.code_hash
            == hashlib.sha256(exchange["code"].encode()).hexdigest()
        )
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert client.post("/mcp/oauth/token", data=exchange).status_code == 400
    new = client.post("/mcp/oauth/token", data=authorize(client, oauth_env)).json()[
        "access_token"
    ]
    revoked = client.delete(
        f"/mcp/oauth/clients/{exchange['client_id']}", headers=auth_headers["admin"]
    )
    assert revoked.status_code == 204, revoked.text
    with pytest.raises(ApiHTTPException, match="revoked"):
        resolve_mcp_read_context(mcp_request(new), db_session, cursor_secret=b"a" * 32)
    assert (
        db_session.scalar(
            select(ApiToken.revoked_at).where(
                ApiToken.token_hash == hashlib.sha256(new.encode()).hexdigest()
            )
        )
        is not None
    )


def test_discovery_uses_configured_origin_and_strict_request_parsing(client, oauth_env):
    metadata = client.get(
        "/.well-known/oauth-authorization-server", headers={"Host": "evil.example"}
    )
    assert (
        metadata.status_code == 400
    )  # TrustedHostMiddleware rejects an injected host.
    metadata = client.get("/.well-known/oauth-authorization-server")
    assert (
        metadata.status_code == 200
        and metadata.json()["issuer"] == "https://threatlens.example"
    )
    protected = client.get("/.well-known/oauth-protected-resource/api/v1/mcp")
    assert protected.json()["resource"] == "https://threatlens.example/api/v1/mcp"
    assert (
        client.post(
            "/mcp/oauth/token",
            content="code=x&code=y",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/mcp/oauth/token",
            content="x" * 8193,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 413
    )
