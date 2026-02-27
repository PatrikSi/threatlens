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


def test_runtime_fetchable_url_blocks_private_dns_resolution(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.2.25", 0)),
        ],
    )

    assert not is_runtime_fetchable_url("https://internal.example.com/feed.xml")


def test_runtime_fetchable_url_blocks_local_host_suffix():
    assert not is_runtime_fetchable_url("https://corp.local/feed.xml")


def test_ensure_runtime_fetchable_url_raises_for_unsafe_target(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ],
    )

    with pytest.raises(ValueError):
        ensure_runtime_fetchable_url("https://localhost/feed.xml")
