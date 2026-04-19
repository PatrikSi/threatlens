from types import SimpleNamespace

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


def test_resolve_client_ip_uses_last_forwarded_hop_from_trusted_proxy(monkeypatch):
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
