"""Recover through the scheduler's real discovery path without repeating paid I/O."""

import copy
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ai_article_continuation import AIArticleContinuation
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_qualification import AIQualification
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services import ai_ops, ai_qualification
from app.services.ai_provider_client import AICompletionResult
from app.services.ai_qualification_cases import SOURCE
from app.tasks.ai_qualification_tasks import generate_ai_qualification
from tests.integration.test_ai_article_continuation import continuation as continuation
from tests.integration.test_ai_provider_api import enable_ai as enable_ai
from tests.integration.test_ai_qualification import setup_qualification as setup_qualification
from tests.unit.test_ai_extraction_workflow import extraction_item as extraction_item


QUALIFICATION_TASK = "app.tasks.ai_qualification_tasks.generate_ai_qualification"


def _invoke_worker(db, monkeypatch, run_id, *, delivery):
    @contextmanager
    def session():
        yield db

    monkeypatch.setattr("app.tasks.ai_qualification_tasks.SessionLocal", session)
    generate_ai_qualification.push_request(id=delivery, hostname="qualification-worker")
    try:
        return generate_ai_qualification.run(str(run_id))
    finally:
        generate_ai_qualification.pop_request()


def _expire_worker(db, run):
    run.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()


def _lose_unsent_worker(db, run_id):
    ai_ops.start_ai_task_run(db, run_id=run_id, celery_task_id="lost-delivery")
    run = db.get(AITaskRun, run_id)
    assert run.status == "running"
    _expire_worker(db, run)
    return run


def _reconcile(db, *, snapshot_available=True, **live_tasks):
    tasks = {"active_tasks": [], "reserved_tasks": [], "scheduled_tasks": []}
    tasks.update(live_tasks)
    ai_ops._reconcile_stale_ai_runs(
        db, snapshot_available=snapshot_available, workers=[], **tasks,
    )
    db.expire_all()


def _provider_recorder(monkeypatch):
    calls = []

    def provider(_active, **kwargs):
        prompt = json.loads(kwargs["messages"][-1]["content"])
        calls.append(prompt)
        payload = (
            {
                "body_markdown": "The source reports scheduled task persistence. [S1]",
                "citations": ["S1"], "key_points": [],
            }
            if "section" in prompt else
            {
                "findings": [{
                    "text": "The source reports scheduled task persistence.",
                    "citations": ["S1"],
                    "evidence_quotes": [{"citation": "S1", "quote": SOURCE}],
                }],
            }
        )
        return AICompletionResult(
            payload=payload, provider="openai_compatible", model="fixture",
            latency_ms=10, prompt_tokens=100, completion_tokens=50, total_tokens=150,
        )

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    return calls


@pytest.mark.parametrize("snapshot_available", [True, False])
def test_reconcile_recovers_unsent_qualification_and_fences_lost_delivery(
    db_session, setup_qualification, monkeypatch, snapshot_available,
):
    _, _, run_id = setup_qualification
    run = _lose_unsent_worker(db_session, run_id)
    selection = copy.deepcopy(run.metadata_json["provider_selection"])
    _reconcile(db_session, snapshot_available=snapshot_available)
    assert run.status == "queued" and run.finished_at is None
    assert run.celery_task_id != "lost-delivery"
    assert run.metadata_json["provider_selection"] == selection
    delivery = run.celery_task_id
    dispatch = db_session.get(AIWorkflowDispatch, run_id)
    assert dispatch.state == "pending" and dispatch.delivery_id == delivery
    calls = _provider_recorder(monkeypatch)
    assert _invoke_worker(db_session, monkeypatch, run_id, delivery="lost-delivery")["status"] == "skipped"
    assert calls == []
    assert _invoke_worker(db_session, monkeypatch, run_id, delivery=delivery)["status"] == "ready"
    assert len(calls) == 2
    assert db_session.get(AITaskRun, run_id).status == "ready"
    assert db_session.get(AIWorkflowDispatch, run_id).state == "complete"


@pytest.mark.parametrize("state", ["active", "reserved", "scheduled"])
def test_inspected_qualification_is_not_mistaken_for_a_lost_worker(
    db_session, setup_qualification, state,
):
    _, _, run_id = setup_qualification
    run = _lose_unsent_worker(db_session, run_id)
    request = {"name": QUALIFICATION_TASK, "id": "lost-delivery"}
    if state == "reserved":
        request["args"] = repr((str(run_id),))
    else:
        request["kwargs"] = {"task_run_id": str(run_id)}
    raw = {"request": request, "eta": "2026-09-27T12:00:00Z"} if state == "scheduled" else request
    live = ai_ops._flatten_live_tasks({"qualification-worker": [raw]}, state=state)
    assert len(live) == 1
    assert live[0].run_id == run_id
    assert live[0].task_name == "connection_test"
    assert live[0].celery_task_id == "lost-delivery"
    key = {"active": "active_tasks", "reserved": "reserved_tasks", "scheduled": "scheduled_tasks"}[state]
    _reconcile(db_session, **{key: live})
    assert run.status == "running" and run.celery_task_id == "lost-delivery"
    assert run.finished_at is None


@pytest.mark.parametrize("snapshot_available", [True, False])
def test_waiting_qualification_does_not_expire_with_paused_consumers(
    db_session, setup_qualification, snapshot_available,
):
    _, _, run_id = setup_qualification
    run = db_session.get(AITaskRun, run_id)
    dispatch = db_session.get(AIWorkflowDispatch, run_id)
    original_delivery = run.celery_task_id
    original_dispatch = (dispatch.state, dispatch.delivery_id, dispatch.payload_json)
    _expire_worker(db_session, run)
    _reconcile(db_session, snapshot_available=snapshot_available)
    assert run.status == "queued" and run.finished_at is None
    assert run.celery_task_id == original_delivery
    assert (dispatch.state, dispatch.delivery_id, dispatch.payload_json) == original_dispatch
    assert db_session.get(AIQualification, run_id).reserved_tokens == 0


@pytest.mark.parametrize("receipt_state", ["reserved", "ambiguous", "succeeded"])
def test_reconcile_settles_uncheckpointed_provider_io_without_replaying(
    db_session, setup_qualification, monkeypatch, receipt_state,
):
    _, _, run_id = setup_qualification
    run = _lose_unsent_worker(db_session, run_id)
    receipt = AIProviderAttemptReceipt(
        operation_id=uuid.uuid4(), attempt_number=1, request_fingerprint="a" * 64,
        task_run_id_snapshot=run_id, feature_type="connection_test",
        resource_type="connection_test",
        max_attempts=1, requested_max_tokens=1024, iam_revision=1,
        data_policy_revision=1, data_policy_mode="disabled", state=receipt_state,
        io_outcome="response_received" if receipt_state == "succeeded" else receipt_state,
        retryable=None if receipt_state == "reserved" else False,
        settled_at=None if receipt_state == "reserved" else datetime.now(timezone.utc),
    )
    db_session.add(receipt)
    db_session.commit()
    _reconcile(db_session)
    assert run.status == "error" and run.reason == "stale_task_lost"
    assert run.finished_at is not None
    assert db_session.get(AIWorkflowDispatch, run_id).state == "complete"
    assert db_session.get(AIProviderAttemptReceipt, receipt.id).state == receipt_state
    calls = _provider_recorder(monkeypatch)
    assert _invoke_worker(db_session, monkeypatch, run_id, delivery="lost-delivery")["status"] == "skipped"
    assert calls == []


def test_reconcile_resumes_only_unsent_probes_after_a_checkpointed_crash(
    db_session, setup_qualification, monkeypatch,
):
    _, _, run_id = setup_qualification
    calls = _provider_recorder(monkeypatch)
    messages = ai_qualification.qualification_messages

    class WorkerCrash(BaseException):
        """Simulate process death, outside the worker's ordinary error handler."""

    def crash_before_second_probe(feature):
        if feature == "report_section":
            raise WorkerCrash()
        return messages(feature)

    monkeypatch.setattr(ai_qualification, "qualification_messages", crash_before_second_probe)
    with pytest.raises(WorkerCrash):
        _invoke_worker(db_session, monkeypatch, run_id, delivery="lost-delivery")
    row = db_session.get(AIQualification, run_id)
    assert len(calls) == 1
    assert len(row.results_json) == 1 and row.results_json[0]["state"] == "completed"
    completed = copy.deepcopy(row.results_json[0])
    initial_reservation = row.reserved_tokens
    assert db_session.scalar(select(AIProviderAttemptReceipt.state)) == "succeeded"
    run = db_session.get(AITaskRun, run_id)
    _expire_worker(db_session, run)
    _reconcile(db_session)
    assert run.status == "queued"
    delivery = run.celery_task_id
    assert row.results_json == [completed]
    assert row.reserved_tokens == initial_reservation
    monkeypatch.setattr(ai_qualification, "qualification_messages", messages)
    assert _invoke_worker(db_session, monkeypatch, run_id, delivery="lost-delivery")["status"] == "skipped"
    assert len(calls) == 1
    assert _invoke_worker(db_session, monkeypatch, run_id, delivery=delivery)["status"] == "ready"
    assert len(calls) == 2
    assert row.results_json[0] == completed
    assert initial_reservation < row.reserved_tokens <= row.token_budget
    assert list(db_session.scalars(select(AIProviderAttemptReceipt.state))) == ["succeeded", "succeeded"]


@pytest.mark.parametrize("metadata", [{}, {"qualification": False}, {"qualification": None}])
def test_reconcile_preserves_inline_connection_diagnostic_lifecycle(db_session, metadata):
    run = AITaskRun(
        task_type="connection_test", status="running", trigger_source="manual",
        metadata_json=metadata, updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(run)
    db_session.commit()
    _reconcile(db_session)
    assert run.status == "running" and run.finished_at is None
    assert db_session.get(AIWorkflowDispatch, run.id) is None


def test_continuation_recovery_retains_authorized_progress_budget_and_provider(
    client, auth_headers, db_session, continuation,
):
    item, body = continuation
    response = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["run_id"])
    row = db_session.get(AIArticleContinuation, run_id)
    authorization = copy.deepcopy(row.authorization_encrypted)
    progress = copy.deepcopy(db_session.get(ItemAIEnrichment, item.id).extraction_progress_json)
    run = _lose_unsent_worker(db_session, run_id)
    selection = copy.deepcopy(run.metadata_json["provider_selection"])
    _reconcile(db_session)
    assert run.status == "queued" and run.celery_task_id != "lost-delivery"
    assert run.metadata_json["extraction_continuation"] is True
    assert run.metadata_json["provider_selection"] == selection
    assert row.authorization_encrypted == authorization
    assert row.expected_progress_digest == body["progress_revision"]
    assert row.section_limit == 16 and row.token_budget == 128000
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == progress
    dispatch = db_session.get(AIWorkflowDispatch, run_id)
    assert dispatch.state == "pending"
    assert dispatch.task_name == "app.tasks.feed_tasks.generate_item_ai_enrichment"
    assert dispatch.payload_json["task_run_id"] == str(run_id)
