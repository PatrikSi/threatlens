from __future__ import annotations

import gzip
import random
import tracemalloc
import zlib
from contextlib import closing
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import get_settings
from app.services import notification_webhook_http as webhooks, oidc_client
from app.services.bounded_response import read_response_prefix


class _Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    def close(self):
        self.closed = True


def _response(body: bytes, encoding: str, *, status_code: int = 200):
    encoded = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    stream = _Chunks([encoded, b"must not consume beyond the preview"])
    response = httpx.Response(
        status_code,
        headers={"content-encoding": encoding},
        stream=stream,
        request=httpx.Request("GET", "https://idp.example.com/metadata"),
    )
    return response, stream


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_oidc_rejects_compressed_oversize_without_allocating_full_body(monkeypatch, encoding):
    response, stream = _response(b"a" * 8_000_000, encoding)
    monkeypatch.setattr(get_settings(), "oidc_max_response_bytes", 65_536)

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def build_request(self, method, url, data=None):
            return httpx.Request(method, url, data=data)

        def send(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(oidc_client, "build_safe_http_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(oidc_client, "ensure_runtime_fetchable_url", lambda *_args, **_kwargs: None)
    tracemalloc.start()
    try:
        with pytest.raises(
            oidc_client.OIDCProtocolError,
            match="OIDC endpoint response exceeded the configured size limit",
        ):
            oidc_client._fetch_json("GET", "https://idp.example.com/metadata")
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # The old iter_bytes decoder allocated over 20 MB before enforcing the cap.
    assert peak_bytes < 1_000_000
    assert stream.closed
    assert stream.consumed == 1


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_webhook_compressed_preview_stops_before_allocating_or_reading_remainder(encoding):
    response, stream = _response(b"a" * 8_000_000, encoding)
    tracemalloc.start()
    try:
        with closing(response):
            assert webhooks.read_response_preview(response) == "a" * 4000
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak_bytes < 500_000
    assert stream.consumed == 1
    assert stream.closed


def test_compressed_prefix_preserves_all_bytes_when_encoded_input_exceeds_prefix_size():
    body = random.Random(0).randbytes(4000)
    encoded = gzip.compress(body)
    assert len(encoded) > len(body)
    response = httpx.Response(
        200, headers={"content-encoding": "gzip"},
        stream=_Chunks([encoded[:4000], encoded[4000:]]),
    )
    with closing(response):
        assert read_response_prefix(response, 4000) == body


def test_zero_length_preview_does_not_consume_the_stream():
    stream = _Chunks([b"unused"])
    with closing(httpx.Response(200, stream=stream)) as response:
        assert read_response_prefix(response, 0) == b""
    assert stream.consumed == 0


def test_webhook_preview_stops_when_operation_lease_is_lost():
    stream = _Chunks([b"part one", b"must not consume"])
    checks = []

    def revoked(timeout):
        checks.append(timeout)
        raise RuntimeError("operation lease lost")

    with closing(httpx.Response(200, stream=stream)) as response:
        with webhooks.notification_delivery_lease_heartbeat(revoked):
            with pytest.raises(RuntimeError, match="operation lease lost"):
                webhooks.read_response_preview(response, lease_timeout_seconds=10)
    assert len(checks) == 1
    assert checks[0] >= 10
    assert stream.consumed == 1
    assert stream.closed


@pytest.mark.parametrize("status_code", [200, 400, 503])
def test_webhook_invalid_compression_keeps_observed_final_status(monkeypatch, status_code):
    stream = _Chunks([b"invalid gzip"])
    response = httpx.Response(
        status_code, headers={"content-encoding": "gzip"}, stream=stream,
        request=httpx.Request("POST", "https://hooks.example.com/events"),
    )
    calls = []

    def send(*_args, **_kwargs):
        calls.append(True)
        webhooks._mark_notification_external_io_started()
        return response

    monkeypatch.setattr(webhooks, "build_safe_http_client", lambda **_kwargs: httpx.Client())
    monkeypatch.setattr(webhooks, "send_request_with_redirects", send)
    rendered = SimpleNamespace(
        timeout_seconds=10, url=str(response.request.url), method="POST",
        headers=[], query_params=[], body=None, headers_dict={},
        query_param_pairs=[], json_body=None, form_body=None, raw_body=None,
    )
    external_attempts = []
    with webhooks.notification_delivery_external_io_marker(lambda: external_attempts.append(True)):
        result = webhooks.send_rendered_notification_request(rendered)
    assert result.status_code == status_code
    assert result.success is (status_code == 200)
    assert "unavailable" in result.response_body_preview.lower()
    assert calls == [True]
    assert external_attempts == [True]
    assert stream.closed
