"""Actual MCP HTTP discovery and delegated scope step-up responses."""

import hashlib
import secrets
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.api_token import ApiToken
from app.models.mcp_oauth import MCPDelegation, MCPOAuthClient
from tests.integration.test_mcp_http import _request


@pytest.mark.parametrize("token", [None, "tlp_invalid", "tlmcp_invalid"])
def test_all_unauthenticated_mcp_requests_advertise_discovery(
    mcp_http_environment, monkeypatch, token
):
    monkeypatch.setenv("MCP_OAUTH_ENABLED", "true")
    get_settings.cache_clear()
    response = _request(mcp_http_environment, token=token)
    assert response.status_code == 401, response.text
    assert (
        'resource_metadata="https://threatlens.example/.well-known/oauth-protected-resource/api/v1/mcp"'
        in response.headers["www-authenticate"]
    )


def test_delegated_scope_denial_exposes_complete_challenge_to_allowed_browser(
    mcp_http_environment, monkeypatch
):
    monkeypatch.setenv("MCP_OAUTH_ENABLED", "true")
    get_settings.cache_clear()
    env = mcp_http_environment
    raw = "tlmcp_" + secrets.token_urlsafe(40)
    with Session(env.engine) as db:
        client = MCPOAuthClient(
            id=uuid.uuid4(),
            name="Scope test",
            redirect_uris=["https://client.example/callback"],
        )
        db.add(client)
        token = db.get(ApiToken, env.credential_id)
        token.token_prefix = raw[:22]
        token.token_hash = hashlib.sha256(raw.encode()).hexdigest()
        db.add(
            MCPDelegation(
                token_id=token.id,
                client_id=client.id,
                resource="https://threatlens.example/api/v1/mcp",
                label_cap_json={"enforced": False, "allowed_label_ids": []},
            )
        )
        db.commit()
        client_id = client.id
    try:
        response = _request(
            env,
            "tools/call",
            name="get_report",
            arguments={"report_id": str(uuid.uuid4())},
            token=raw,
            headers={"Origin": "https://client.example"},
        )
        assert response.status_code == 403, response.text
        challenge = response.headers["www-authenticate"]
        assert 'error="insufficient_scope"' in challenge
        assert 'scope="read:items read:mcp read:reports"' in challenge
        assert "resource_metadata=" in challenge
        assert (
            "www-authenticate"
            in response.headers["access-control-expose-headers"].lower()
        )
        assert _request(env, token=raw).status_code == 200
    finally:
        with Session(env.engine) as db:
            db.execute(
                delete(MCPDelegation).where(MCPDelegation.client_id == client_id)
            )
            db.execute(delete(MCPOAuthClient).where(MCPOAuthClient.id == client_id))
            db.commit()
