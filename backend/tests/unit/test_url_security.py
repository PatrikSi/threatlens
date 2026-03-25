import socket

import pytest

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


def test_runtime_fetchable_url_allows_private_dns_resolution_by_default(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.2.25", 0)),
        ],
    )

    assert is_runtime_fetchable_url("https://internal.example.com/feed.xml")


def test_runtime_fetchable_url_allows_local_host_suffix_by_default(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.25", 0)),
        ],
    )

    assert is_runtime_fetchable_url("https://corp.local/feed.xml")


def test_ensure_runtime_fetchable_url_can_still_raise_when_private_targets_are_explicitly_disabled(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ],
    )

    with pytest.raises(ValueError):
        ensure_runtime_fetchable_url("https://localhost/feed.xml", allow_private_network=False)
