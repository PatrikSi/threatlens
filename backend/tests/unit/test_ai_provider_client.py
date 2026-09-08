from __future__ import annotations

from contextlib import contextmanager

from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from app.services.ai_config import ActiveAISettings
from app.services.ai_provider_client import (
    AI_PROVIDER_IO_AMBIGUOUS,
    AI_PROVIDER_IO_NOT_SENT,
    AI_PROVIDER_IO_RESPONSE_RECEIVED,
    AIIntegrationError,
    call_ai_json,
)
from app.services.safe_fetch import SafeFetchError


def _active_settings() -> ActiveAISettings:
    return cast(
        ActiveAISettings,
        SimpleNamespace(
            ai_enabled=True,
            ai_configured=True,
            provider_type="openai_compatible",
            base_url="http://localhost:11434/v1",
            model="local-threat-model",
            api_key=None,
            temperature=0.2,
            max_completion_tokens=500,
            request_timeout_seconds=30,
        ),
    )


def _raising_client_factory(error: Exception):
    class _RaisingClient:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        @contextmanager
        def stream(self, method, url, *, headers, json):
            _ = (url, headers, json)
            raise error
            yield  # pragma: no cover

    return _RaisingClient


@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("connection timed out"),
        httpx.PoolTimeout("connection pool exhausted"),
        httpx.InvalidURL("invalid URL"),
        httpx.UnsupportedProtocol("unsupported protocol"),
        SafeFetchError("target rejected before connection"),
    ],
    ids=[
        "connect-error",
        "connect-timeout",
        "pool-timeout",
        "invalid-url",
        "unsupported-protocol",
        "safe-fetch-guard",
    ],
)
def test_call_ai_json_classifies_pre_send_failures_as_not_sent(
    transport_error: Exception,
) -> None:
    with pytest.raises(AIIntegrationError) as exc_info:
        call_ai_json(
            _active_settings(),
            messages=[{"role": "user", "content": "{}"}],
            client_factory=_raising_client_factory(transport_error),
        )

    error = exc_info.value
    assert error.provider_io_outcome == AI_PROVIDER_IO_NOT_SENT
    assert error.retryable is True
    assert error.debug_payload()["provider_io_outcome"] == AI_PROVIDER_IO_NOT_SENT


def test_call_ai_json_classifies_http_status_as_response_received() -> None:
    request = httpx.Request(
        "POST", "http://localhost:11434/v1/chat/completions"
    )
    response = httpx.Response(
        503,
        request=request,
        json={"error": {"message": "provider overloaded"}},
    )

    class _StatusClient:
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        @contextmanager
        def stream(self, method, url, *, headers, json):
            _ = (url, headers, json)
            yield response

    with pytest.raises(AIIntegrationError) as exc_info:
        call_ai_json(
            _active_settings(),
            messages=[{"role": "user", "content": "{}"}],
            client_factory=_StatusClient,
        )

    error = exc_info.value
    assert error.provider_io_outcome == AI_PROVIDER_IO_RESPONSE_RECEIVED
    assert error.retryable is True
    assert error.status_code == 503
    assert error.debug_payload()["provider_io_outcome"] == AI_PROVIDER_IO_RESPONSE_RECEIVED


@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ReadTimeout("response timed out"),
        httpx.WriteTimeout("request write timed out"),
        httpx.RemoteProtocolError("connection closed without a response"),
        ValueError("client failed after entering the request path"),
    ],
    ids=["read-timeout", "write-timeout", "protocol-error", "value-error"],
)
def test_call_ai_json_classifies_uncertain_transport_failures_as_ambiguous(
    transport_error: Exception,
) -> None:
    with pytest.raises(AIIntegrationError) as exc_info:
        call_ai_json(
            _active_settings(),
            messages=[{"role": "user", "content": "{}"}],
            client_factory=_raising_client_factory(transport_error),
        )

    error = exc_info.value
    assert error.provider_io_outcome == AI_PROVIDER_IO_AMBIGUOUS
    assert error.retryable is False
    assert error.debug_payload()["provider_io_outcome"] == AI_PROVIDER_IO_AMBIGUOUS


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("compressed", [False, True])
def test_provider_caps_decoded_success_and_error_responses(monkeypatch, status, compressed):
    import gzip
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_response_max_bytes", 1024)
    consumed = []
    closed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            consumed.append(1)
            raw = b"a" * 100_000
            yield gzip.compress(raw) if compressed else raw
            consumed.append(2)
            yield b"not reached"

        def close(self):
            closed.append(True)

    def factory(**_kwargs):
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            status, stream=Stream(), headers={"content-encoding": "gzip"} if compressed else {},
        )))

    with pytest.raises(AIIntegrationError, match="byte cap") as caught:
        call_ai_json(_active_settings(), messages=[{"role": "user", "content": "{}"}], client_factory=factory)
    assert caught.value.retryable is False
    assert caught.value.provider_io_outcome == AI_PROVIDER_IO_RESPONSE_RECEIVED
    assert caught.value.status_code == status
    assert caught.value.response_body is None
    assert consumed == [1]
    assert closed == [True]


def test_provider_total_timeout_is_ambiguous_and_releases_synchronous_fence():
    import threading
    import time
    from app.services.safe_fetch import build_safe_http_client
    from tests.unit.test_outbound_budgets import _slow_server

    active = _active_settings()
    active.request_timeout_seconds = 0.15
    fence = threading.Lock()
    with _slow_server("body") as url:
        active.base_url = url

        def factory(**kwargs):
            kwargs["allow_private_network"] = True
            return build_safe_http_client(**kwargs)

        began = time.monotonic()
        with pytest.raises(AIIntegrationError) as caught:
            with fence:
                call_ai_json(active, messages=[{"role": "user", "content": "{}"}], client_factory=factory)
        assert caught.value.provider_io_outcome == AI_PROVIDER_IO_AMBIGUOUS
        assert not caught.value.retryable
        assert time.monotonic() - began < 0.8
        assert fence.acquire(blocking=False)
        fence.release()
