from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpcore
import httpx
import pytest

from app.core.config import get_settings
from app.schemas.ai import AISettingsUpdate
from app.services.ai_normalization import coerce_score
from app.services.ai_integration import _ai_error_is_retryable
from app.services.ai_provider_client import AIIntegrationError, call_ai_json
from app.services.safe_fetch import build_safe_http_client


def _active():
    return SimpleNamespace(
        ai_enabled=True, ai_configured=True, provider_type="openai_compatible",
        base_url="https://provider.example/v1", model="test", api_key=None,
        temperature=0.2, max_completion_tokens=500, request_timeout_seconds=5,
    )


def _call(payload, *, status=200):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        status, request=request, content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    ))
    return call_ai_json(
        _active(), messages=[{"role": "user", "content": "test"}],
        client_factory=lambda **_kwargs: httpx.Client(transport=transport),
    )


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -1, 2**31, True, 1.5, {}, "bad"])
def test_invalid_optional_usage_does_not_break_a_successful_response(value):
    completion = _call({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": value},
    })
    assert completion.payload == {"ok": True}
    assert completion.prompt_tokens == 100
    assert completion.completion_tokens == 50
    assert completion.total_tokens is None


@pytest.mark.parametrize("value", [0, 25, "25", 25.0, 2**31 - 1])
def test_valid_optional_usage_preserves_provider_counts(value):
    completion = _call({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"total_tokens": value},
    })
    assert completion.total_tokens == int(value)


@pytest.mark.parametrize("status, payload", [
    (200, {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}),
    (200, {"choices": [{"message": {"content": "not JSON"}, "finish_reason": "stop"}]}),
    (200, {"choices": []}),
    (429, {"error": {"message": "Rate limit exceeded for this API key"}}),
])
def test_failed_response_preserves_valid_usage_and_elapsed_time(status, payload):
    with pytest.raises(AIIntegrationError) as caught:
        _call({**payload, "usage": {
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
        }}, status=status)
    error = caught.value
    assert error.provider_io_outcome == "response_received"
    assert (error.prompt_tokens, error.completion_tokens, error.total_tokens) == (100, 50, 150)
    assert error.latency_ms is not None and error.latency_ms >= 0


@pytest.mark.parametrize("status, retryable", [(200, False), (401, False), (403, False), (429, True), (503, True)])
def test_http_status_overrides_auth_wording_but_keeps_nonstandard_auth_errors(status, retryable):
    with pytest.raises(AIIntegrationError) as caught:
        _call({"error": {"message": "Rate limit exceeded for this API key"}}, status=status)
    assert caught.value.retryable is retryable


@pytest.mark.parametrize("finish_reason, refusal, content, category", [
    ("stop", "untrusted refusal detail", None, "provider_refusal"),
    ("stop", "untrusted refusal detail", '{"ok":true}', "provider_refusal"),
    ("length", "untrusted refusal detail", None, "provider_refusal"),
    ("content_filter", None, None, "provider_content_filter"),
    ("content_filter", None, '{"ok":true}', "provider_content_filter"),
])
def test_explicit_provider_refusals_are_terminal_and_retain_usage(
    finish_reason, refusal, content, category,
):
    with pytest.raises(AIIntegrationError) as caught:
        _call({
            "choices": [{"message": {"content": content, "refusal": refusal}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        })
    error = caught.value
    assert error.provider_io_outcome == "response_received"
    assert error.retry_hint == category
    assert error.retryable is False
    assert _ai_error_is_retryable(error) is False
    assert error.total_tokens == 105
    assert error.latency_ms is not None
    assert "content policy" in str(error)
    assert "untrusted refusal detail" not in str(error)
    assert "untrusted refusal detail" not in json.dumps(error.debug_payload())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", float("nan"), float("inf"), True, 10**400])
def test_nonfinite_relevance_score_cannot_reach_json_responses(value):
    assert coerce_score(value) is None


@pytest.mark.parametrize("named", [False, True], ids=["legacy", "named"])
@pytest.mark.parametrize("resolved_ip, allowed", [("8.8.8.8", False), ("127.0.0.1", True)])
def test_plaintext_ai_endpoint_enforces_private_ip_after_dns_resolution(
    monkeypatch, named, resolved_ip, allowed,
):
    monkeypatch.setattr(get_settings(), "allow_private_network_ai", True)
    active = _active()
    active.base_url = "http://ai.internal:11434/v1"
    assert AISettingsUpdate(base_url=active.base_url).base_url == active.base_url
    if named:
        active.provider_id = uuid.uuid4()
        active.credential_origin = "http://ai.internal:11434"
    connected = []
    monkeypatch.setattr("app.services.safe_fetch.resolve_runtime_allowed_ips", lambda *_args, **_kwargs: [resolved_ip])

    def connect(_self, *, host, **_kwargs):
        connected.append(host)
        raise httpcore.ConnectError("synthetic connection failure; no socket opened")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    with pytest.raises(AIIntegrationError) as caught:
        call_ai_json(active, messages=[{"role": "user", "content": "test"}], client_factory=build_safe_http_client)
    assert caught.value.provider_io_outcome == "not_sent"
    assert connected == ([resolved_ip] if allowed else [])
