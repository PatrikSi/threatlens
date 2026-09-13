import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services.ai_context_budget import AIContextBudgetError, estimate_tokens
from app.services.report_prompt_budget import estimate_message_tokens
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_provider_protocol import (
    build_provider_request_payload, provider_output_ceiling, provider_report_context_budget,
    validate_provider_request,
)
from app.services.ai_request_identity import ai_request_fingerprint


def _active(**changes):
    return SimpleNamespace(
        **({"provider_type": "openai_compatible", "base_url": "https://example.com",
            "model": "operator-selected-model", "temperature": 0.2, "max_completion_tokens": 5000,
            "report_context_window_tokens": 8192, "report_reserved_output_tokens": 1200,
            "report_context_safety_percent": 15} | changes)
    )


MESSAGES = [{"role": "user", "content": 'Return JSON: {"ok": true}'}]


def test_default_request_and_fingerprint_remain_byte_compatible():
    active = _active()
    assert build_provider_request_payload(active, MESSAGES, 5000) == {
        "model": active.model, "messages": MESSAGES, "temperature": 0.2,
        "max_tokens": 5000, "stream": False,
    }
    original = {
        "feature_type": "item_enrichment", "messages": MESSAGES, "item_id": None,
        "daily_brief_id": None, "report_id": None, "provider_type": active.provider_type,
        "base_url": active.base_url, "model": active.model, "temperature": 0.2,
        "max_tokens": 5000, "stream": False,
    }
    expected = hashlib.sha256(json.dumps(original, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    assert ai_request_fingerprint(
        active=active, feature_type="item_enrichment", messages=MESSAGES, item_id=None,
        daily_brief_id=None, report_id=None, requested_max_tokens=5000,
    ) == expected


@pytest.mark.parametrize("dialect, parameter", [("chat_completions", "max_tokens"), ("chat_completions_modern", "max_completion_tokens")])
@pytest.mark.parametrize("effort", [None, "none", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_explicit_dialects_and_optional_parameters(dialect, parameter, effort):
    payload = build_provider_request_payload(_active(
        request_dialect=dialect, temperature=None, reasoning_effort=effort,
        structured_output_mode="json_object",
    ), MESSAGES, 5000)
    assert payload[parameter] == 5000
    assert ("max_tokens" in payload) != ("max_completion_tokens" in payload)
    assert "temperature" not in payload
    assert payload["response_format"] == {"type": "json_object"}
    assert payload.get("reasoning_effort") == effort
    assert ("reasoning_effort" in payload) == (effort is not None)


@pytest.mark.parametrize("capabilities", [
    {"request_dialect": "native_gemini"}, {"reasoning_effort": "unexpected"},
    {"structured_output_mode": "unsafe"}, {"model_context_window_tokens": -1},
    {"model_max_output_tokens": -1}, {"temperature": float("nan")},
])
def test_invalid_persisted_capabilities_fail_before_transport(capabilities):
    with pytest.raises(AIIntegrationError) as caught:
        build_provider_request_payload(_active(**capabilities), MESSAGES, 5000)
    assert caught.value.provider_io_outcome == "not_sent"
    assert caught.value.retryable is False
    assert caught.value.failure_category.startswith("provider_")


def test_context_and_output_ceiling_use_actual_message_input_and_safety():
    active = _active(model_context_window_tokens=8192, model_max_output_tokens=6000)
    messages = [{"role": "user", "content": "x" * 9982}]
    ceiling = 8192 - 1229 - 384 - estimate_message_tokens(messages)
    assert provider_output_ceiling(active, messages) == ceiling
    validate_provider_request(active, messages, ceiling)
    with pytest.raises(AIIntegrationError, match=f"{ceiling:,}"):
        validate_provider_request(active, messages, ceiling + 1)
    assert provider_output_ceiling(active, MESSAGES) == 6000


def test_report_planning_uses_smaller_model_context_and_rejects_excess_output():
    active = _active(model_context_window_tokens=4096, model_max_output_tokens=2000)
    assert provider_report_context_budget(active).context_window_tokens == 4096
    active.report_reserved_output_tokens = 2001
    with pytest.raises(AIContextBudgetError, match="model limits"):
        provider_report_context_budget(active)


@pytest.mark.parametrize("content", ["\x00" * 2000, "\n\"" * 2000, "https://example.com/path?data=" + "x" * 2000])
def test_context_estimate_includes_serialized_escaping_and_message_framing(content):
    messages = [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": content}]
    serialized = json.dumps(messages, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert estimate_message_tokens(messages) == estimate_tokens(serialized)
    assert estimate_message_tokens(messages) > sum(estimate_tokens(message["content"]) for message in messages)
    active = _active(model_context_window_tokens=8192)
    with pytest.raises(AIIntegrationError, match="configured model limits"):
        validate_provider_request(active, messages, 7000)


def test_escaped_input_that_previously_fit_raw_content_fails_before_io():
    messages = [{"role": "user", "content": "\x00" * 1050}]
    active = _active(model_context_window_tokens=8192)
    raw_headroom = 8192 - 1229 - 384 - estimate_tokens(messages[0]["content"])
    assert raw_headroom > 5000
    with pytest.raises(AIIntegrationError) as caught:
        validate_provider_request(active, messages, 5000)
    assert caught.value.provider_io_outcome == "not_sent"
    assert caught.value.failure_category == "provider_context_budget_exceeded"
