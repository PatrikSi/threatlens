import socket

import pytest

from app.services.safe_fetch import UnsafeTargetError, _PinnedSyncBackend
from app.services.url_utils import ensure_runtime_fetchable_url, is_runtime_fetchable_url


def test_runtime_fetchable_url_allows_public_dns_resolution(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )

    assert is_runtime_fetchable_url("https://example.com/feed.xml")


def test_runtime_fetchable_url_blocks_private_dns_resolution_by_default(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.2.25", 0)),
        ],
    )

    assert not is_runtime_fetchable_url("https://internal.example.com/feed.xml")


def test_runtime_fetchable_url_blocks_local_host_suffix_by_default(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.25", 0)),
        ],
    )

    assert not is_runtime_fetchable_url("https://corp.local/feed.xml")


def test_runtime_fetchable_url_can_allow_private_targets_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ],
    )

    assert is_runtime_fetchable_url("https://localhost/feed.xml", allow_private_network=True)


def test_ensure_runtime_fetchable_url_raises_for_private_targets_by_default(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ],
    )

    with pytest.raises(ValueError):
        ensure_runtime_fetchable_url("https://localhost/feed.xml")


def test_pinned_sync_backend_connects_to_vetted_ip_instead_of_re_resolving_host(monkeypatch):
    backend = _PinnedSyncBackend(allow_private_network=False)
    captured: dict[str, object] = {}

    class _FakeBackend:
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout
            return object()

        def connect_unix_socket(self, path, timeout=None, socket_options=None):
            _ = (path, timeout, socket_options)
            return object()

        def sleep(self, seconds: float) -> None:
            _ = seconds

    monkeypatch.setattr("app.services.safe_fetch.resolve_runtime_allowed_ips", lambda *_args, **_kwargs: ["93.184.216.34"])
    monkeypatch.setattr(backend, "_backend", _FakeBackend())

    backend.connect_tcp("example.com", 443, timeout=5.0)

    assert captured["host"] == "93.184.216.34"
    assert captured["port"] == 443
    assert captured["timeout"] == 5.0


def test_pinned_sync_backend_rejects_hosts_without_allowed_runtime_ips(monkeypatch):
    backend = _PinnedSyncBackend(allow_private_network=False)
    monkeypatch.setattr("app.services.safe_fetch.resolve_runtime_allowed_ips", lambda *_args, **_kwargs: [])

    with pytest.raises(UnsafeTargetError, match="URL is not allowed for outbound fetch"):
        backend.connect_tcp("example.com", 443)
