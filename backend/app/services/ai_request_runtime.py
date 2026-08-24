from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import replace

from sqlalchemy.orm import Session

from app.services.ai_config import ActiveAISettings
from app.services.ai_ops import record_ai_task_event
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError


class AITaskRunStoppedError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.code = "canceled" if reason == "canceled" else "task_stopped"


def run_ai_json_request(
    db: Session,
    active: ActiveAISettings,
    *,
    feature_type: str,
    messages: list[dict[str, str]],
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    task_run_id: uuid.UUID | None,
    max_completion_tokens: int | None,
    max_retry_completion_tokens: int | None,
    max_provider_attempts: int | None,
    execution_checkpoint: Callable[[], None] | None,
    execution_commit: Callable[[], None] | None,
    report_feature_type: str,
    call_ai_json: Callable[..., AICompletionResult],
    record_task_run_stop_observed: Callable[..., str | None],
    record_usage_event: Callable[..., None],
    build_provider_exchange_payload: Callable[..., dict],
    provider_retry_delay_seconds: Callable[..., float],
    ai_error_is_retryable: Callable[[AIIntegrationError], bool],
    next_retry_max_completion_tokens: Callable[..., int],
    sleep: Callable[[float], None] = time.sleep,
) -> AICompletionResult:
    max_attempts = max(1, active.request_max_retries + 1)
    if max_provider_attempts is not None:
        if max_provider_attempts < 1:
            raise AIIntegrationError(
                "AI provider attempt budget is exhausted", retryable=False
            )
        max_attempts = min(max_attempts, max_provider_attempts)
    last_error: AIIntegrationError | None = None
    request_max_tokens = max_completion_tokens or active.max_completion_tokens
    _commit_ai_progress(db, execution_commit)

    for attempt in range(1, max_attempts + 1):
        if execution_checkpoint is not None:
            execution_checkpoint()
        if attempt > 1:
            stop_reason = record_task_run_stop_observed(
                db,
                task_run_id=task_run_id,
                stage="before_provider_retry",
            )
            if stop_reason is not None:
                _commit_ai_progress(db, execution_commit)
                raise AITaskRunStoppedError(stop_reason)
            _commit_ai_progress(db, execution_commit)
        try:
            call_kwargs: dict[str, object] = {"messages": messages}
            if request_max_tokens != active.max_completion_tokens:
                call_kwargs["max_completion_tokens"] = request_max_tokens
            completion = call_ai_json(active, **call_kwargs)
        except AIIntegrationError as exc:
            if execution_checkpoint is not None:
                execution_checkpoint()
            last_error = exc
            next_request_max_tokens = next_retry_max_completion_tokens(
                feature_type=feature_type,
                current=request_max_tokens,
                error=exc,
                maximum=max_retry_completion_tokens,
            )
            report_truncation_has_headroom = not (
                feature_type == report_feature_type
                and exc.retry_hint == "expand_completion_budget"
                and next_request_max_tokens <= request_max_tokens
            )
            should_retry = (
                attempt < max_attempts
                and ai_error_is_retryable(exc)
                and report_truncation_has_headroom
            )
            retry_delay_seconds = (
                provider_retry_delay_seconds(attempt=attempt) if should_retry else None
            )
            payload = {
                **exc.debug_payload(),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "requested_max_tokens": request_max_tokens,
            }
            if next_request_max_tokens != request_max_tokens:
                payload["next_max_tokens"] = next_request_max_tokens
            if retry_delay_seconds is not None:
                payload["retry_delay_seconds"] = round(retry_delay_seconds, 3)
            if task_run_id is not None:
                record_ai_task_event(
                    db,
                    run_id=task_run_id,
                    event_type=(
                        "provider_exchange_retry"
                        if should_retry
                        else "provider_exchange_failed"
                    ),
                    message=str(exc),
                    payload=payload,
                )
            record_usage_event(
                db,
                feature_type=feature_type,
                success=False,
                provider=active.provider_type,
                model=active.model,
                item_id=item_id,
                daily_brief_id=daily_brief_id,
                report_id=report_id,
                error=str(exc),
            )
            _commit_ai_progress(db, execution_commit)
            if should_retry:
                request_max_tokens = next_request_max_tokens
                if retry_delay_seconds is not None and retry_delay_seconds > 0:
                    sleep(retry_delay_seconds)
                continue
            exc.attempt_count = attempt
            raise

        if execution_checkpoint is not None:
            execution_checkpoint()
        if task_run_id is not None:
            provider_exchange_payload = {
                **build_provider_exchange_payload(
                    request_url=completion.request_url,
                    request_payload=completion.request_payload,
                    response_body=completion.response_body,
                    response_json=completion.response_json,
                    status_code=completion.status_code,
                    finish_reason=completion.finish_reason,
                ),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "requested_max_tokens": request_max_tokens,
            }
            record_ai_task_event(
                db,
                run_id=task_run_id,
                event_type="provider_exchange",
                payload=provider_exchange_payload,
            )

        record_usage_event(
            db,
            feature_type=feature_type,
            success=True,
            provider=completion.provider,
            model=completion.model,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            report_id=report_id,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            latency_ms=completion.latency_ms,
        )
        _commit_ai_progress(db, execution_commit)
        return replace(completion, attempt_count=attempt)

    if last_error is None:
        raise AIIntegrationError("AI request failed unexpectedly")
    last_error.attempt_count = max_attempts
    raise last_error


def _commit_ai_progress(
    db: Session,
    execution_commit: Callable[[], None] | None,
) -> None:
    if execution_commit is not None:
        execution_commit()
        return
    db.commit()


__all__ = ["AITaskRunStoppedError", "run_ai_json_request"]
