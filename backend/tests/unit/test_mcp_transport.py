import copy
import time
from types import SimpleNamespace

import anyio
import pytest

from app.services import mcp_transport


@pytest.fixture
def transport_settings(monkeypatch):
    settings = SimpleNamespace(
        mcp_enabled=True,
        mcp_allowed_origins=["https://client.example"],
        mcp_max_concurrent_requests=2,
        database_pool_size=8,
        database_max_overflow=2,
        mcp_request_timeout_seconds=0.2,
        mcp_request_max_bytes=64,
    )
    monkeypatch.setattr(mcp_transport, "get_settings", lambda: settings)
    return settings


def _scope(*, headers=(), method="POST"):
    return {
        "type": "http", "method": method, "path": "/v1/mcp", "query_string": b"",
        "headers": [(b"content-type", b"application/json"), *headers], "state": {},
    }


async def _run(app, scope, *, chunks=(), send_hook=None):
    messages = []
    inputs = iter(chunks)

    async def receive():
        return next(inputs, {"type": "http.disconnect"})

    async def send(message):
        messages.append(copy.deepcopy(message))
        if send_hook is not None:
            await send_hook(message)

    await app(scope, receive, send)
    return messages


async def _json_response(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


@pytest.mark.parametrize("value,expected", [
    ("application/json;q=0.5", True),
    ("application/json; q=0", False),
    ("application/json;q=0, */*;q=1", False),
    ("application/*;q=0.25", True),
    ("text/event-stream", False),
    ("application/json;q=NaN", False),
    ("application/json;q=2", False),
    ("application/json;q=0;q=1", False),
])
def test_json_accept_quality_and_specificity(value, expected):
    assert mcp_transport._accepts_json(value) is expected


def test_cors_keeps_the_explicit_mcp_origin_policy(transport_settings):
    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"access-control-allow-origin", b"*"),
            (b"access-control-allow-credentials", b"true"),
        ]})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = mcp_transport.MCPTransportMiddleware(application)
    messages = anyio.run(_run, middleware, _scope(headers=[(b"origin", b"https://client.example")]))
    headers = dict(messages[0]["headers"])
    assert headers[b"access-control-allow-origin"] == b"https://client.example"
    assert b"access-control-allow-credentials" not in headers


def test_unknown_origin_is_denied_before_application_access(transport_settings):
    called = False

    async def application(scope, receive, send):
        nonlocal called
        called = True

    middleware = mcp_transport.MCPTransportMiddleware(application)
    messages = anyio.run(_run, middleware, _scope(headers=[(b"origin", b"https://other.example")]))
    assert messages[0]["status"] == 403
    assert called is False


@pytest.mark.parametrize("requested,status", [
    (b"authorization, mcp-method", 204),
    (b"authorization, cookie", 403),
])
def test_preflight_only_allows_the_mcp_header_set(transport_settings, requested, status):
    middleware = mcp_transport.MCPTransportMiddleware(_json_response)
    messages = anyio.run(_run, middleware, _scope(method="OPTIONS", headers=[
        (b"origin", b"https://client.example"),
        (b"access-control-request-method", b"POST"),
        (b"access-control-request-headers", requested),
    ]))
    assert messages[0]["status"] == status


def test_chunked_request_cannot_evade_the_body_limit(transport_settings):
    transport_settings.mcp_request_max_bytes = 8
    cleanups = []

    async def application(scope, receive, send):
        scope["state"]["mcp_cleanup"] = lambda: cleanups.append("closed")
        while True:
            chunk = await receive()
            if not chunk.get("more_body"):
                break
        await _json_response(scope, receive, send)

    async def exercise():
        return await _run(mcp_transport.MCPTransportMiddleware(application), _scope(), chunks=[
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"67890", "more_body": False},
        ])

    messages = anyio.run(exercise)
    assert messages[0]["status"] == 413
    assert cleanups == ["closed"]


def test_response_fences_live_through_the_final_socket_send(transport_settings):
    cleanups = []

    async def application(scope, receive, send):
        scope["state"]["mcp_cleanup"] = lambda: cleanups.append("closed")
        await _json_response(scope, receive, send)

    async def socket_send(message):
        assert cleanups == []
        await anyio.sleep(0.01)
        assert cleanups == []

    async def exercise():
        return await _run(
            mcp_transport.MCPTransportMiddleware(application), _scope(), send_hook=socket_send
        )

    messages = anyio.run(exercise)
    assert messages[-1]["body"] == b"{}"
    assert cleanups == ["closed"]


def test_backpressure_cannot_retain_authorization_fences_forever(transport_settings):
    transport_settings.mcp_request_timeout_seconds = 0.03
    cleanups = []

    async def application(scope, receive, send):
        scope["state"]["mcp_cleanup"] = lambda: cleanups.append("closed")
        await _json_response(scope, receive, send)

    async def socket_send(message):
        if message["type"] == "http.response.body":
            await anyio.sleep_forever()

    middleware = mcp_transport.MCPTransportMiddleware(application)

    async def exercise():
        with pytest.raises(TimeoutError, match="transfer deadline"):
            await _run(middleware, _scope(), send_hook=socket_send)

    anyio.run(exercise)
    assert cleanups == ["closed"]
    assert middleware._active == 0


def test_disconnect_releases_fences_and_admission(transport_settings):
    cleanups = []

    async def application(scope, receive, send):
        scope["state"]["mcp_cleanup"] = lambda: cleanups.append("closed")
        await _json_response(scope, receive, send)

    async def disconnected_send(message):
        raise OSError("client disconnected")

    middleware = mcp_transport.MCPTransportMiddleware(application)

    async def exercise():
        with pytest.raises(OSError, match="disconnected"):
            await _run(middleware, _scope(), send_hook=disconnected_send)

    anyio.run(exercise)
    assert cleanups == ["closed"]
    assert middleware._active == 0


def test_late_authorization_expiry_prevents_sensitive_publication(transport_settings):
    cleanups = []

    async def application(scope, receive, send):
        scope["state"]["mcp_cleanup"] = lambda: cleanups.append("closed")
        scope["state"]["mcp_deadline"] = time.monotonic() - 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"sensitive report"})

    messages = anyio.run(_run, mcp_transport.MCPTransportMiddleware(application), _scope())
    assert messages[0]["status"] == 504
    assert all(b"sensitive report" not in message.get("body", b"") for message in messages)
    assert cleanups == ["closed"]


def test_credential_expiry_tightens_an_in_progress_transfer(transport_settings):
    transport_settings.mcp_request_timeout_seconds = 5
    cleanups = []

    async def application(scope, receive, send):
        scope["state"]["mcp_cleanup"] = lambda: cleanups.append("closed")
        scope["state"]["mcp_deadline"] = time.monotonic() + 0.02
        await _json_response(scope, receive, send)

    async def slow_socket(message):
        if message["type"] == "http.response.body":
            await anyio.sleep_forever()

    async def exercise():
        # The enclosing bound detects accidentally keeping the original 5s timer.
        with anyio.fail_after(0.5):
            with pytest.raises(TimeoutError, match="transfer deadline"):
                await _run(mcp_transport.MCPTransportMiddleware(application), _scope(), send_hook=slow_socket)

    anyio.run(exercise)
    assert cleanups == ["closed"]


def test_admission_reserves_an_audit_connection_for_each_read(transport_settings):
    transport_settings.database_pool_size = 2
    transport_settings.database_max_overflow = 0
    transport_settings.mcp_max_concurrent_requests = 4
    entered = anyio.Event()
    release = anyio.Event()

    async def application(scope, receive, send):
        entered.set()
        await release.wait()
        await _json_response(scope, receive, send)

    middleware = mcp_transport.MCPTransportMiddleware(application)

    async def exercise():
        async with anyio.create_task_group() as group:
            group.start_soon(_run, middleware, _scope())
            await entered.wait()
            messages = await _run(middleware, _scope())
            assert messages[0]["status"] == 429
            release.set()

    anyio.run(exercise)
    assert middleware._active == 0
