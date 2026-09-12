"""Pure retry planning shared by durable provider attempt execution."""
from collections.abc import Callable
from dataclasses import dataclass

from app.services.ai_config import ActiveAISettings
from app.services.ai_provider_client import AIIntegrationError, AI_PROVIDER_IO_NOT_SENT


@dataclass(frozen=True, slots=True)
class _ProviderFailureRetryPlan:
    next_max_tokens: int
    should_retry: bool
    retry_delay_seconds: float | None
    payload: dict[str, object]


def _provider_attempt_limit(
    *,
    active: ActiveAISettings,
    max_provider_attempts: int | None,
) -> int:
    configured_limit = max(1, int(active.request_max_retries) + 1)
    if max_provider_attempts is None:
        return configured_limit
    if max_provider_attempts < 1:
        error = AIIntegrationError(
            "AI provider attempt budget is exhausted",
            retryable=False,
            provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
        )
        error.attempt_count = 0
        raise error
    return min(configured_limit, max_provider_attempts)


def _provider_failure_retry_plan(
    *,
    feature_type: str,
    error: AIIntegrationError,
    attempt: int,
    max_attempts: int,
    request_max_tokens: int,
    max_retry_completion_tokens: int | None,
    next_retry_max_completion_tokens: Callable[..., int],
    ai_error_is_retryable: Callable[[AIIntegrationError], bool],
    provider_retry_delay_seconds: Callable[..., float],
) -> _ProviderFailureRetryPlan:
    next_max_tokens = next_retry_max_completion_tokens(
        feature_type=feature_type,
        current=request_max_tokens,
        error=error,
        maximum=max_retry_completion_tokens,
    )
    truncation_has_headroom = not (
        error.retry_hint == "expand_completion_budget"
        and next_max_tokens <= request_max_tokens
    )
    should_retry = (
        attempt < max_attempts
        and ai_error_is_retryable(error)
        and truncation_has_headroom
    )
    retry_delay_seconds = (
        provider_retry_delay_seconds(attempt=attempt) if should_retry else None
    )
    payload = {
        **error.debug_payload(),
        "attempt": attempt,
        "max_attempts": max_attempts,
        "requested_max_tokens": request_max_tokens,
    }
    if next_max_tokens != request_max_tokens:
        payload["next_max_tokens"] = next_max_tokens
    if retry_delay_seconds is not None:
        payload["retry_delay_seconds"] = round(retry_delay_seconds, 3)
    return _ProviderFailureRetryPlan(
        next_max_tokens=next_max_tokens,
        should_retry=should_retry,
        retry_delay_seconds=retry_delay_seconds,
        payload=payload,
    )


