from __future__ import annotations

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

        def post(self, url, *, headers, json):
            _ = (url, headers, json)
            raise error

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

        def post(self, url, *, headers, json):
            _ = (url, headers, json)
            return response

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
