"""Model-independent request dialects and explicit provider context guardrails."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.core.ai_limits import AI_CONTEXT_PROTOCOL_OVERHEAD_TOKENS, MAX_AI_COMPLETION_TOKENS
from app.schemas.ai_provider_capabilities import AIProviderCapabilityFields, capability_values
from app.services.ai_context_budget import AIContextBudget, AIContextBudgetError, build_context_budget
from app.services.report_prompt_budget import estimate_message_tokens

if TYPE_CHECKING:
    from app.services.ai_config import ActiveAISettings

PROVIDER_CONTEXT_SAFETY_PERCENT = 15


def provider_capability_snapshot(active: object) -> dict:
    """Omit unchanged defaults to preserve existing queued-request fingerprints."""
    return {
        name: value for name, value in capability_values(active).items()
        if value != AIProviderCapabilityFields.model_fields[name].default
    }


def _validated_capabilities(active: object) -> AIProviderCapabilityFields:
    try:
        return AIProviderCapabilityFields.model_validate(capability_values(active))
    except ValidationError as exc:
        raise _request_error("Saved AI provider capabilities are invalid. Review the provider settings.", "provider_capabilities_invalid") from exc


def provider_output_ceiling(active: ActiveAISettings, messages: list[dict[str, str]] | None = None) -> int:
    capabilities = _validated_capabilities(active)
    ceiling = capabilities.model_max_output_tokens or MAX_AI_COMPLETION_TOKENS
    if capabilities.model_context_window_tokens is not None:
        context = capabilities.model_context_window_tokens
        input_tokens = estimate_message_tokens(messages) if messages is not None else 0
        headroom = context - math.ceil(context * PROVIDER_CONTEXT_SAFETY_PERCENT / 100)
        headroom -= AI_CONTEXT_PROTOCOL_OVERHEAD_TOKENS + input_tokens
        ceiling = min(ceiling, headroom)
    return min(MAX_AI_COMPLETION_TOKENS, ceiling)


def validate_provider_request(active: ActiveAISettings, messages: list[dict[str, str]], requested_tokens: int) -> None:
    if type(requested_tokens) is not int or not 128 <= requested_tokens <= MAX_AI_COMPLETION_TOKENS:
        raise _request_error(
            f"AI completion tokens must be a whole number between 128 and {MAX_AI_COMPLETION_TOKENS:,}. Review AI settings.",
            "provider_output_budget_invalid",
        )
    ceiling = provider_output_ceiling(active, messages)
    if requested_tokens > ceiling:
        input_tokens = estimate_message_tokens(messages)
        raise _request_error(
            f"AI request allows {requested_tokens:,} output tokens, but the configured model limits leave "
            f"{max(0, ceiling):,} after approximately {input_tokens:,} input tokens and safety reserves. "
            "Reduce the feature output budget or input, or correct the selected model's context/output limits. "
            "No provider request was sent; this context check does not truncate the prompt.",
            "provider_context_budget_exceeded",
        )


def build_provider_request_payload(active: ActiveAISettings, messages: list[dict[str, str]], requested_tokens: int) -> dict:
    validate_provider_request(active, messages, requested_tokens)
    capabilities = _validated_capabilities(active)
    token_parameter = "max_completion_tokens" if capabilities.request_dialect == "chat_completions_modern" else "max_tokens"
    payload = {"model": active.model, "messages": messages, token_parameter: requested_tokens, "stream": False}
    if active.temperature is not None:
        if (
            not isinstance(active.temperature, (int, float)) or isinstance(active.temperature, bool)
            or not math.isfinite(active.temperature) or not 0 <= active.temperature <= 2
        ):
            raise _request_error("AI temperature must be between 0 and 2, or omitted for models that do not support it.", "provider_temperature_invalid")
        payload["temperature"] = active.temperature
    if capabilities.reasoning_effort is not None:
        payload["reasoning_effort"] = capabilities.reasoning_effort
    if capabilities.structured_output_mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def provider_report_context_window(active: ActiveAISettings) -> int:
    model_context = _validated_capabilities(active).model_context_window_tokens
    return min(active.report_context_window_tokens, model_context) if model_context else active.report_context_window_tokens


def provider_report_safety_percent(active: ActiveAISettings) -> int:
    safety = active.report_context_safety_percent
    return max(safety, PROVIDER_CONTEXT_SAFETY_PERCENT) if getattr(active, "model_context_window_tokens", None) is not None else safety


def provider_report_context_budget(active: ActiveAISettings) -> AIContextBudget:
    if active.report_reserved_output_tokens > provider_output_ceiling(active):
        raise AIContextBudgetError(
            "The initial report completion budget exceeds the selected provider's model limits. "
            "Reduce the report budget or correct the provider's context/output limits."
        )
    return build_context_budget(
        context_window_tokens=provider_report_context_window(active),
        reserved_output_tokens=active.report_reserved_output_tokens, safety_percent=provider_report_safety_percent(active),
    )


def _request_error(message: str, code: str):
    # Import locally because the transport client consumes this pure request
    # builder; callers receive the same categorized, never-sent failure type.
    from app.services.ai_provider_client import AIIntegrationError

    return AIIntegrationError(message, retry_hint=code, failure_category=code, retryable=False, provider_io_outcome="not_sent")
