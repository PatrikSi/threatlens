from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.api import deps


def _make_request(*, client_host: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": headers,
        "client": (client_host, 12345),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_resolve_client_ip_walks_back_to_first_untrusted_client_hop(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs=["172.16.0.0/12", "198.51.100.0/24"]),
    )
    request = _make_request(client_host="172.18.0.2", forwarded_for="203.0.113.10, 198.51.100.44")
    assert deps.resolve_client_ip(request) == "203.0.113.10"


def test_resolve_client_ip_stops_when_forwarded_chain_reaches_untrusted_proxy(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs=["172.16.0.0/12"]),
    )
    request = _make_request(client_host="172.18.0.2", forwarded_for="203.0.113.10, 198.51.100.44")
    assert deps.resolve_client_ip(request) == "198.51.100.44"


def test_resolve_client_ip_ignores_forwarded_header_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs=["172.16.0.0/12"]),
    )
    request = _make_request(client_host="198.51.100.7", forwarded_for="203.0.113.10")
    assert deps.resolve_client_ip(request) == "198.51.100.7"


def test_resolve_client_ip_ignores_invalid_forwarded_hops(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs=["172.16.0.0/12", "198.51.100.0/24"]),
    )
    request = _make_request(
        client_host="172.18.0.2",
        forwarded_for="garbage, 203.0.113.10, not-an-ip, 198.51.100.44",
    )
    assert deps.resolve_client_ip(request) == "203.0.113.10"


def test_require_token_scopes_rejects_session_jwt_used_in_authorization_header():
    checker = deps.require_token_scopes("read:feeds")
    request = _make_request(client_host="127.0.0.1")
    request.state.token_scopes = None
    request.state.auth_credential_kind = deps.AUTH_SESSION_BEARER

    with pytest.raises(deps.HTTPException) as excinfo:
        checker(request=request, user=object())

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Bearer auth requires a scoped API token"
    assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}


def test_require_token_scopes_allows_cookie_session_without_api_token_scopes():
    checker = deps.require_token_scopes("read:feeds")
    request = _make_request(client_host="127.0.0.1")
    request.state.token_scopes = None
    request.state.auth_credential_kind = deps.AUTH_SESSION_COOKIE
    user = object()

    assert checker(request=request, user=user) is user


def test_require_token_scopes_enforces_missing_api_token_scope():
    checker = deps.require_token_scopes("write:feeds")
    request = _make_request(client_host="127.0.0.1")
    request.state.token_scopes = ["read:feeds"]
    request.state.auth_credential_kind = deps.AUTH_API_TOKEN

    with pytest.raises(deps.HTTPException) as excinfo:
        checker(request=request, user=object())

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Insufficient token scope"
