import socket

import httpcore
import httpx
import pytest

from app.services.safe_fetch import UnsafeTargetError, _PinnedSyncBackend, build_safe_http_client


@pytest.mark.parametrize(
    "candidate",
    ["8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8", "0.0.0.0", "::", "224.0.0.1", "ff02::1", "240.0.0.1"],
)
def test_private_only_client_never_connects_to_public_or_nonunicast_dns(monkeypatch, candidate):
    family = socket.AF_INET6 if ":" in candidate else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (family, socket.SOCK_STREAM, 6, "", (candidate, 11434)),
    ])
    connections = []

    def unexpected_connection(*_args, **kwargs):
        connections.append(kwargs)
        raise AssertionError("Disallowed address reached the socket backend")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", unexpected_connection)
    with build_safe_http_client(
        timeout=httpx.Timeout(1), allow_private_network=True, private_network_only=True,
    ) as client:
        with pytest.raises(UnsafeTargetError):
            client.post("http://ai.internal:11434/v1/chat/completions", headers={"Authorization": "Bearer test-only-key"})
    assert connections == []


@pytest.mark.parametrize("candidate", ["127.0.0.1", "10.0.0.2", "192.168.1.2", "169.254.1.2", "100.64.0.1", "::1", "fd00::2", "fe80::2", "::ffff:192.168.1.2"])
def test_private_only_backend_pins_nonpublic_unicast_answers(monkeypatch, candidate):
    backend = _PinnedSyncBackend(allow_private_network=True, private_network_only=True)
    connections = []
    monkeypatch.setattr("app.services.safe_fetch.resolve_runtime_allowed_ips", lambda *_args, **_kwargs: ["8.8.8.8", candidate])

    def connect(**kwargs):
        connections.append(kwargs)
        return object()

    monkeypatch.setattr(backend._backend, "connect_tcp", connect)
    backend.connect_tcp("ai.internal", 11434, timeout=2, local_address=None)
    assert [entry["host"] for entry in connections] == [candidate]
    assert connections[0]["port"] == 11434
    assert connections[0]["timeout"] == 2


def test_private_only_backend_rechecks_dns_before_each_connection(monkeypatch):
    answers = iter([["192.168.1.2"], ["8.8.8.8"]])
    monkeypatch.setattr("app.services.safe_fetch.resolve_runtime_allowed_ips", lambda *_args, **_kwargs: next(answers))
    backend = _PinnedSyncBackend(allow_private_network=True, private_network_only=True)
    connections = []

    def connect(**kwargs):
        connections.append(kwargs["host"])
        return object()

    monkeypatch.setattr(backend._backend, "connect_tcp", connect)
    backend.connect_tcp("ai.internal", 11434)
    with pytest.raises(UnsafeTargetError):
        backend.connect_tcp("ai.internal", 11434)
    assert connections == ["192.168.1.2"]


def test_private_only_never_overrides_private_network_opt_in(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 11434)),
    ])
    with build_safe_http_client(timeout=httpx.Timeout(1), private_network_only=True) as client:
        with pytest.raises(UnsafeTargetError):
            client.get("http://ai.internal:11434/")


def test_default_backend_still_allows_public_answers_with_private_opt_in(monkeypatch):
    monkeypatch.setattr("app.services.safe_fetch.resolve_runtime_allowed_ips", lambda *_args, **_kwargs: ["8.8.8.8"])
    backend = _PinnedSyncBackend(allow_private_network=True)
    connections = []

    def connect(**kwargs):
        connections.append(kwargs["host"])
        return object()

    monkeypatch.setattr(backend._backend, "connect_tcp", connect)
    backend.connect_tcp("example.com", 443)
    assert connections == ["8.8.8.8"]
