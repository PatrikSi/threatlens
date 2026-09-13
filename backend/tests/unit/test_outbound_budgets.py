from __future__ import annotations

import gzip
import socket
import threading
import time
from contextlib import closing, contextmanager

import httpx
import pytest

from app.services.bounded_response import ResponseBodyTooLarge, read_bounded_response
from app.services.outbound_deadline import (
    OutboundDeadlineExceeded, deadline_getaddrinfo, outbound_deadline,
)
from app.services.safe_fetch import build_safe_http_client, safe_stream_with_redirects


@pytest.fixture(autouse=True)
def runtime_events(monkeypatch):
    # Keep telemetry's Redis DNS and I/O outside these outbound budget tests.
    events = []
    monkeypatch.setattr(
        "app.services.outbound_deadline.record_runtime_event", events.append
    )
    return events


class _Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.consumed = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    def close(self):
        self.closed = True


@pytest.mark.parametrize("encoding", [None, "gzip", "deflate"])
def test_decoded_and_encoded_response_caps(encoding):
    import zlib

    raw = b"a" * 1_000_000
    encoded = gzip.compress(raw) if encoding == "gzip" else zlib.compress(raw) if encoding == "deflate" else raw
    stream = _Chunks([encoded, b"never consumed"])
    with closing(httpx.Response(503, headers={"content-encoding": encoding} if encoding else {}, stream=stream)) as response:
        with pytest.raises(ResponseBodyTooLarge):
            read_bounded_response(response, 1024)
    assert stream.consumed == 1
    assert stream.closed


def test_compressed_exact_cap_and_truncated_stream():
    raw = b"a" * 1024
    compressed = gzip.compress(raw)
    response = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=_Chunks([compressed[:10], compressed[10:]]))
    assert read_bounded_response(response, 1024) == raw
    response = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=_Chunks([compressed[:-2]]))
    with pytest.raises(httpx.DecodingError):
        read_bounded_response(response, 1024)


def test_dns_timeout_does_not_start_an_http_request(monkeypatch, runtime_events):
    started = threading.Event()
    finish = threading.Event()
    returned = threading.Event()

    def resolve(*_a, **_kw):
        started.set()
        finish.wait(2)
        returned.set()
        return []

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    begin = time.monotonic()
    try:
        with pytest.raises(OutboundDeadlineExceeded):
            with outbound_deadline(0.06):
                deadline_getaddrinfo("example.com")
        assert started.is_set()
        assert time.monotonic() - begin < 0.5
        assert not returned.is_set()
        assert runtime_events == ["outbound_deadline"]
    finally:
        finish.set()
        assert returned.wait(1)


@pytest.mark.parametrize("mode", ["headers", "body"])
def test_oidc_exchange_has_a_total_deadline(mode, monkeypatch):
    from app.core.config import get_settings
    from app.services import oidc_client

    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_OIDC", "true")
    monkeypatch.setenv("OIDC_TOTAL_TIMEOUT_SECONDS", "0.15")
    get_settings.cache_clear()
    with _slow_server(mode) as url:
        began = time.monotonic()
        with pytest.raises(oidc_client.OIDCProtocolError, match="timed out"):
            oidc_client._fetch_json("GET", url)
        assert time.monotonic() - began < 0.8


@pytest.mark.parametrize("mode", ["headers", "body"])
def test_webhook_deadline_preserves_observed_status_and_ambiguous_attempts(mode, monkeypatch):
    from types import SimpleNamespace
    from app.services import notification_webhook_http as webhooks

    monkeypatch.setattr(webhooks.settings, "allow_private_network_webhooks", True)
    calls = []
    with _slow_server(mode) as url, webhooks.notification_delivery_external_io_marker(lambda: calls.append(True)):
        rendered = SimpleNamespace(
            timeout_seconds=0.15, url=url, method="POST", headers=[], query_params=[], body=None,
            headers_dict={}, query_param_pairs=[], json_body=None, form_body=None, raw_body=None,
        )
        began = time.monotonic()
        if mode == "headers":
            with pytest.raises(webhooks.WebhookAmbiguousResponseError):
                webhooks.send_rendered_notification_request(rendered)
        else:
            result = webhooks.send_rendered_notification_request(rendered)
            assert result.status_code == 200
            assert result.success
            assert "unavailable" in result.response_body_preview.lower()
        assert time.monotonic() - began < 0.8
        assert calls == [True]


@contextmanager
def _slow_server(mode):
    stop = threading.Event()
    done = threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    port = listener.getsockname()[1]

    def serve():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(1)
                conn.recv(65536)
                if mode == "headers":
                    conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                elif mode == "body":
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
                while not stop.wait(0.02):
                    if mode != "tls":
                        conn.sendall(b"a")
        except (OSError, TimeoutError):
            pass
        finally:
            done.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"{'https' if mode == 'tls' else 'http'}://127.0.0.1:{port}/"
    finally:
        stop.set()
        listener.close()
        assert done.wait(3)
        thread.join()


@pytest.mark.parametrize("mode", ["headers", "body", "tls"])
def test_total_deadline_stops_trickles_and_releases_request_guard(mode):
    held = threading.Lock()

    @contextmanager
    def guard(_url):
        with held:
            yield

    with _slow_server(mode) as url:
        began = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            with outbound_deadline(0.15), build_safe_http_client(
                timeout=httpx.Timeout(1), allow_private_network=True,
            ) as client:
                response = safe_stream_with_redirects(
                    client, "GET", url, allow_private_network=True, request_context=guard,
                )
                try:
                    read_bounded_response(response, 10_000)
                finally:
                    response.close()
        assert time.monotonic() - began < 0.8
        assert held.acquire(blocking=False)
        held.release()


def test_redirects_share_one_total_budget_and_close_each_guard():
    held = []

    @contextmanager
    def guard(url):
        held.append(url)
        try:
            yield
        finally:
            held.remove(url)

    requests = []

    def respond(request):
        requests.append(request)
        time.sleep(0.04)
        return httpx.Response(302, headers={"location": "/next"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(OutboundDeadlineExceeded):
            with outbound_deadline(0.06):
                safe_stream_with_redirects(client, "GET", "http://127.0.0.1/", allow_private_network=True, request_context=guard)
    assert len(requests) == 2
    assert held == []


def test_completed_body_does_not_recheck_a_released_domain_guard():
    held = False

    @contextmanager
    def guard(_url):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    def check():
        assert held

    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=_Chunks([b"body"])))) as client:
        response = safe_stream_with_redirects(client, "GET", "http://127.0.0.1/", allow_private_network=True, request_context=guard)
        assert read_bounded_response(response, 100, check=check) == b"body"
    assert not held


def test_saturated_dns_capacity_is_bounded(monkeypatch, runtime_events):
    import app.services.outbound_deadline as budgets

    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(budgets, "_resolver_slots", slots)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: pytest.fail("resolver must not start"))
    began = time.monotonic()
    with pytest.raises(OutboundDeadlineExceeded, match="capacity"):
        with outbound_deadline(0.03):
            deadline_getaddrinfo("example.com")
    assert time.monotonic() - began < 0.5
    assert runtime_events == ["outbound_deadline"]


def test_request_write_stops_when_peer_never_reads():
    from httpcore._backends.sync import SyncStream
    from app.services.safe_fetch import _DeadlineSyncStream
    import httpcore

    sender, receiver = socket.socketpair()
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    began = time.monotonic()
    try:
        with pytest.raises(httpcore.WriteTimeout):
            with outbound_deadline(0.05):
                _DeadlineSyncStream(SyncStream(sender)).write(b"a" * 1_000_000, timeout=1)
        assert time.monotonic() - began < 0.5
    finally:
        sender.close()
        receiver.close()


def test_domain_slot_wait_obeys_total_budget(monkeypatch):
    from app.tasks import feed_task_coordination as coordination
    from types import SimpleNamespace

    monkeypatch.setattr(coordination, "redis_client", SimpleNamespace(set=lambda *_a, **_kw: False))
    monkeypatch.setattr(coordination, "_try_take_stale_lease", lambda *_a, **_kw: False)
    began = time.monotonic()
    with pytest.raises(OutboundDeadlineExceeded):
        with outbound_deadline(0.04), coordination.domain_slot("example.com"):
            pytest.fail("unavailable slot must not be acquired")
    assert time.monotonic() - began < 0.5


def test_domain_slot_acquired_after_deadline_is_released(monkeypatch):
    from app.tasks import feed_task_coordination as coordination
    from types import SimpleNamespace

    released = []

    def acquire(*_a, **_kw):
        time.sleep(0.03)
        return True

    monkeypatch.setattr(coordination, "redis_client", SimpleNamespace(set=acquire))
    monkeypatch.setattr(coordination, "_best_effort_release_lease", lambda *args: released.append(args))
    with pytest.raises(OutboundDeadlineExceeded):
        with outbound_deadline(0.02), coordination.domain_slot("example.com"):
            pytest.fail("expired slot must not enter HTTP I/O")
    assert len(released) == 1


def test_expiry_before_tls_closes_the_connected_socket():
    from app.services.safe_fetch import _DeadlineSyncStream
    from httpcore._backends.sync import SyncStream

    sender, receiver = socket.socketpair()
    try:
        stream = _DeadlineSyncStream(SyncStream(sender))
        with pytest.raises(OutboundDeadlineExceeded):
            with outbound_deadline(0.01):
                time.sleep(0.02)
                stream.start_tls(None, "example.com", timeout=1)
        assert sender.fileno() == -1
    finally:
        sender.close()
        receiver.close()
