"""An explicit continuation recovers only provider calls proven not sent."""
import copy
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services import ai_ops
from app.services.ai_article_continuation import progress_digest
from app.services.ai_integration import run_item_ai_enrichment
from app.services.ai_provider_attempts import reconcile_ai_provider_attempt
from app.services.ai_provider_client import AIIntegrationError
from tests.unit.test_ai_extraction_workflow import extraction_item as extraction_item, completion


@pytest.fixture
def interrupted_sections(db_session, extraction_item, monkeypatch):
    item, article = extraction_item
    article.text = "Ordinary introduction. " * 850
    run = ai_ops.queue_ai_task_run(db_session, task_type="item_enrichment", trigger_source="manual", item_id=item.id)
    ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id="original-worker")
    db_session.commit()
    calls = []

    def provider(_active, **kwargs):
        section = json.loads(kwargs["messages"][-1]["content"])["extraction_section"]["index"]
        calls.append(section)
        if section == 1:
            raise AIIntegrationError("Delivery requires reconciliation", provider_io_outcome="ambiguous", retryable=False)
        return completion({"entities": [], "relationships": []})

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    assert result.status == "error"
    ai_ops.finish_ai_task_run(db_session, run_id=run.id, status="error", reason="provider_error")
    db_session.commit()
    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id).order_by(AIProviderAttemptReceipt.created_at)))
    receipt = next(row for row in receipts if row.state == "ambiguous")
    progress = copy.deepcopy(db_session.get(ItemAIEnrichment, item.id).extraction_progress_json)
    assert [section["status"] for section in progress["sections"]] == ["completed", "started", "pending"]
    assert receipt.request_fingerprint == progress["sections"][1]["request_fingerprint"]
    monkeypatch.setattr("app.api.routes.ai_article_continuation.publish_accepted_assessment", lambda *_a: None)
    return item, run, receipt, progress, calls


def test_reconcile_then_authorize_continuation_preserves_completed_calls_and_receipt_history(
    client, auth_headers, db_session, interrupted_sections, monkeypatch,
):
    item, original, receipt, progress, calls = interrupted_sections
    body = {"request_id": str(uuid.uuid4()), "progress_revision": progress_digest(progress)}
    blocked = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert blocked.status_code == 409 and "Reconcile" in blocked.text
    reconcile_ai_provider_attempt(db_session, receipt_id=receipt.id, expected_revision=receipt.revision,
        action="confirmed_not_sent", actor_user_id=uuid.uuid4())
    db_session.commit()
    accepted = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert accepted.status_code == 202, accepted.text
    again = client.post(f"/ai/articles/{item.id}/continue", json=body, headers=auth_headers["admin"])
    assert again.json()["run_id"] == accepted.json()["run_id"]
    run_id = uuid.UUID(accepted.json()["run_id"])
    ai_ops.start_ai_task_run(db_session, run_id=run_id, celery_task_id="continuation-worker")
    db_session.commit()

    def provider(_active, **kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"])["extraction_section"]["index"])
        return completion({"entities": [], "relationships": []})

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run_id)
    assert result.status == "ready", result.reason
    current = result.enrichment.extraction_progress_json
    assert calls == [0, 1, 1, 2]
    assert current["sections"][0] == progress["sections"][0]
    assert current["sections"][1]["previous_attempt_receipts"] == [str(receipt.id)]
    assert progress["reserved_tokens"] < current["reserved_tokens"] <= current["token_budget"]
    assert current["task_run_id"] == str(run_id)
    assert result.enrichment.structured_extraction_json["coverage"]["uncovered_chars"] == 0
    db_session.refresh(receipt)
    assert receipt.state == "failed" and receipt.reconciliation_action == "confirmed_not_sent"
    assert receipt.task_run_id_snapshot == original.id
    assert db_session.get(AITaskRun, run_id).metadata_json["provider_selection"] == original.metadata_json["provider_selection"]


@pytest.mark.parametrize("change", [
    "acknowledged", "missing_receipt", "wrong_fingerprint", "wrong_item", "active_run", "voided_active_run", "succeeded",
])
def test_continuation_cannot_infer_safe_replay_from_unrelated_or_ambiguous_receipts(
    client, auth_headers, db_session, interrupted_sections, change,
):
    item, original, receipt, progress, _ = interrupted_sections
    if change == "acknowledged":
        reconcile_ai_provider_attempt(db_session, receipt_id=receipt.id, expected_revision=receipt.revision,
            action="acknowledged_may_have_sent", actor_user_id=uuid.uuid4())
    elif change == "succeeded":
        receipt.state, receipt.io_outcome = "succeeded", "response_received"
    elif change == "voided_active_run":
        receipt.state, receipt.io_outcome, receipt.retryable = "voided", "not_sent", True
        receipt.pre_io_failure_count = 1
        receipt.last_pre_io_failure_at = datetime.now(timezone.utc)
    else:
        reconcile_ai_provider_attempt(db_session, receipt_id=receipt.id, expected_revision=receipt.revision,
            action="confirmed_not_sent", actor_user_id=uuid.uuid4())
    if change == "missing_receipt":
        db_session.delete(receipt)
    elif change == "wrong_fingerprint":
        receipt.request_fingerprint = "f" * 64
    elif change == "wrong_item":
        receipt.resource_id = uuid.uuid4()
    elif change in {"active_run", "voided_active_run"}:
        original.status = "running"
        original.finished_at = None
    db_session.commit()
    response = client.post(f"/ai/articles/{item.id}/continue", headers=auth_headers["admin"], json={
        "request_id": str(uuid.uuid4()), "progress_revision": progress_digest(progress),
    })
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("change", ["receipt_deleted", "prior_run_reopened"])
def test_worker_rechecks_receipt_and_terminal_task_before_modifying_authorized_checkpoints(
    client, auth_headers, db_session, interrupted_sections, monkeypatch, change,
):
    item, original, receipt, progress, calls = interrupted_sections
    reconcile_ai_provider_attempt(db_session, receipt_id=receipt.id, expected_revision=receipt.revision,
        action="confirmed_not_sent", actor_user_id=uuid.uuid4())
    db_session.commit()
    accepted = client.post(f"/ai/articles/{item.id}/continue", headers=auth_headers["admin"], json={
        "request_id": str(uuid.uuid4()), "progress_revision": progress_digest(progress),
    })
    assert accepted.status_code == 202, accepted.text
    if change == "receipt_deleted":
        db_session.delete(receipt)
    else:
        original.status, original.finished_at = "running", None
    run_id = uuid.UUID(accepted.json()["run_id"])
    ai_ops.start_ai_task_run(db_session, run_id=run_id, celery_task_id="continuation-worker")
    db_session.commit()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: pytest.fail("Missing proof must block send"))
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run_id)
    assert result.status == "error"
    assert ("Reconcile" if change == "receipt_deleted" else "not settled") in result.enrichment.error
    assert result.enrichment.extraction_progress_json == progress
    assert calls == [0, 1]
