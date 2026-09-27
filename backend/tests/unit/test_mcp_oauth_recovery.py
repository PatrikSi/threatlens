"""Configuration and transient OAuth errors remain controlled and actionable."""

import asyncio
from types import SimpleNamespace
from urllib.parse import urlencode
import uuid

import pytest
from starlette.requests import Request

from app.api.routes import mcp_oauth as routes
from app.core.api_errors import ApiHTTPException
from app.services import mcp_oauth as service


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.example:99999",
        "https://app.example:not-a-port",
        "https://[broken.example",
        "https://app.example\\attacker.example",
        "https://app.example\n",
        "https://app.example /",
    ],
)
def test_invalid_oauth_origin_produces_configuration_error(monkeypatch, origin):
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            public_app_url=origin,
            mcp_enabled=True,
            mcp_oauth_enabled=True,
        ),
    )
    with pytest.raises(ApiHTTPException) as error:
        service.oauth_urls()
    assert error.value.status_code == 503
    assert error.value.error_code == "oauth_configuration_invalid"
    assert service.challenge_header() == "Bearer"


def test_normalized_exchange_errors_keep_safe_retry_and_authentication_headers(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "oauth_urls",
        lambda: ("https://app.example", "https://app.example/api/v1/mcp"),
    )

    def busy(*_args):
        raise ApiHTTPException(
            status_code=503,
            error_code="operation_busy",
            detail="Retry this request.",
            headers={
                "Retry-After": "3",
                "WWW-Authenticate": "Bearer",
                "X-Unsafe": "excluded",
            },
        )

    monkeypatch.setattr(routes, "_operation", busy)
    payload = urlencode(
        {
            "grant_type": "authorization_code",
            "code": "a" * 43,
            "client_id": str(uuid.uuid4()),
            "redirect_uri": "https://client.example/callback",
            "resource": "https://app.example/api/v1/mcp",
            "code_verifier": "v" * 43,
        }
    ).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp/oauth/token",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
        receive=receive,
    )
    response = asyncio.run(routes.exchange_mcp_authorization_code(request, db=object()))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert "x-unsafe" not in response.headers
