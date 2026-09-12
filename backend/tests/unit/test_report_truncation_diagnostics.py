"""Terminal report errors explain context limits without changing provider I/O."""

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import get_settings
from app.models.report import Report
from app.services import ai_integration, report_generation
from app.services.ai_config import load_active_ai_settings
from app.services.ai_context_budget import build_context_budget
from app.services.ai_provider_client import AIIntegrationError
from app.services.report_prompt_budget import estimate_message_tokens


def _request(db, *, active, budget, messages, report_id=None):
    return report_generation._request_report_completion(
        db,
        active=active,
        budget=budget,
        messages=messages,
        report_id=report_id or uuid.uuid4(),
        task_run_id=None,
        provider_operation_scope="evidence_batch:1",
        max_provider_attempts=5,
        execution_checkpoint=None,
        execution_commit=None,
    )


def test_reasoning_truncation_stops_at_1588_context_headroom_and_explains_why(
    db_session, monkeypatch
):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    report = Report(
        id=uuid.uuid4(), title="Synthetic context-limited report", report_type="custom",
        status="running", trigger_source="manual", generation_stage="evidence_synthesis",
        period_start=now - timedelta(days=1), period_end=now,
    )
    db_session.add(report)
    db_session.commit()
    active = replace(
        load_active_ai_settings(db_session),
        ai_enabled=True, ai_configured=True,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="synthetic-reasoning-model", api_key=None,
        max_completion_tokens=131_072, request_max_retries=4,
        report_reserved_output_tokens=1200, report_context_window_tokens=8192,
    )
    budget = build_context_budget(
        context_window_tokens=8192, reserved_output_tokens=1200, safety_percent=15,
    )
    messages = [{"role": "user", "content": "x" * 9982}]
    assert estimate_message_tokens(messages) == 4991
    requested_tokens = []

    def respond(request):
        tokens = json.loads(request.content)["max_tokens"]
        requested_tokens.append(tokens)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": None}, "finish_reason": "length"}],
                "usage": {
                    "prompt_tokens": 4991,
                    "completion_tokens": tokens,
                    "completion_tokens_details": {"reasoning_tokens": tokens},
                },
            },
        )

    monkeypatch.setattr(
        ai_integration, "build_safe_http_client",
        lambda **_: httpx.Client(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(ai_integration, "_provider_retry_delay_seconds", lambda **_: 0)
    original_call = ai_integration._call_ai_json
    provider_errors = []

    def capture_error(*args, **kwargs):
        try:
            return original_call(*args, **kwargs)
        except AIIntegrationError as error:
            provider_errors.append(error)
            raise

    monkeypatch.setattr(ai_integration, "_call_ai_json", capture_error)
    with pytest.raises(AIIntegrationError) as caught:
        _request(
            db_session, active=active, budget=budget, messages=messages,
            report_id=report.id,
        )

    assert requested_tokens == [1200, 1588]
    error = caught.value
    assert error is provider_errors[-1]
    assert error.attempt_count == 2
    assert error.retry_hint == "expand_completion_budget"
    assert error.retryable is True
    assert error.provider_io_outcome == "response_received"
    assert error.request_payload["max_tokens"] == 1588
    assert error.request_payload["messages"] == messages
    assert error.response_json["usage"]["completion_tokens_details"]["reasoning_tokens"] == 1588
    assert error.status_code == 200
    for fragment in (
        "context window 8,192", "estimated serialized input 4,991",
        "safety reserve 1,229", "protocol reserve 384",
        "remaining output headroom 1,588", "Initial report allowance 1,200",
        "final request allowance 1,588", "retry ceiling 1,588",
        "provider attempts 2", "The remaining context limits output",
    ):
        assert fragment in str(error)
    assert "x" * 80 not in str(error)


@pytest.mark.parametrize(
    "provider_default, expected_ceiling, limiting_factor",
    [
        (8000, "8,000", "report/provider output settings"),
        (131_072, "131,072", "application output limit"),
    ],
)
@pytest.mark.parametrize("final_tokens", [None, 1200])
def test_diagnostic_distinguishes_output_ceiling_without_inventing_final_request(
    monkeypatch, provider_default, expected_ceiling, limiting_factor, final_tokens
):
    request_payload = {"max_tokens": final_tokens} if final_tokens is not None else None
    error = AIIntegrationError(
        "Truncated", retry_hint="expand_completion_budget",
        provider_io_outcome="response_received",
        request_payload=request_payload,
    )
    error.attempt_count = 3

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(report_generation, "request_ai_json_with_usage", fail)
    with pytest.raises(AIIntegrationError) as caught:
        _request(
            None,
            active=SimpleNamespace(
                report_reserved_output_tokens=1200,
                max_completion_tokens=provider_default,
            ),
            budget=build_context_budget(
                context_window_tokens=1_000_000,
                reserved_output_tokens=1200,
                safety_percent=15,
            ),
            messages=[{"role": "user", "content": "Evidence"}],
        )
    assert caught.value is error
    assert f"retry ceiling {expected_ceiling}" in str(error)
    assert limiting_factor in str(error)
    expected_final = "unavailable" if final_tokens is None else "1,200"
    assert f"final request allowance {expected_final}" in str(error)
    assert "provider attempts 3" in str(error)
    assert error.request_payload is request_payload


def test_nontruncation_failure_remains_unchanged(monkeypatch):
    error = AIIntegrationError(
        "Authorization changed", provider_io_outcome="not_sent", retryable=False,
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(report_generation, "request_ai_json_with_usage", fail)
    with pytest.raises(AIIntegrationError) as caught:
        _request(
            None,
            active=SimpleNamespace(report_reserved_output_tokens=1200, max_completion_tokens=8000),
            budget=build_context_budget(
                context_window_tokens=8192, reserved_output_tokens=1200, safety_percent=15,
            ),
            messages=[{"role": "user", "content": "Evidence"}],
        )
    assert caught.value is error
    assert str(error) == "Authorization changed"
    assert error.provider_io_outcome == "not_sent"
    assert error.retryable is False
