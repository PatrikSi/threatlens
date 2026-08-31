from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.ai_egress_data_policy import (
    AIEgressAuthorization,
    AIEgressPolicyError,
)
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_request_runtime import (
    AIProviderAttemptAmbiguousError,
    AIProviderAttemptReplayBlockedError,
    AIProviderReservationUnsettledError,
    run_ai_json_request,
)


def test_policy_fence_runs_after_progress_commit_before_every_provider_attempt(
    db_session,
):
    events: list[str] = []
    provider_attempt = 0
    active = SimpleNamespace(
        request_max_retries=1,
        max_completion_tokens=512,
        provider_type="openai_compatible",
        model="test-model",
    )

    def enforce_egress_data_policy(_db, **lineage):
        request_fingerprint = lineage.pop("request_fingerprint")
        assert len(request_fingerprint) == 64
        assert int(request_fingerprint, 16) >= 0
        assert lineage == {
            "feature_type": "report",
            "item_id": None,
            "daily_brief_id": None,
            "report_id": None,
        }
        events.append("fence")
        return _authorization(request_fingerprint)

    def call_ai_json(_active, **_kwargs):
        nonlocal provider_attempt
        provider_attempt += 1
        events.append(f"provider:{provider_attempt}")
        if provider_attempt == 1:
            raise AIIntegrationError(
                "temporary provider failure",
                retryable=True,
                provider_io_outcome="response_received",
            )
        return AICompletionResult(
            payload={"ok": True},
            provider="openai_compatible",
            model="test-model",
            latency_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    result = run_ai_json_request(
        db_session,
        active,
        feature_type="report",
        messages=[{"role": "user", "content": "content excluded from policy"}],
        item_id=None,
        daily_brief_id=None,
        report_id=None,
        task_run_id=None,
        provider_operation_scope="section:test",
        max_completion_tokens=None,
        max_retry_completion_tokens=None,
        max_provider_attempts=None,
        execution_checkpoint=lambda: events.append("checkpoint"),
        execution_commit=lambda: events.append("commit"),
        enforce_egress_data_policy=enforce_egress_data_policy,
        report_feature_type="report",
        call_ai_json=call_ai_json,
        record_task_run_stop_observed=lambda *_args, **_kwargs: events.append(
            "stop_check"
        ),
        record_usage_event=lambda *_args, **kwargs: events.append(
            f"usage:{kwargs['success']}"
        ),
        build_provider_exchange_payload=lambda **_kwargs: {},
        provider_retry_delay_seconds=lambda **_kwargs: 0,
        ai_error_is_retryable=lambda error: error.retryable,
        next_retry_max_completion_tokens=lambda **kwargs: kwargs["current"],
        sleep=lambda _seconds: None,
    )

    assert result.payload == {"ok": True}
    assert events == [
        "commit",
        "checkpoint",
        "stop_check",
        "commit",
        "fence",
        "commit",
        "fence",
        "provider:1",
        "checkpoint",
        "usage:False",
        "commit",
        "checkpoint",
        "stop_check",
        "commit",
        "fence",
        "commit",
        "fence",
        "provider:2",
        "checkpoint",
        "usage:True",
        "commit",
    ]


def test_policy_failure_never_counts_as_a_provider_attempt(db_session):
    provider_calls = 0
    policy_calls = 0
    usage_calls = 0
    active = SimpleNamespace(
        request_max_retries=3,
        max_completion_tokens=512,
        provider_type="openai_compatible",
        model="test-model",
    )

    def enforce_egress_data_policy(_db, **_lineage):
        nonlocal policy_calls
        policy_calls += 1
        raise AIEgressPolicyError("policy unavailable", retryable=True)

    def call_ai_json(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("the provider must not be called")

    def record_usage_event(*_args, **_kwargs):
        nonlocal usage_calls
        usage_calls += 1

    with pytest.raises(AIEgressPolicyError) as captured:
        run_ai_json_request(
            db_session,
            active,
            feature_type="report",
            messages=[{"role": "user", "content": "sensitive prompt"}],
            item_id=None,
            daily_brief_id=None,
            report_id=None,
            task_run_id=None,
            provider_operation_scope="section:test",
            max_completion_tokens=None,
            max_retry_completion_tokens=None,
            max_provider_attempts=1,
            execution_checkpoint=None,
            execution_commit=None,
            enforce_egress_data_policy=enforce_egress_data_policy,
            report_feature_type="report",
            call_ai_json=call_ai_json,
            record_task_run_stop_observed=lambda *_args, **_kwargs: None,
            record_usage_event=record_usage_event,
            build_provider_exchange_payload=lambda **_kwargs: {},
            provider_retry_delay_seconds=lambda **_kwargs: 0,
            ai_error_is_retryable=lambda error: error.retryable,
            next_retry_max_completion_tokens=lambda **kwargs: kwargs["current"],
            sleep=lambda _seconds: None,
        )

    assert captured.value.attempt_count == 0
    assert policy_calls == 4
    assert provider_calls == 0
    assert usage_calls == 0


def test_retryable_policy_failure_recovers_without_consuming_provider_attempt(
    db_session,
):
    policy_calls = 0
    provider_calls = 0

    def enforce(_db, **_lineage):
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 1:
            raise AIEgressPolicyError("policy lock timeout", retryable=True)
        return _authorization(_lineage["request_fingerprint"])

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    result = _run_request(
        db_session,
        active=_active(retries=1),
        enforce=enforce,
        call_provider=call_provider,
    )

    assert result.attempt_count == 1
    assert policy_calls == 3
    assert provider_calls == 1


def test_retryable_final_policy_failure_retries_before_provider_io(db_session):
    policy_calls = 0
    provider_calls = 0
    sleep_delays: list[float] = []

    def enforce(_db, **lineage):
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 2:
            raise AIEgressPolicyError("final policy lock timeout", retryable=True)
        return _authorization(lineage["request_fingerprint"])

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    result = _run_request(
        db_session,
        active=_active(retries=1),
        enforce=enforce,
        call_provider=call_provider,
        retry_delay_seconds=0.25,
        sleep=sleep_delays.append,
    )

    assert result.attempt_count == 1
    assert policy_calls == 3
    assert provider_calls == 1
    assert sleep_delays == [0.25]


def test_final_policy_failure_does_not_consume_provider_attempt(db_session):
    policy_calls = 0
    provider_calls = 0

    def enforce(_db, **_lineage):
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 2:
            raise AIEgressPolicyError("policy denied", retryable=False)
        return _authorization(_lineage["request_fingerprint"])

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AIIntegrationError(
            "temporary provider failure",
            retryable=True,
            provider_io_outcome="response_received",
        )

    with pytest.raises(AIEgressPolicyError) as captured:
        _run_request(
            db_session,
            active=_active(retries=1),
            enforce=enforce,
            call_provider=call_provider,
        )

    assert captured.value.attempt_count == 0
    assert policy_calls == 2
    assert provider_calls == 0


def test_active_policy_requires_durable_task_history(db_session):
    provider_calls = 0

    def enforce(_db, **lineage):
        return _authorization(lineage["request_fingerprint"], mode="audit")

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="durable task history") as captured:
        _run_request(
            db_session,
            enforce=enforce,
            call_provider=call_provider,
        )

    assert captured.value.attempt_count == 0
    assert provider_calls == 0


def test_authorization_must_match_exact_request_fingerprint(db_session):
    provider_calls = 0

    def enforce(_db, **_lineage):
        return _authorization("0" * 64)

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="does not match") as captured:
        _run_request(
            db_session,
            enforce=enforce,
            call_provider=call_provider,
        )

    assert captured.value.attempt_count == 0
    assert provider_calls == 0


def test_post_provider_database_failure_is_nonretryable_and_ambiguous(db_session):
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    def fail_usage(*_args, **_kwargs):
        raise OperationalError("INSERT usage", {}, Exception("database unavailable"))

    with pytest.raises(AIProviderAttemptAmbiguousError) as captured:
        _run_request(
            db_session,
            call_provider=call_provider,
            record_usage=fail_usage,
        )

    assert captured.value.retryable is False
    assert captured.value.attempt_count == 1
    assert provider_calls == 1


def test_post_provider_custom_commit_failure_is_nonretryable_and_ambiguous(
    db_session,
):
    provider_calls = 0
    commit_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    def commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 4:
            raise RuntimeError("report lease ownership could not be committed")
        db_session.commit()

    with pytest.raises(AIProviderAttemptAmbiguousError) as captured:
        _run_request(
            db_session,
            call_provider=call_provider,
            execution_commit=commit,
        )

    assert captured.value.retryable is False
    assert captured.value.attempt_count == 1
    assert provider_calls == 1
    assert commit_calls == 4


def test_explicit_ambiguous_provider_outcome_is_settled_and_never_retried(
    db_session,
):
    task_run = _task_run(db_session)
    provider_calls = 0
    usage_events: list[dict[str, object]] = []

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AIIntegrationError(
            "provider connection closed after request upload",
            retryable=True,
            provider_io_outcome="ambiguous",
        )

    with pytest.raises(AIProviderAttemptAmbiguousError) as captured:
        _run_request(
            db_session,
            active=_active(retries=1),
            task_run_id=task_run.id,
            call_provider=call_provider,
            record_usage=lambda *_args, **kwargs: usage_events.append(kwargs),
        )

    receipt = db_session.scalar(
        select(AIProviderAttemptReceipt).where(
            AIProviderAttemptReceipt.task_run_id_snapshot == task_run.id
        )
    )
    events = list(
        db_session.scalars(
            select(AITaskEvent)
            .where(AITaskEvent.task_run_id == task_run.id)
            .order_by(AITaskEvent.created_at.asc())
        ).all()
    )
    assert receipt is not None
    assert captured.value.retryable is False
    assert captured.value.attempt_count == 1
    assert provider_calls == 1
    assert receipt.state == "ambiguous"
    assert receipt.io_outcome == "ambiguous"
    assert receipt.retryable is False
    assert [event.event_type for event in events] == [
        "provider_exchange_started",
        "provider_exchange_settled",
        "provider_exchange_ambiguous",
    ]
    assert events[-1].payload_json["provider_io_outcome"] == "ambiguous"
    assert len(usage_events) == 1
    assert usage_events[0]["success"] is False
    assert usage_events[0]["error"] == (
        "provider connection closed after request upload"
    )


@pytest.mark.parametrize(
    "failing_callback",
    ["next_tokens", "retryable", "retry_delay"],
)
def test_retry_callback_failure_settles_receipt_without_leaking_raw_error(
    db_session,
    failing_callback,
):
    task_run = _task_run(db_session)
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AIIntegrationError(
            "temporary provider rejection",
            retryable=True,
            provider_io_outcome="response_received",
        )

    def fail_retry_callback(*_args, **_kwargs):
        raise RuntimeError("retry helper crashed")

    callback_kwargs = {
        {
            "next_tokens": "next_retry_max_completion_tokens",
            "retryable": "ai_error_is_retryable",
            "retry_delay": "provider_retry_delay_seconds",
        }[failing_callback]: fail_retry_callback
    }
    with pytest.raises(
        AIIntegrationError, match="temporary provider rejection"
    ) as captured:
        _run_request(
            db_session,
            active=_active(retries=1),
            task_run_id=task_run.id,
            call_provider=call_provider,
            **callback_kwargs,
        )

    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt is not None
    assert captured.value.retryable is False
    assert captured.value.provider_io_outcome == "response_received"
    assert captured.value.attempt_count == 1
    assert "retry helper crashed" not in str(captured.value)
    assert provider_calls == 1
    assert receipt.state == "failed"
    assert receipt.io_outcome == "response_received"
    assert receipt.retryable is False
    assert receipt.next_max_tokens is None


def test_not_sent_settlement_failure_preserves_definite_transport_outcome(
    db_session,
):
    task_run = _task_run(db_session)
    provider_calls = 0
    commit_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AIIntegrationError(
            "connection refused",
            retryable=True,
            provider_io_outcome="not_sent",
        )

    def commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 4:
            raise RuntimeError("settlement commit callback failed")
        db_session.commit()

    with pytest.raises(
        AIProviderReservationUnsettledError,
        match="did not call the AI provider",
    ) as captured:
        _run_request(
            db_session,
            task_run_id=task_run.id,
            call_provider=call_provider,
            execution_commit=commit,
        )

    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt is not None
    assert captured.value.retryable is False
    assert captured.value.provider_io_outcome == "not_sent"
    assert captured.value.attempt_count == 1
    assert "settlement commit callback failed" not in str(captured.value)
    assert provider_calls == 1
    assert commit_calls == 4
    assert receipt.state == "reserved"
    assert receipt.io_outcome == "reserved"


def test_custom_phase_one_commit_failure_is_mapped_before_provider_io(db_session):
    task_run = _task_run(db_session)
    provider_calls = 0
    commit_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    def commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 3:
            raise RuntimeError("lease commit callback failed")
        db_session.commit()

    with pytest.raises(AIEgressPolicyError, match="checkpoint") as captured:
        _run_request(
            db_session,
            task_run_id=task_run.id,
            call_provider=call_provider,
            execution_commit=commit,
        )

    assert captured.value.retryable is True
    assert captured.value.provider_io_outcome == "not_sent"
    assert captured.value.attempt_count == 0
    assert "lease commit callback failed" not in str(captured.value)
    assert provider_calls == 0
    assert commit_calls == 4
    assert db_session.scalar(select(AIProviderAttemptReceipt.id)) is None


@pytest.mark.parametrize("max_provider_attempts", [0, -1])
def test_exhausted_attempt_budget_is_not_sent_with_zero_attempts(
    db_session,
    max_provider_attempts,
):
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIIntegrationError, match="budget is exhausted") as captured:
        _run_request(
            db_session,
            call_provider=call_provider,
            max_provider_attempts=max_provider_attempts,
        )

    assert captured.value.retryable is False
    assert captured.value.provider_io_outcome == "not_sent"
    assert captured.value.attempt_count == 0
    assert provider_calls == 0


def test_durable_attempt_receipt_blocks_changed_prompt_after_ambiguous_settlement(
    db_session,
):
    task_run = _task_run(db_session)
    provider_calls = 0
    messages = [{"role": "user", "content": "highly sensitive report prompt"}]

    def enforce(_db, **lineage):
        return _authorization(lineage["request_fingerprint"])

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    def fail_usage(*_args, **_kwargs):
        raise OperationalError("INSERT usage", {}, Exception("database unavailable"))

    with pytest.raises(AIProviderAttemptAmbiguousError) as captured:
        _run_request(
            db_session,
            messages=messages,
            task_run_id=task_run.id,
            enforce=enforce,
            call_provider=call_provider,
            record_usage=fail_usage,
        )
    assert captured.value.retryable is False

    with pytest.raises(AIProviderAttemptReplayBlockedError) as replay:
        _run_request(
            db_session,
            messages=[{"role": "user", "content": "changed report prompt"}],
            task_run_id=task_run.id,
            enforce=enforce,
            call_provider=call_provider,
        )
    assert replay.value.retryable is False
    assert "prepared AI request changed" in str(replay.value)

    started = list(
        db_session.scalars(
            select(AITaskEvent).where(
                AITaskEvent.task_run_id == task_run.id,
                AITaskEvent.event_type == "provider_exchange_started",
            )
        ).all()
    )
    assert provider_calls == 1
    assert len(started) == 1
    assert len(started[0].payload_json["request_fingerprint"]) == 64
    assert "highly sensitive" not in json.dumps(started[0].payload_json)


def test_settled_safe_failure_resumes_at_the_next_attempt_after_process_crash(
    db_session,
):
    task_run = _task_run(db_session)
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            raise AIIntegrationError(
                "provider overloaded",
                retryable=True,
                provider_io_outcome="response_received",
            )
        return _completion()

    def crash_before_retry(_seconds):
        raise RuntimeError("worker process crashed")

    with pytest.raises(RuntimeError, match="worker process crashed"):
        _run_request(
            db_session,
            active=_active(retries=1),
            task_run_id=task_run.id,
            call_provider=call_provider,
            retry_delay_seconds=1,
            sleep=crash_before_retry,
        )

    result = _run_request(
        db_session,
        active=_active(retries=1),
        task_run_id=task_run.id,
        call_provider=call_provider,
    )

    receipts = list(
        db_session.scalars(
            select(AIProviderAttemptReceipt).order_by(
                AIProviderAttemptReceipt.attempt_number
            )
        ).all()
    )
    assert result.attempt_count == 2
    assert provider_calls == 2
    assert [receipt.attempt_number for receipt in receipts] == [1, 2]
    assert [receipt.state for receipt in receipts] == ["failed", "succeeded"]
    assert [receipt.io_outcome for receipt in receipts] == [
        "response_received",
        "response_received",
    ]
    assert receipts[0].operation_id == receipts[1].operation_id


def test_final_fence_denial_voids_and_redelivery_reuses_same_attempt(db_session):
    task_run = _task_run(db_session)
    policy_calls = 0
    provider_calls = 0

    def deny_second_fence(_db, **lineage):
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 2:
            raise AIEgressPolicyError("policy denied", retryable=False)
        return _authorization(lineage["request_fingerprint"])

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="policy denied") as captured:
        _run_request(
            db_session,
            task_run_id=task_run.id,
            enforce=deny_second_fence,
            call_provider=call_provider,
        )

    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt is not None
    receipt_id = receipt.id
    assert captured.value.attempt_count == 0
    assert provider_calls == 0
    assert receipt.attempt_number == 1
    assert receipt.reservation_generation == 1
    assert receipt.pre_io_failure_count == 1
    assert receipt.state == "voided"
    assert receipt.io_outcome == "not_sent"

    result = _run_request(
        db_session,
        task_run_id=task_run.id,
        enforce=deny_second_fence,
        call_provider=call_provider,
    )
    db_session.refresh(receipt)

    assert result.attempt_count == 1
    assert provider_calls == 1
    assert receipt.id == receipt_id
    assert receipt.attempt_number == 1
    assert receipt.reservation_generation == 2
    assert receipt.pre_io_failure_count == 1
    assert receipt.state == "succeeded"


def test_policy_revision_change_refreshes_same_attempt_before_provider_io(db_session):
    task_run = _task_run(db_session)
    policy_calls = 0
    provider_calls = 0

    def changing_policy(_db, **lineage):
        nonlocal policy_calls
        policy_calls += 1
        revision = 1 if policy_calls == 1 else 2
        return _authorization(
            lineage["request_fingerprint"],
            iam_revision=revision,
            data_policy_revision=revision,
        )

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    result = _run_request(
        db_session,
        task_run_id=task_run.id,
        enforce=changing_policy,
        call_provider=call_provider,
    )

    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt is not None
    assert result.attempt_count == 1
    assert policy_calls == 4
    assert provider_calls == 1
    assert receipt.attempt_number == 1
    assert receipt.reservation_generation == 2
    assert receipt.pre_io_failure_count == 1
    assert receipt.iam_revision == 2
    assert receipt.data_policy_revision == 2
    assert receipt.state == "succeeded"


def test_repeated_policy_churn_stops_with_a_voided_zero_io_attempt(db_session):
    task_run = _task_run(db_session)
    policy_calls = 0
    provider_calls = 0

    def always_changing_policy(_db, **lineage):
        nonlocal policy_calls
        policy_calls += 1
        return _authorization(
            lineage["request_fingerprint"],
            iam_revision=policy_calls,
            data_policy_revision=policy_calls,
        )

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="changed repeatedly") as captured:
        _run_request(
            db_session,
            task_run_id=task_run.id,
            enforce=always_changing_policy,
            call_provider=call_provider,
        )

    receipt = db_session.scalar(select(AIProviderAttemptReceipt))
    assert receipt is not None
    assert captured.value.attempt_count == 0
    assert policy_calls == 4
    assert provider_calls == 0
    assert receipt.attempt_number == 1
    assert receipt.reservation_generation == 2
    assert receipt.pre_io_failure_count == 2
    assert receipt.state == "voided"


def test_identical_prompts_in_distinct_report_scopes_use_distinct_operations(
    db_session,
):
    task_run = _task_run(db_session)
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    for scope in ("section:executive", "section:recommendations"):
        _run_request(
            db_session,
            task_run_id=task_run.id,
            operation_scope=scope,
            call_provider=call_provider,
        )

    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt)).all())
    assert provider_calls == 2
    assert len(receipts) == 2
    assert len({receipt.operation_id for receipt in receipts}) == 2
    assert {receipt.request_fingerprint for receipt in receipts} == {
        receipts[0].request_fingerprint
    }


def test_invalid_authorization_callback_result_fails_closed_before_provider_io(
    db_session,
):
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="authorization result is invalid"):
        _run_request(
            db_session,
            enforce=lambda *_args, **_kwargs: None,
            call_provider=call_provider,
        )

    assert provider_calls == 0


@pytest.mark.parametrize("invalid_state", ["wrong_type", "finished", "cancelled"])
def test_receipt_reservation_requires_an_active_exact_task_binding(
    db_session,
    invalid_state,
):
    task_run = _task_run(db_session)
    if invalid_state == "wrong_type":
        task_run.task_type = "daily_brief"
    elif invalid_state == "finished":
        task_run.status = "success"
        task_run.finished_at = datetime.now(timezone.utc)
    else:
        task_run.metadata_json = {"cancel_requested_at": "2026-08-31T00:00:00Z"}
    db_session.commit()
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="task"):
        _run_request(
            db_session,
            task_run_id=task_run.id,
            call_provider=call_provider,
        )

    assert provider_calls == 0


def test_receipt_reservation_rejects_a_different_report_resource(db_session):
    expected_report = _report(db_session, title="expected")
    other_report = _report(db_session, title="other")
    task_run = _task_run(db_session, report_id=expected_report.id)
    provider_calls = 0

    def call_provider(_active, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _completion()

    with pytest.raises(AIEgressPolicyError, match="resource does not match"):
        _run_request(
            db_session,
            task_run_id=task_run.id,
            report_id=other_report.id,
            call_provider=call_provider,
        )

    assert provider_calls == 0


def test_post_provider_checkpoint_error_is_settled_before_propagation(db_session):
    task_run = _task_run(db_session)
    checkpoint_calls = 0

    def checkpoint():
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise RuntimeError("claim lost")

    with pytest.raises(RuntimeError, match="claim lost"):
        _run_request(
            db_session,
            task_run_id=task_run.id,
            checkpoint=checkpoint,
            enforce=lambda _db, **lineage: _authorization(
                lineage["request_fingerprint"]
            ),
        )

    events = list(
        db_session.scalars(
            select(AITaskEvent)
            .where(
                AITaskEvent.task_run_id == task_run.id,
                AITaskEvent.event_type.in_(
                    {"provider_exchange_started", "provider_exchange_settled"}
                ),
            )
            .order_by(AITaskEvent.created_at.asc())
        ).all()
    )
    assert [event.event_type for event in events] == [
        "provider_exchange_started",
        "provider_exchange_settled",
    ]
    assert events[0].payload_json["receipt_id"] == events[1].payload_json["receipt_id"]
    assert (
        events[0].payload_json["operation_id"] == events[1].payload_json["operation_id"]
    )
    assert events[1].payload_json["outcome"] == "succeeded"


def _run_request(
    db_session,
    *,
    active=None,
    messages: list[dict[str, str]] | None = None,
    task_run_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
    operation_scope: str = "section:test",
    checkpoint=None,
    enforce=None,
    call_provider=None,
    record_usage=None,
    execution_commit=None,
    max_provider_attempts: int | None = None,
    retry_delay_seconds: float = 0,
    provider_retry_delay_seconds=None,
    ai_error_is_retryable=None,
    next_retry_max_completion_tokens=None,
    sleep=None,
):
    if task_run_id is not None and report_id is None:
        task_run = db_session.get(AITaskRun, task_run_id)
        if task_run is not None:
            report_id = task_run.report_id
    return run_ai_json_request(
        db_session,
        active or _active(),
        feature_type="report",
        messages=messages or [{"role": "user", "content": "report"}],
        item_id=None,
        daily_brief_id=None,
        report_id=report_id,
        task_run_id=task_run_id,
        provider_operation_scope=operation_scope,
        max_completion_tokens=None,
        max_retry_completion_tokens=None,
        max_provider_attempts=max_provider_attempts,
        execution_checkpoint=checkpoint,
        execution_commit=execution_commit,
        enforce_egress_data_policy=enforce
        or (lambda _db, **lineage: _authorization(lineage["request_fingerprint"])),
        report_feature_type="report",
        call_ai_json=call_provider or (lambda _active, **_kwargs: _completion()),
        record_task_run_stop_observed=lambda *_args, **_kwargs: None,
        record_usage_event=record_usage or (lambda *_args, **_kwargs: None),
        build_provider_exchange_payload=lambda **_kwargs: {},
        provider_retry_delay_seconds=provider_retry_delay_seconds
        or (lambda **_kwargs: retry_delay_seconds),
        ai_error_is_retryable=ai_error_is_retryable or (lambda error: error.retryable),
        next_retry_max_completion_tokens=next_retry_max_completion_tokens
        or (lambda **kwargs: kwargs["current"]),
        sleep=sleep or (lambda _seconds: None),
    )


def _active(*, retries: int = 0):
    return SimpleNamespace(
        request_max_retries=retries,
        max_completion_tokens=512,
        provider_type="openai_compatible",
        model="test-model",
    )


def _completion() -> AICompletionResult:
    return AICompletionResult(
        payload={"ok": True},
        provider="openai_compatible",
        model="test-model",
        latency_ms=1,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
    )


def _authorization(
    request_fingerprint: str,
    *,
    mode: str = "disabled",
    iam_revision: int = 1,
    data_policy_revision: int = 1,
) -> AIEgressAuthorization:
    return AIEgressAuthorization(
        request_fingerprint=request_fingerprint,
        audit_log_id=None,
        iam_revision=iam_revision,
        data_policy_revision=data_policy_revision,
        data_policy_mode=mode,
    )


def _task_run(
    db_session,
    *,
    report_id: uuid.UUID | None = None,
) -> AITaskRun:
    if report_id is None:
        report_id = _report(db_session, title=f"runtime-{uuid.uuid4()}").id
    task_run = AITaskRun(
        task_type="report",
        trigger_source="manual",
        status="running",
        report_id=report_id,
        metadata_json={},
    )
    db_session.add(task_run)
    db_session.commit()
    return task_run


def _report(db_session, *, title: str) -> Report:
    now = datetime.now(timezone.utc)
    report = Report(
        title=title,
        period_start=now,
        period_end=now,
        filters_json={},
        prompt_config_json={},
        generation_context_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )
    db_session.add(report)
    db_session.commit()
    return report
