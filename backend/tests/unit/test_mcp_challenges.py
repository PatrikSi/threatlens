"""Complete OAuth challenges without suggesting broader evidence authorization."""

from types import SimpleNamespace

import pytest

from app.api.routes import mcp
from app.core.api_errors import ApiHTTPException
from app.services import mcp_challenges, mcp_transport
from app.services.mcp_protocol import LATEST_PROTOCOL_VERSION, MCPRequest


@pytest.fixture(autouse=True)
def configured_origin(monkeypatch):
    monkeypatch.setattr(
        mcp_challenges,
        "oauth_urls",
        lambda: ("https://threatlens.example", "https://threatlens.example/api/v1/mcp"),
    )


def _request(name="get_hunt_queue"):
    return MCPRequest(1, "tools/call", {"name": name}, LATEST_PROTOCOL_VERSION)


@pytest.mark.parametrize(
    "status,code", [(401, "invalid_token"), (403, "mcp_scope_required")]
)
def test_challenge_includes_all_operation_scopes_and_discovery(status, code):
    error = ApiHTTPException(
        status_code=status,
        error_code=code,
        detail="Access denied",
        headers={"WWW-Authenticate": "Bearer"},
    )
    response = mcp._error_response(_request(), error)
    challenge = response.headers["www-authenticate"]
    assert 'scope="read:items read:mcp read:teams"' in challenge
    assert (
        'resource_metadata="https://threatlens.example/.well-known/oauth-protected-resource/api/v1/mcp"'
        in challenge
    )
    assert ('error="insufficient_scope"' in challenge) == (status == 403)


def test_non_scope_access_denial_does_not_suggest_step_up():
    response = mcp._error_response(
        _request(),
        ApiHTTPException(
            status_code=403, error_code="data_access_denied", detail="Unavailable"
        ),
    )
    assert "www-authenticate" not in response.headers


def test_delegated_tool_scope_gate_does_not_reveal_unknown_tools():
    context = SimpleNamespace(
        authorization=SimpleNamespace(
            has=lambda scope: scope in {"read:mcp", "read:items"}
        )
    )
    mcp._require_delegated_tool_scopes(context, _request("unknown_tool"))
    mcp._require_delegated_tool_scopes(context, _request("get_article_evidence"))
    with pytest.raises(ApiHTTPException) as error:
        mcp._require_delegated_tool_scopes(context, _request())
    assert error.value.error_code == "mcp_insufficient_scope"


def test_transport_unauthenticated_response_includes_discovery():
    response = mcp_transport.transport_error(
        401, "mcp_bearer_required", "Bearer required"
    )
    assert "resource_metadata=" in response.headers["www-authenticate"]
