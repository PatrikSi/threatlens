"""High-output requests retain finite budgets and recover useful truncations."""

from types import SimpleNamespace

import httpx
import pytest

from app.core.ai_limits import MAX_AI_COMPLETION_TOKENS
from app.schemas.ai import AISettingsUpdate
from app.schemas.ai_providers import AIProviderCreate
from app.services.ai_context_budget import build_context_budget
from app.services.ai_integration import (
    FEATURE_DAILY_BRIEF,
    FEATURE_ITEM_ENRICHMENT,
    FEATURE_REPORT,
    _next_retry_max_completion_tokens,
)
from app.services.ai_provider_client import AIIntegrationError, call_ai_json
from app.services.ai_request_runtime import _provider_failure_retry_plan


@pytest.mark.parametrize(
    "tokens", [128, 8193, 32_768, 65_536, MAX_AI_COMPLETION_TOKENS]
)
def test_legacy_and_named_settings_accept_larger_output_limits(tokens):
    assert (
        AISettingsUpdate(max_completion_tokens=tokens).max_completion_tokens == tokens
    )
    assert (
        AIProviderCreate(
            name="Model",
            base_url="https://provider.example/v1",
            model="model",
            max_completion_tokens=tokens,
        ).max_completion_tokens
        == tokens
    )


@pytest.mark.parametrize("tokens", [127, MAX_AI_COMPLETION_TOKENS + 1, 16384.5])
def test_completion_limits_remain_bounded_whole_numbers(tokens):
    with pytest.raises(ValueError):
        AISettingsUpdate(max_completion_tokens=tokens)
    with pytest.raises(ValueError):
        AIProviderCreate(
            name="Model",
            base_url="https://provider.example/v1",
            model="model",
            max_completion_tokens=tokens,
        )


def test_larger_report_budget_does_not_change_default_completion_tokens():
    settings = AISettingsUpdate(
        report_context_window_tokens=262_144,
        report_reserved_output_tokens=MAX_AI_COMPLETION_TOKENS,
    )
    assert settings.max_completion_tokens == 5000
    assert settings.report_reserved_output_tokens == MAX_AI_COMPLETION_TOKENS
    with pytest.raises(ValueError):
        AISettingsUpdate(
            report_context_window_tokens=262_144,
            report_reserved_output_tokens=MAX_AI_COMPLETION_TOKENS + 1,
        )


def test_report_context_validation_matches_runtime_at_exact_input_boundary():
    # ceil(2049 * 5 / 100) = 103, plus the 384-token protocol reserve.
    settings = AISettingsUpdate(
        report_context_window_tokens=2049,
        report_reserved_output_tokens=1050,
        report_context_safety_percent=5,
        report_source_token_cap=128,
    )
    budget = build_context_budget(
        context_window_tokens=settings.report_context_window_tokens,
        reserved_output_tokens=settings.report_reserved_output_tokens,
        safety_percent=settings.report_context_safety_percent,
    )
    assert budget.usable_input_tokens == 512
    with pytest.raises(ValueError, match="leave at least 512"):
        AISettingsUpdate(
            report_context_window_tokens=2049,
            report_reserved_output_tokens=1051,
            report_context_safety_percent=5,
            report_source_token_cap=128,
        )
    with pytest.raises(ValueError, match="source token cap"):
        AISettingsUpdate(
            report_context_window_tokens=2049,
            report_reserved_output_tokens=1050,
            report_context_safety_percent=5,
            report_source_token_cap=512,
        )


@pytest.mark.parametrize("feature", [FEATURE_DAILY_BRIEF, FEATURE_ITEM_ENRICHMENT])
@pytest.mark.parametrize(
    "current", [700, 5000, 16_384, 65_536, MAX_AI_COMPLETION_TOKENS]
)
def test_truncation_retry_expands_without_shrinking_large_requests(feature, current):
    error = AIIntegrationError("truncated", retry_hint="expand_completion_budget")
    result = _next_retry_max_completion_tokens(
        feature_type=feature, current=current, error=error
    )
    assert current <= result <= MAX_AI_COMPLETION_TOKENS
    if current < MAX_AI_COMPLETION_TOKENS:
        assert result > current


@pytest.mark.parametrize(
    "feature", [FEATURE_REPORT, FEATURE_DAILY_BRIEF, FEATURE_ITEM_ENRICHMENT]
)
def test_truncation_at_application_ceiling_does_not_repeat_same_request(feature):
    error = AIIntegrationError(
        "truncated", retry_hint="expand_completion_budget", retryable=True
    )
    plan = _provider_failure_retry_plan(
        feature_type=feature,
        error=error,
        attempt=1,
        max_attempts=4,
        request_max_tokens=MAX_AI_COMPLETION_TOKENS,
        max_retry_completion_tokens=MAX_AI_COMPLETION_TOKENS,
        next_retry_max_completion_tokens=_next_retry_max_completion_tokens,
        ai_error_is_retryable=lambda error: error.retryable,
        provider_retry_delay_seconds=lambda **_: pytest.fail("Must not schedule retry"),
    )
    assert plan.should_retry is False
    assert plan.next_max_tokens == MAX_AI_COMPLETION_TOKENS


@pytest.mark.parametrize("content", [None, "", '{"summary":', '{"summary":"partial"}'])
def test_truncated_and_reasoning_only_provider_responses_get_budget_recovery_hint(
    content,
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": content}, "finish_reason": "length"}
                ]
            },
        )

    active = SimpleNamespace(
        ai_enabled=True,
        ai_configured=True,
        base_url="https://provider.example/v1",
        model="model",
        api_key=None,
        temperature=0.2,
        request_timeout_seconds=30,
        max_completion_tokens=32_768,
    )
    with pytest.raises(AIIntegrationError, match="32,768 completion tokens") as caught:
        call_ai_json(
            active,
            messages=[{"role": "user", "content": "Return JSON"}],
            client_factory=lambda **_: httpx.Client(
                transport=httpx.MockTransport(respond)
            ),
        )
    assert len(requests) == 1
    assert caught.value.request_payload["max_tokens"] == 32_768
    assert caught.value.retry_hint == "expand_completion_budget"
    assert caught.value.provider_io_outcome == "response_received"
    assert "Initial report completion tokens" in str(caught.value)


@pytest.mark.parametrize("tokens", [127, MAX_AI_COMPLETION_TOKENS + 1, 128.5, True])
@pytest.mark.parametrize("override", [False, True])
def test_invalid_persisted_or_override_budget_fails_before_provider_io(
    tokens, override
):
    active = SimpleNamespace(
        ai_enabled=True,
        ai_configured=True,
        base_url="https://provider.example/v1",
        model="model",
        api_key=None,
        max_completion_tokens=5000 if override else tokens,
    )
    with pytest.raises(AIIntegrationError, match="completion tokens must be") as caught:
        call_ai_json(
            active,
            messages=[],
            max_completion_tokens=tokens if override else None,
            client_factory=lambda **_: pytest.fail("Must not create provider client"),
        )
    assert caught.value.provider_io_outcome == "not_sent"
    assert caught.value.retryable is False
