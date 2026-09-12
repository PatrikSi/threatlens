import pytest
from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.services import ai_provider_budgets as budgets
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from tests.unit.test_ai_request_runtime import _completion, _run_request, _task_run


@pytest.mark.parametrize("telemetry_fails", [False, True])
def test_admission_deferral_voids_receipt_and_resumes_same_attempt(db_session, monkeypatch, telemetry_fails):
    run = _task_run(db_session)
    provider_calls = []
    usage = []

    def deny(*_args, **_kwargs):
        raise AIWorkflowDeferred("provider_concurrency_budget", 1)

    def record_usage(*_args, **values):
        if telemetry_fails and values.get("provider_io_outcome") == "not_sent":
            raise RuntimeError("synthetic deferral telemetry failure")
        usage.append(values)

    def call(*_args, **_kwargs):
        provider_calls.append(True)
        return _completion()

    monkeypatch.setattr(budgets, "reserve_provider_budget", deny)
    with pytest.raises(AIWorkflowDeferred):
        _run_request(db_session, task_run_id=run.id, call_provider=call, record_usage=record_usage)
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id))
    assert receipt.state == "voided"
    assert provider_calls == []
    receipt_id = receipt.id
    monkeypatch.setattr(budgets, "reserve_provider_budget", lambda *_args, **_kwargs: None)
    completion = _run_request(db_session, task_run_id=run.id, call_provider=call, record_usage=record_usage)
    assert completion.attempt_count == 1
    assert provider_calls == [True]
    db_session.expire_all()
    assert db_session.get(AIProviderAttemptReceipt, receipt_id).state == "succeeded"
    assert usage[-1]["provider_io_outcome"] == "response_received"
