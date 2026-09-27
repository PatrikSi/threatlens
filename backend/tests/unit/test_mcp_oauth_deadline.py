"""Slow unauthenticated token bodies cannot retain request tasks indefinitely."""

import asyncio
import json
from starlette.requests import Request
from app.api.routes import mcp_oauth as routes


def test_token_body_has_an_absolute_deadline_without_consuming_code(monkeypatch):
    monkeypatch.setattr(routes, "TOKEN_BODY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        routes.service,
        "oauth_urls",
        lambda: ("https://app.example", "https://app.example/api/v1/mcp"),
    )

    def unexpected(*_args):
        raise AssertionError("Timed-out bodies cannot begin a code exchange")

    monkeypatch.setattr(routes, "_operation", unexpected)

    async def receive():
        await asyncio.sleep(0.004)
        return {"type": "http.request", "body": b"x", "more_body": True}

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
    assert response.status_code == 408
    assert json.loads(response.body)["error"] == "invalid_request"
    assert response.headers["cache-control"] == "no-store"
