from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.services.ai_config import ActiveAISettings
from app.services.ai_egress_data_policy import (
    AIEgressAuthorization,
    AIEgressPolicyError,
    mark_ai_egress_provider_io_state,
)
from app.services.ai_ops import record_ai_task_event
from app.services.ai_provider_attempts import (
    AIProviderAttemptReservation,
    AIProviderAttemptStateError,
    AIProviderTaskBindingError,
    lock_ai_provider_attempt_for_io,
    reserve_ai_provider_attempt,
    settle_ai_provider_attempt,
    void_ai_provider_attempt_reservation,
)
from app.services.ai_provider_client import (
    AI_PROVIDER_IO_AMBIGUOUS,
    AI_PROVIDER_IO_NOT_SENT,
    AICompletionResult,
    AIIntegrationError,
)


logger = logging.getLogger(__name__)


class AITaskRunStoppedError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.code = "canceled" if reason == "canceled" else "task_stopped"


class AIProviderAttemptAmbiguousError(AIIntegrationError):
    """A provider may have processed the request, so automatic replay is unsafe."""


class AIProviderReservationUnsettledError(AIIntegrationError):
    """A durable reservation could not be settled before any provider I/O."""


class AIProviderAttemptReplayBlockedError(AIIntegrationError):
    """A durable receipt prevents another automatic provider call."""


class _AIProviderAuthorizationRefreshRequired(RuntimeError):
    """The durable reservation snapshot changed before provider I/O."""


@dataclass(frozen=True, slots=True)
class _AuthorizationCallbacks:
    checkpoint: Callable[[], None] | None
    commit: Callable[[], None] | None
    enforce: Callable[..., object]
    retry_delay_seconds: Callable[..., float]
    sleep: Callable[[float], None]


@dataclass(frozen=True, slots=True)
class _ProviderFailureRetryPlan:
    next_max_tokens: int
    should_retry: bool
    retry_delay_seconds: float | None
    payload: dict[str, object]


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
    provider_operation_scope: str,
    max_completion_tokens: int | None,
    max_retry_completion_tokens: int | None,
    max_provider_attempts: int | None,
    execution_checkpoint: Callable[[], None] | None,
    execution_commit: Callable[[], None] | None,
    enforce_egress_data_policy: Callable[..., object],
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
    max_attempts = _provider_attempt_limit(
        active=active,
        max_provider_attempts=max_provider_attempts,
    )
    last_error: AIIntegrationError | None = None
    request_max_tokens = max_completion_tokens or active.max_completion_tokens
    provider_attempts = 0
    authorization_refreshes = 0
    authorization_callbacks = _AuthorizationCallbacks(
        checkpoint=execution_checkpoint,
        commit=execution_commit,
        enforce=enforce_egress_data_policy,
        retry_delay_seconds=provider_retry_delay_seconds,
        sleep=sleep,
    )
    _commit_ai_progress(db, execution_commit)

    attempt = 1
    while attempt <= max_attempts:
        if execution_checkpoint is not None:
            execution_checkpoint()
        stop_reason = record_task_run_stop_observed(
            db,
            task_run_id=task_run_id,
            stage=(
                "before_provider_retry" if attempt > 1 else "before_provider_attempt"
            ),
        )
        if stop_reason is not None:
            _commit_ai_progress(db, execution_commit)
            raise AITaskRunStoppedError(stop_reason)
        _commit_ai_progress(db, execution_commit)

        call_kwargs: dict[str, object] = {"messages": messages}
        if request_max_tokens != active.max_completion_tokens:
            call_kwargs["max_completion_tokens"] = request_max_tokens
        request_fingerprint = _ai_request_fingerprint(
            active=active,
            feature_type=feature_type,
            messages=messages,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            report_id=report_id,
            requested_max_tokens=request_max_tokens,
        )
        authorization, reservation = _authorize_and_reserve_provider_attempt(
            db,
            active=active,
            callbacks=authorization_callbacks,
            feature_type=feature_type,
            item_id=item_id,
            daily_brief_id=daily_brief_id,
            report_id=report_id,
            task_run_id=task_run_id,
            provider_operation_scope=provider_operation_scope,
            request_fingerprint=request_fingerprint,
            requested_max_tokens=request_max_tokens,
            provider_attempts=provider_attempts,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        if reservation is not None:
            max_attempts = reservation.max_attempts
            if reservation.resumed_safe_failure:
                provider_attempts = attempt
                assert reservation.next_max_tokens is not None
                request_max_tokens = reservation.next_max_tokens
                attempt += 1
                authorization_refreshes = 0
                continue

        try:
            authorization = _lock_final_provider_fence(
                db,
                active=active,
                callbacks=authorization_callbacks,
                prior_authorization=authorization,
                reservation=reservation,
                feature_type=feature_type,
                item_id=item_id,
                daily_brief_id=daily_brief_id,
                report_id=report_id,
                task_run_id=task_run_id,
                request_fingerprint=request_fingerprint,
                provider_attempts=provider_attempts,
                attempt=attempt,
            )
        except _AIProviderAuthorizationRefreshRequired:
            authorization_refreshes = _next_authorization_refresh_or_raise(
                active=active,
                authorization_refreshes=authorization_refreshes,
                provider_attempts=provider_attempts,
            )
            continue
        authorization_refreshes = 0
        provider_attempts = attempt
        try:
            completion = call_ai_json(active, **call_kwargs)
        except AIIntegrationError as exc:
            checkpoint_error = _capture_checkpoint_error(execution_checkpoint)
            last_error = exc
            if exc.provider_io_outcome == AI_PROVIDER_IO_AMBIGUOUS:
                _settle_ambiguous_attempt_or_leave_reserved(
                    db,
                    execution_commit=execution_commit,
                    authorization=authorization,
                    reservation=reservation,
                    request_fingerprint=request_fingerprint,
                    provider_attempts=provider_attempts,
                    feature_type=feature_type,
                    task_run_id=task_run_id,
                    message=str(exc),
                    record_usage_event=record_usage_event,
                    active=active,
                    item_id=item_id,
                    daily_brief_id=daily_brief_id,
                    report_id=report_id,
                )
                raise _ambiguous_provider_attempt(provider_attempts) from exc
            retry_callback_error: Exception | None = None
            try:
                retry_plan = _provider_failure_retry_plan(
                    feature_type=feature_type,
                    report_feature_type=report_feature_type,
                    error=exc,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    request_max_tokens=request_max_tokens,
                    max_retry_completion_tokens=max_retry_completion_tokens,
                    next_retry_max_completion_tokens=(next_retry_max_completion_tokens),
                    ai_error_is_retryable=ai_error_is_retryable,
                    provider_retry_delay_seconds=provider_retry_delay_seconds,
                )
                next_request_max_tokens = retry_plan.next_max_tokens
                should_retry = retry_plan.should_retry
                retry_delay_seconds = retry_plan.retry_delay_seconds
                payload = retry_plan.payload
            except Exception as callback_exc:
                logger.exception(
                    "ai_provider_retry_callback_failed feature_type=%s "
                    "task_run_id=%s attempt=%s error_type=%s",
                    feature_type,
                    task_run_id,
                    attempt,
                    type(callback_exc).__name__,
                )
                retry_callback_error = callback_exc
                exc.retryable = False
                next_request_max_tokens = request_max_tokens
                should_retry = False
                retry_delay_seconds = None
                payload = {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "requested_max_tokens": request_max_tokens,
                    "provider_io_outcome": exc.provider_io_outcome,
                    "retryable": False,
                    "retry_processing_failed": True,
                }
            try:
                mark_ai_egress_provider_io_state(
                    db,
                    authorization=authorization,
                    state=(
                        "not_sent"
                        if exc.provider_io_outcome == AI_PROVIDER_IO_NOT_SENT
                        else "sent"
                    ),
                    attempt_count=provider_attempts,
                )
                if reservation is not None:
                    settle_ai_provider_attempt(
                        db,
                        receipt_id=reservation.receipt_id,
                        request_fingerprint=request_fingerprint,
                        state="failed",
                        io_outcome=exc.provider_io_outcome,
                        retryable=should_retry,
                        reservation_generation=reservation.reservation_generation,
                        next_max_tokens=(
                            next_request_max_tokens if should_retry else None
                        ),
                    )
                    _record_provider_attempt_settled_event(
                        db,
                        task_run_id=task_run_id,
                        reservation=reservation,
                        outcome="failed",
                        io_outcome=exc.provider_io_outcome,
                        retryable=should_retry,
                    )
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
                        payload={
                            **payload,
                            **_receipt_event_payload(reservation),
                        },
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
            except Exception as settlement_exc:
                _rollback_quietly(db)
                if exc.provider_io_outcome == AI_PROVIDER_IO_NOT_SENT:
                    raise _reservation_unsettled(provider_attempts) from settlement_exc
                raise _ambiguous_provider_attempt(provider_attempts) from settlement_exc
            if checkpoint_error is not None:
                raise checkpoint_error from exc
            if retry_callback_error is not None:
                exc.attempt_count = provider_attempts
                raise exc from retry_callback_error
            if should_retry:
                request_max_tokens = next_request_max_tokens
                if retry_delay_seconds is not None and retry_delay_seconds > 0:
                    sleep(retry_delay_seconds)
                attempt += 1
                authorization_refreshes = 0
                continue
            exc.attempt_count = provider_attempts
            raise
        except Exception as exc:
            logger.exception(
                "ai_provider_attempt_ambiguous feature_type=%s task_run_id=%s "
                "attempt=%s error_type=%s",
                feature_type,
                task_run_id,
                attempt,
                type(exc).__name__,
            )
            _settle_ambiguous_attempt_or_leave_reserved(
                db,
                execution_commit=execution_commit,
                authorization=authorization,
                reservation=reservation,
                request_fingerprint=request_fingerprint,
                provider_attempts=provider_attempts,
                feature_type=feature_type,
                task_run_id=task_run_id,
                message=str(exc),
                record_usage_event=record_usage_event,
                active=active,
                item_id=item_id,
                daily_brief_id=daily_brief_id,
                report_id=report_id,
            )
            raise _ambiguous_provider_attempt(provider_attempts) from exc

        checkpoint_error = _capture_checkpoint_error(execution_checkpoint)
        try:
            mark_ai_egress_provider_io_state(
                db,
                authorization=authorization,
                state="sent",
                attempt_count=provider_attempts,
            )
            if reservation is not None:
                settle_ai_provider_attempt(
                    db,
                    receipt_id=reservation.receipt_id,
                    request_fingerprint=request_fingerprint,
                    state="succeeded",
                    io_outcome="response_received",
                    retryable=False,
                    reservation_generation=reservation.reservation_generation,
                )
                _record_provider_attempt_settled_event(
                    db,
                    task_run_id=task_run_id,
                    reservation=reservation,
                    outcome="succeeded",
                    io_outcome="response_received",
                    retryable=False,
                )
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
                    **_receipt_event_payload(reservation),
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
        except Exception as settlement_exc:
            _rollback_quietly(db)
            raise _ambiguous_provider_attempt(provider_attempts) from settlement_exc
        if checkpoint_error is not None:
            raise checkpoint_error
        return replace(completion, attempt_count=provider_attempts)

    if last_error is None:
        raise AIIntegrationError(
            "AI provider attempt history exhausted its durable retry budget.",
            retryable=False,
        )
    last_error.attempt_count = max_attempts
    raise last_error


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
    report_feature_type: str,
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
    report_truncation_has_headroom = not (
        feature_type == report_feature_type
        and error.retry_hint == "expand_completion_budget"
        and next_max_tokens <= request_max_tokens
    )
    should_retry = (
        attempt < max_attempts
        and ai_error_is_retryable(error)
        and report_truncation_has_headroom
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


def _authorize_and_reserve_provider_attempt(
    db: Session,
    *,
    active: ActiveAISettings,
    callbacks: _AuthorizationCallbacks,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    task_run_id: uuid.UUID | None,
    provider_operation_scope: str,
    request_fingerprint: str,
    requested_max_tokens: int,
    provider_attempts: int,
    attempt: int,
    max_attempts: int,
) -> tuple[AIEgressAuthorization, AIProviderAttemptReservation | None]:
    policy_attempt = 0
    while True:
        policy_attempt += 1
        authorization: AIEgressAuthorization | None = None
        try:
            candidate = callbacks.enforce(
                db,
                feature_type=feature_type,
                item_id=item_id,
                daily_brief_id=daily_brief_id,
                report_id=report_id,
                request_fingerprint=request_fingerprint,
            )
            if not isinstance(candidate, AIEgressAuthorization):
                raise AIEgressPolicyError(
                    "AI provider request is paused because its data-policy "
                    "authorization result is invalid.",
                    retryable=False,
                )
            authorization = candidate
            _require_authorization_binding(
                authorization,
                request_fingerprint=request_fingerprint,
            )
            if task_run_id is None:
                if authorization.data_policy_mode in {"audit", "enforced"}:
                    raise AIEgressPolicyError(
                        "AI provider request is paused because durable task history "
                        "is required while data policy is active. Retry the operation "
                        "through its queued task.",
                        retryable=True,
                    )
                reservation = None
            else:
                reservation = reserve_ai_provider_attempt(
                    db,
                    task_run_id=task_run_id,
                    feature_type=feature_type,
                    item_id=item_id,
                    daily_brief_id=daily_brief_id,
                    report_id=report_id,
                    operation_scope=provider_operation_scope,
                    attempt_number=attempt,
                    max_attempts=max_attempts,
                    requested_max_tokens=requested_max_tokens,
                    request_fingerprint=request_fingerprint,
                    iam_revision=authorization.iam_revision,
                    data_policy_revision=authorization.data_policy_revision,
                    data_policy_mode=authorization.data_policy_mode,
                )
                if not reservation.resumed_safe_failure:
                    mark_ai_egress_provider_io_state(
                        db,
                        authorization=authorization,
                        state="reserved",
                        attempt_count=attempt,
                    )
                record_ai_task_event(
                    db,
                    run_id=task_run_id,
                    event_type=(
                        "provider_exchange_resumed"
                        if reservation.resumed_safe_failure
                        else "provider_exchange_started"
                    ),
                    payload={
                        **_receipt_event_payload(reservation),
                        "request_fingerprint": request_fingerprint,
                        "attempt": attempt,
                        "max_attempts": reservation.max_attempts,
                        "requested_max_tokens": requested_max_tokens,
                        "data_policy_mode": authorization.data_policy_mode,
                        "data_policy_revision": authorization.data_policy_revision,
                        "iam_revision": authorization.iam_revision,
                    },
                )
            _commit_egress_authorization(db, callbacks.commit)
            return authorization, reservation
        except AIProviderAttemptStateError as exc:
            try:
                _commit_ai_progress(db, callbacks.commit)
            except Exception:
                _rollback_quietly(db)
            raise _provider_attempt_replay_blocked(
                exc,
                attempt_count=max(provider_attempts, attempt),
            ) from exc
        except AIProviderTaskBindingError as binding_exc:
            _rollback_quietly(db)
            policy_error = AIEgressPolicyError(
                str(binding_exc), retryable=binding_exc.retryable
            )
        except SQLAlchemyError as database_exc:
            _rollback_quietly(db)
            policy_error = _authorization_checkpoint_error(database_exc)
        except AIEgressPolicyError as caught_policy_error:
            policy_error = caught_policy_error
        if callbacks.checkpoint is not None:
            callbacks.checkpoint()
        should_retry = (
            policy_error.retryable and policy_attempt <= active.request_max_retries
        )
        try:
            if task_run_id is not None:
                record_ai_task_event(
                    db,
                    run_id=task_run_id,
                    event_type=(
                        "egress_policy_retry"
                        if should_retry
                        else "egress_policy_unavailable"
                        if policy_error.retryable
                        else "egress_policy_denied"
                    ),
                    message=str(policy_error),
                    payload={
                        "provider_io_state": "not_sent",
                        "provider_attempt_count": provider_attempts,
                        "policy_attempt": policy_attempt,
                        "retryable": policy_error.retryable,
                    },
                )
            _commit_ai_progress(db, callbacks.commit)
        except Exception as commit_exc:
            _rollback_quietly(db)
            policy_error = _authorization_checkpoint_error(commit_exc)
            should_retry = policy_attempt <= active.request_max_retries
        if not should_retry:
            policy_error.attempt_count = provider_attempts
            raise policy_error
        retry_delay_seconds = callbacks.retry_delay_seconds(attempt=policy_attempt)
        if retry_delay_seconds > 0:
            callbacks.sleep(retry_delay_seconds)


def _lock_final_provider_fence(
    db: Session,
    *,
    active: ActiveAISettings,
    callbacks: _AuthorizationCallbacks,
    prior_authorization: AIEgressAuthorization,
    reservation: AIProviderAttemptReservation | None,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    task_run_id: uuid.UUID | None,
    request_fingerprint: str,
    provider_attempts: int,
    attempt: int,
) -> AIEgressAuthorization:
    """Reauthorize and retain all policy/task locks through the provider call."""

    policy_attempt = 0
    while True:
        policy_attempt += 1
        final_authorization: AIEgressAuthorization | None = None
        locked_receipt = None
        try:
            candidate = callbacks.enforce(
                db,
                feature_type=feature_type,
                item_id=item_id,
                daily_brief_id=daily_brief_id,
                report_id=report_id,
                request_fingerprint=request_fingerprint,
            )
            if not isinstance(candidate, AIEgressAuthorization):
                raise AIEgressPolicyError(
                    "AI provider request is paused because its final data-policy "
                    "authorization result is invalid.",
                    retryable=False,
                )
            final_authorization = candidate
            _require_authorization_binding(
                final_authorization,
                request_fingerprint=request_fingerprint,
            )
            if reservation is not None:
                if task_run_id is None:
                    raise AIProviderTaskBindingError(
                        "AI provider task history is unavailable.", retryable=True
                    )
                locked_receipt = lock_ai_provider_attempt_for_io(
                    db,
                    reservation=reservation,
                    task_run_id=task_run_id,
                    feature_type=feature_type,
                    item_id=item_id,
                    daily_brief_id=daily_brief_id,
                    report_id=report_id,
                    request_fingerprint=request_fingerprint,
                )
        except AIProviderAttemptStateError as state_exc:
            _rollback_quietly(db)
            raise _provider_attempt_replay_blocked(
                state_exc,
                attempt_count=max(provider_attempts, attempt),
            ) from state_exc
        except AIProviderTaskBindingError as binding_exc:
            policy_error = AIEgressPolicyError(
                str(binding_exc), retryable=binding_exc.retryable
            )
        except SQLAlchemyError as database_exc:
            _rollback_quietly(db)
            policy_error = _authorization_checkpoint_error(database_exc)
        except AIEgressPolicyError as caught_policy_error:
            policy_error = caught_policy_error
        except Exception as unexpected_exc:
            logger.exception(
                "ai_final_egress_fence_failed feature_type=%s task_run_id=%s "
                "error_type=%s",
                feature_type,
                task_run_id,
                type(unexpected_exc).__name__,
            )
            policy_error = AIEgressPolicyError(
                "AI provider request is paused because its final data-policy "
                "authorization failed.",
                retryable=False,
            )
            _rollback_quietly(db)
        else:
            assert final_authorization is not None
            if locked_receipt is not None and not _authorization_snapshot_matches(
                locked_receipt,
                final_authorization,
            ):
                try:
                    _void_final_provider_reservation(
                        db,
                        execution_commit=callbacks.commit,
                        prior_authorization=prior_authorization,
                        final_authorization=final_authorization,
                        reservation=reservation,
                        request_fingerprint=request_fingerprint,
                        provider_attempts=provider_attempts,
                        attempt=attempt,
                        task_run_id=task_run_id,
                        event_type="egress_authorization_refreshed",
                        message=(
                            "AI provider authorization changed before external I/O; "
                            "the same durable attempt will be refreshed."
                        ),
                    )
                except AIProviderAttemptStateError as state_exc:
                    _rollback_quietly(db)
                    raise _provider_attempt_replay_blocked(
                        state_exc,
                        attempt_count=max(provider_attempts, attempt),
                    ) from state_exc
                except Exception as settlement_exc:
                    _rollback_quietly(db)
                    raise _reservation_unsettled(provider_attempts) from settlement_exc
                raise _AIProviderAuthorizationRefreshRequired

            mark_ai_egress_provider_io_state(
                db,
                authorization=final_authorization,
                state="reserved",
                attempt_count=attempt,
            )
            return final_authorization

        should_retry_policy = (
            policy_error.retryable and policy_attempt <= active.request_max_retries
        )
        if should_retry_policy:
            _rollback_quietly(db)
            retry_delay_seconds = callbacks.retry_delay_seconds(attempt=policy_attempt)
            if retry_delay_seconds > 0:
                callbacks.sleep(retry_delay_seconds)
            continue

        try:
            _void_final_provider_reservation(
                db,
                execution_commit=callbacks.commit,
                prior_authorization=prior_authorization,
                final_authorization=final_authorization,
                reservation=reservation,
                request_fingerprint=request_fingerprint,
                provider_attempts=provider_attempts,
                attempt=attempt,
                task_run_id=task_run_id,
                event_type=(
                    "egress_policy_unavailable"
                    if policy_error.retryable
                    else "egress_policy_denied"
                ),
                message=str(policy_error),
            )
        except AIProviderAttemptStateError as state_exc:
            _rollback_quietly(db)
            raise _provider_attempt_replay_blocked(
                state_exc,
                attempt_count=max(provider_attempts, attempt),
            ) from state_exc
        except Exception as settlement_exc:
            _rollback_quietly(db)
            raise _reservation_unsettled(provider_attempts) from settlement_exc
        policy_error.attempt_count = provider_attempts
        raise policy_error


def _void_final_provider_reservation(
    db: Session,
    *,
    execution_commit: Callable[[], None] | None,
    prior_authorization: AIEgressAuthorization,
    final_authorization: AIEgressAuthorization | None,
    reservation: AIProviderAttemptReservation | None,
    request_fingerprint: str,
    provider_attempts: int,
    attempt: int,
    task_run_id: uuid.UUID | None,
    event_type: str,
    message: str,
) -> None:
    mark_ai_egress_provider_io_state(
        db,
        authorization=prior_authorization,
        state="not_sent",
        attempt_count=attempt,
    )
    if (
        final_authorization is not None
        and final_authorization.audit_log_id != prior_authorization.audit_log_id
    ):
        mark_ai_egress_provider_io_state(
            db,
            authorization=final_authorization,
            state="not_sent",
            attempt_count=attempt,
        )
    if reservation is not None:
        void_ai_provider_attempt_reservation(
            db,
            receipt_id=reservation.receipt_id,
            request_fingerprint=request_fingerprint,
            reservation_generation=reservation.reservation_generation,
        )
    if task_run_id is not None:
        record_ai_task_event(
            db,
            run_id=task_run_id,
            event_type=event_type,
            message=message,
            payload={
                "stage": "final_provider_fence",
                "provider_io_state": "not_sent",
                "provider_attempt_count": provider_attempts,
                "attempt": attempt,
                "attempt_consumed": False,
                **_receipt_event_payload(reservation),
            },
        )
    _commit_ai_progress(db, execution_commit)


def _authorization_snapshot_matches(
    receipt,
    authorization: AIEgressAuthorization,
) -> bool:
    return (
        receipt.iam_revision == authorization.iam_revision
        and receipt.data_policy_revision == authorization.data_policy_revision
        and receipt.data_policy_mode == authorization.data_policy_mode
    )


def _ai_request_fingerprint(
    *,
    active: ActiveAISettings,
    feature_type: str,
    messages: list[dict[str, str]],
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    requested_max_tokens: int,
) -> str:
    serialized = json.dumps(
        {
            "feature_type": feature_type,
            "messages": messages,
            "item_id": str(item_id) if item_id is not None else None,
            "daily_brief_id": (
                str(daily_brief_id) if daily_brief_id is not None else None
            ),
            "report_id": str(report_id) if report_id is not None else None,
            "provider_type": active.provider_type,
            "base_url": getattr(active, "base_url", None),
            "model": active.model,
            "temperature": getattr(active, "temperature", None),
            "max_tokens": max(1, int(requested_max_tokens)),
            "stream": False,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _next_authorization_refresh_or_raise(
    *,
    active: ActiveAISettings,
    authorization_refreshes: int,
    provider_attempts: int,
) -> int:
    next_refresh = max(0, int(authorization_refreshes)) + 1
    if next_refresh <= max(1, int(active.request_max_retries) + 1):
        return next_refresh
    policy_error = AIEgressPolicyError(
        "AI provider request is paused because its authorization changed repeatedly "
        "before external I/O. Retry the task after policy updates settle.",
        retryable=True,
    )
    policy_error.attempt_count = provider_attempts
    raise policy_error


def _require_authorization_binding(
    authorization: AIEgressAuthorization,
    *,
    request_fingerprint: str,
) -> None:
    if authorization.request_fingerprint != request_fingerprint:
        raise AIEgressPolicyError(
            "AI provider request is paused because its authorization does not match "
            "the prepared request. Retry the operation.",
            retryable=True,
        )


def _settle_ambiguous_attempt_or_leave_reserved(
    db: Session,
    *,
    execution_commit: Callable[[], None] | None,
    authorization: AIEgressAuthorization,
    reservation: AIProviderAttemptReservation | None,
    request_fingerprint: str,
    provider_attempts: int,
    feature_type: str,
    task_run_id: uuid.UUID | None,
    message: str,
    record_usage_event: Callable[..., None],
    active: ActiveAISettings,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
) -> None:
    try:
        mark_ai_egress_provider_io_state(
            db,
            authorization=authorization,
            state="ambiguous",
            attempt_count=provider_attempts,
        )
        if reservation is not None:
            settle_ai_provider_attempt(
                db,
                receipt_id=reservation.receipt_id,
                request_fingerprint=request_fingerprint,
                state="ambiguous",
                io_outcome="ambiguous",
                retryable=False,
                reservation_generation=reservation.reservation_generation,
            )
            _record_provider_attempt_settled_event(
                db,
                task_run_id=task_run_id,
                reservation=reservation,
                outcome="ambiguous",
                io_outcome="ambiguous",
                retryable=False,
            )
        if task_run_id is not None:
            record_ai_task_event(
                db,
                run_id=task_run_id,
                event_type="provider_exchange_ambiguous",
                message=message,
                payload={
                    "attempt": provider_attempts,
                    "provider_io_outcome": "ambiguous",
                    "retryable": False,
                    **_receipt_event_payload(reservation),
                },
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
            error=message,
        )
        _commit_ai_progress(db, execution_commit)
    except Exception:
        logger.exception(
            "ai_provider_ambiguous_settlement_failed feature_type=%s "
            "task_run_id=%s attempt=%s",
            feature_type,
            task_run_id,
            provider_attempts,
        )
        _rollback_quietly(db)


def _record_provider_attempt_settled_event(
    db: Session,
    *,
    task_run_id: uuid.UUID | None,
    reservation: AIProviderAttemptReservation,
    outcome: str,
    io_outcome: str,
    retryable: bool,
) -> None:
    if task_run_id is None:
        return
    record_ai_task_event(
        db,
        run_id=task_run_id,
        event_type="provider_exchange_settled",
        payload={
            **_receipt_event_payload(reservation),
            "attempt": reservation.attempt_number,
            "outcome": outcome,
            "provider_io_outcome": io_outcome,
            "retryable": retryable,
        },
    )


def _receipt_event_payload(
    reservation: AIProviderAttemptReservation | None,
) -> dict[str, object]:
    if reservation is None:
        return {}
    return {
        "receipt_id": str(reservation.receipt_id),
        "operation_id": str(reservation.operation_id),
        "reservation_generation": reservation.reservation_generation,
    }


def _ambiguous_provider_attempt(attempt_count: int) -> AIProviderAttemptAmbiguousError:
    error = AIProviderAttemptAmbiguousError(
        "The AI provider may have processed this request, but ThreatLens could not "
        "confirm durable local settlement. Automatic replay is blocked to avoid "
        "duplicate disclosure or charges; inspect the AI task history before retrying.",
        retryable=False,
    )
    error.attempt_count = max(1, int(attempt_count))
    return error


def _provider_attempt_replay_blocked(
    state_error: AIProviderAttemptStateError,
    *,
    attempt_count: int,
) -> AIProviderAttemptReplayBlockedError:
    error = AIProviderAttemptReplayBlockedError(
        f"{state_error} ThreatLens did not issue another provider request. Review "
        "the durable receipt before starting a new logical operation or reconciling "
        "an unresolved attempt.",
        retryable=False,
        provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
    )
    error.attempt_count = max(0, int(attempt_count))
    return error


def _reservation_unsettled(
    attempt_count: int,
) -> AIProviderReservationUnsettledError:
    error = AIProviderReservationUnsettledError(
        "ThreatLens did not call the AI provider, but it could not safely settle the "
        "durable reservation. Automatic replay is blocked; reconcile the provider "
        "attempt as definitely not sent before retrying.",
        retryable=False,
        provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
    )
    error.attempt_count = max(0, int(attempt_count))
    return error


def _authorization_checkpoint_error(exc: Exception) -> AIEgressPolicyError:
    return AIEgressPolicyError(
        "AI provider request is paused because its data-policy authorization "
        "checkpoint could not be committed. Retry the request.",
        retryable=True,
    )


def _capture_checkpoint_error(
    checkpoint: Callable[[], None] | None,
) -> Exception | None:
    if checkpoint is None:
        return None
    try:
        checkpoint()
    except Exception as exc:
        return exc
    return None


def _rollback_quietly(db: Session) -> None:
    try:
        db.rollback()
    except SQLAlchemyError:
        pass


def _commit_ai_progress(
    db: Session,
    execution_commit: Callable[[], None] | None,
) -> None:
    if execution_commit is not None:
        execution_commit()
        return
    db.commit()


def _commit_egress_authorization(
    db: Session,
    execution_commit: Callable[[], None] | None,
) -> None:
    try:
        _commit_ai_progress(db, execution_commit)
    except Exception as exc:
        _rollback_quietly(db)
        raise _authorization_checkpoint_error(exc) from exc


__all__ = [
    "AIProviderAttemptAmbiguousError",
    "AIProviderAttemptReplayBlockedError",
    "AIProviderReservationUnsettledError",
    "AITaskRunStoppedError",
    "run_ai_json_request",
]
