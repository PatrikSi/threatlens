"""Repair sweeps and explicit synthesis recovery preserve paid-call boundaries."""
import copy
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.services.ai_article_continuation import progress_digest
from app.services.ai_config import get_or_create_ai_settings, load_active_ai_settings
from app.services.ai_integration import run_item_ai_enrichment
from app.services.ai_ops import finish_ai_task_run, queue_ai_task_run, start_ai_task_run
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.tasks import feed_tasks
from app.tasks.feed_task_dispatchers import dispatch_items_missing_ai_enrichment
from tests.unit.test_ai_extraction_workflow import extraction_item as extraction_item


def result():
    return AICompletionResult(payload={"summary_text": "A source observation.",
        "structured_extraction": {"entities": [], "relationships": []}}, provider="openai_compatible",
        model="test-model", latency_ms=1, prompt_tokens=100, completion_tokens=20, total_tokens=120)


@pytest.fixture
def section_job(db_session, extraction_item, monkeypatch):
    item, article = extraction_item
    now = datetime.now(timezone.utc)
    article.text = "x" * 17000
    item.published_at = item.first_seen_at = now
    get_or_create_ai_settings(db_session).auto_enrich_new_items = True
    db_session.add(ItemClassification(item_id=item.id, primary_category="other", secondary_categories=[],
        confidence=.5, scores_json={}, matched_terms_json={}, source_hash="x", rules_version="test", classified_at=now))
    db_session.commit()
    run = queue_ai_task_run(db_session, task_type="item_enrichment", trigger_source="automatic", item_id=item.id)
    start_ai_task_run(db_session, run_id=run.id, worker_name="test")
    db_session.commit()
    monkeypatch.setattr("app.api.routes.ai_article_continuation.publish_accepted_assessment", lambda *_a: None)
    return item, article, run


def finish(db, item, run, outcome):
    finish_ai_task_run(db, run_id=run.id, status=outcome.status, reason=outcome.reason)
    db.get(ItemAIEnrichment, item.id).updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()


def sweep(db, monkeypatch):
    @contextmanager
    def session_factory():
        yield db
    monkeypatch.setattr(feed_tasks, "db_session", session_factory)
    monkeypatch.setattr("app.services.ai_workflow_publication.publish_ai_workflow", lambda *_a, **_kw: None)
    return dispatch_items_missing_ai_enrichment(db_session_factory=session_factory,
        settings=SimpleNamespace(dispatch_items_failed_ai_enrichment_after_seconds=3600,
            dispatch_items_missing_ai_enrichment_batch_size=100),
        load_active_ai_settings=load_active_ai_settings, reconcile_stale_ai_runs=lambda _db: None,
        auto_enrich_cutoff=lambda now: now - timedelta(days=1), auto_enrich_window_hours=lambda: 24,
        safe_queue_item_ai_enrichment_run=feed_tasks._safe_queue_item_ai_enrichment_run, trigger_source="automatic")


def interrupted(db, item, run, monkeypatch, *, outcome="response_received"):
    calls = []
    def provider(_active, **kwargs):
        index = json.loads(kwargs["messages"][-1]["content"])["extraction_section"]["index"]
        calls.append(index)
        if index == 1:
            raise AIIntegrationError("Unavailable section", provider_io_outcome=outcome, retryable=False)
        return result()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    failed = run_item_ai_enrichment(db, item_id=item.id, task_run_id=run.id)
    assert failed.status == "error"
    finish(db, item, run, failed)
    return calls, copy.deepcopy(failed.enrichment.extraction_progress_json)


@pytest.mark.parametrize("outcome", ["response_received", "ambiguous", "not_sent"])
def test_sweep_does_not_replace_terminal_paid_work(db_session, section_job, monkeypatch, outcome):
    item, _, run = section_job
    calls, progress = interrupted(db_session, item, run, monkeypatch, outcome=outcome)
    assert sweep(db_session, monkeypatch) == {"queued": 0}
    assert sweep(db_session, monkeypatch) == {"queued": 0}
    assert calls == [0, 1]
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == progress
    assert list(db_session.scalars(select(AITaskRun.id).where(AITaskRun.item_id == item.id))) == [run.id]


@pytest.mark.parametrize("change", [None, "source", "shorter_source", "disabled_extraction", "provider", "cancel"])
def test_safe_repair_reuses_logical_run_and_never_replays_completed_sections(
    db_session, section_job, monkeypatch, change,
):
    item, article, run = section_job
    calls, progress = interrupted(db_session, item, run, monkeypatch, outcome="not_sent")
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id, AIProviderAttemptReceipt.state == "failed"))
    # A durable pre-I/O void still has a retry allowance in this same operation.
    receipt.state, receipt.retryable = "voided", True
    receipt.pre_io_failure_count = 1
    receipt.last_pre_io_failure_at = datetime.now(timezone.utc)
    if change == "cancel":
        run.metadata_json = {**run.metadata_json, "cancel_requested_at": datetime.now(timezone.utc).isoformat()}
    db_session.commit()
    assert sweep(db_session, monkeypatch) == {"queued": 0 if change == "cancel" else 1}
    if change == "cancel":
        assert calls == [0, 1]
        return
    db_session.refresh(run)
    assert run.status == "queued" and run.finished_at is None
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == progress
    if change == "source":
        article.text += " changed evidence"
        article.retrieved_at += timedelta(seconds=1)
    elif change == "shorter_source":
        article.text = "Short replacement evidence."
    elif change == "disabled_extraction":
        settings = get_or_create_ai_settings(db_session)
        settings.structured_extraction_enabled = False
        settings.summary_enabled = True
    elif change == "provider":
        get_or_create_ai_settings(db_session).model = "another-model"
    start_ai_task_run(db_session, run_id=run.id, celery_task_id=run.celery_task_id)
    db_session.commit()
    def provider(_active, **kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"])["extraction_section"]["index"])
        return result()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    repaired = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=run.id)
    assert repaired.status == ("ready" if change is None else "error")
    assert calls == ([0, 1, 1, 2] if change is None else [0, 1])
    current = db_session.get(ItemAIEnrichment, item.id).extraction_progress_json
    assert current["sections"][0] == progress["sections"][0]
    assert current["task_run_id"] == str(run.id)
    assert current["token_budget"] == progress["token_budget"]
    if change is None:
        assert current["reserved_tokens"] > progress["reserved_tokens"]
    else:
        assert current == progress


@pytest.mark.parametrize("outcome", ["not_sent", "legacy_not_sent", "insufficient_budget", "ambiguous", "invalid_synthesis"])
def test_synthesis_failure_publishes_verified_sections_and_only_safe_explicit_recovery_sends(
    db_session, section_job, monkeypatch, client, auth_headers, outcome,
):
    item, _, run = section_job
    get_or_create_ai_settings(db_session).summary_enabled = True
    db_session.commit()
    calls = []
    def provider(_active, **kwargs):
        body = json.loads(kwargs["messages"][-1]["content"])
        if body["task"] == "item_section_synthesis":
            calls.append("synthesis")
            if outcome == "invalid_synthesis":
                return result()
            raise AIIntegrationError("Synthesis unavailable", provider_io_outcome=("not_sent" if outcome in {"legacy_not_sent", "insufficient_budget"} else outcome), retryable=False)
        calls.append(body["extraction_section"]["index"])
        return result()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    enriched = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=run.id)
    assert enriched.status == "ready", enriched.enrichment.error
    assert enriched.enrichment.structured_extraction_json["coverage"]["synthesis_status"] == "failed"
    assert enriched.enrichment.summary_text.startswith("Section 1:")
    assert "combined summary" in enriched.enrichment.structured_extraction_json["information_gaps"][0]
    finish(db_session, item, run, enriched)
    progress = copy.deepcopy(enriched.enrichment.extraction_progress_json)
    if outcome == "legacy_not_sent":
        progress["synthesis"].pop("request_fingerprint")
        progress["synthesis"]["status"] = "started"
        enriched.enrichment.extraction_progress_json = progress
        enriched.enrichment.status = "error"
        enriched.enrichment.structured_extraction_json = None
        enriched.enrichment.summary_text = None
        db_session.commit()
    elif outcome == "insufficient_budget":
        progress["reserved_tokens"] = progress["token_budget"] - 1
        enriched.enrichment.extraction_progress_json = progress
        db_session.commit()
    body = {"request_id": str(uuid.uuid4()), "progress_revision": progress_digest(progress)}
    response = client.post(f"/ai/articles/{item.id}/continue", headers=auth_headers["admin"], json=body)
    if outcome == "insufficient_budget":
        assert response.status_code == 409 and "cannot fit synthesis recovery" in response.text, response.text
        assert calls == [0, 1, 2, "synthesis"]
        return
    if outcome not in {"not_sent", "legacy_not_sent"}:
        assert response.status_code == 409 and "Reconcile" in response.text, response.text
        assert calls == [0, 1, 2, "synthesis"]
        return
    assert response.status_code == 202, response.text
    assert response.json()["section_limit"] == progress["section_limit"]
    assert response.json()["token_budget"] == progress["token_budget"]
    assert client.post(f"/ai/articles/{item.id}/continue", headers=auth_headers["admin"], json=body).json() == response.json()
    recovery_id = uuid.UUID(response.json()["run_id"])
    start_ai_task_run(db_session, run_id=recovery_id, worker_name="synthesis-recovery")
    db_session.commit()
    def recovered_provider(_active, **kwargs):
        body = json.loads(kwargs["messages"][-1]["content"])
        assert body["task"] == "item_section_synthesis"
        calls.append("synthesis")
        completion = result()
        completion.payload["summary_text"] = "The source presents a research observation. [S1]"
        return completion
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", recovered_provider)
    recovered = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=recovery_id, force=True)
    assert recovered.status == "ready", recovered.enrichment.error
    current = recovered.enrichment.extraction_progress_json
    assert current["sections"] == progress["sections"]
    assert current["synthesis"]["status"] == "completed"
    assert current["summary_scope"] == "section_synthesis"
    assert calls == [0, 1, 2, "synthesis", "synthesis"]
    assert current["reserved_tokens"] > progress["reserved_tokens"]


@pytest.mark.parametrize("revoke", [False, True])
def test_repair_of_enlarged_continuation_preserves_plan_and_rechecks_accepting_authority(
    db_session, section_job, monkeypatch, client, auth_headers, revoke,
):
    from app.models.api_token import ApiToken
    from app.models.ai_article_continuation import AIArticleContinuation
    item, _, original = section_job
    _, initial_progress = interrupted(db_session, item, original, monkeypatch, outcome="not_sent")
    accepted = client.post(f"/ai/articles/{item.id}/continue", headers=auth_headers["admin"], json={
        "request_id": str(uuid.uuid4()), "progress_revision": progress_digest(initial_progress),
    })
    assert accepted.status_code == 202, accepted.text
    continuation_id = uuid.UUID(accepted.json()["run_id"])
    continuation = db_session.get(AITaskRun, continuation_id)
    start_ai_task_run(db_session, run_id=continuation_id)
    db_session.commit()
    calls, progress = interrupted(db_session, item, continuation, monkeypatch, outcome="not_sent")
    assert progress["section_limit"] == 16 and progress["token_budget"] == 128000
    assert calls == [1]
    # Terminal failure cannot silently authorize a fresh plan on a later sweep.
    assert sweep(db_session, monkeypatch) == {"queued": 0}
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == progress
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == continuation_id, AIProviderAttemptReceipt.state == "failed"))
    receipt.state, receipt.retryable = "voided", True
    receipt.pre_io_failure_count = 1
    receipt.last_pre_io_failure_at = datetime.now(timezone.utc)
    continuation.metadata_json = {**continuation.metadata_json, "automatic_recovery_blocked": False}
    db_session.commit()
    assert sweep(db_session, monkeypatch) == {"queued": 1}
    assert db_session.get(AIArticleContinuation, continuation_id).section_limit == 16
    if revoke:
        token = db_session.scalar(select(ApiToken).where(ApiToken.name == "pytest-auth-admin@example.com"))
        assert token is not None
        token.revoked_at = datetime.now(timezone.utc)
    start_ai_task_run(db_session, run_id=continuation_id, celery_task_id=continuation.celery_task_id)
    db_session.commit()
    def provider(_active, **kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"])["extraction_section"]["index"])
        return result()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    recovered = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=continuation_id, force=True)
    assert recovered.status == ("error" if revoke else "ready")
    assert calls == ([1] if revoke else [1, 1, 2])
    current = recovered.enrichment.extraction_progress_json
    assert current["section_limit"] == 16 and current["token_budget"] == 128000
    assert current["sections"][0] == progress["sections"][0]


@pytest.mark.parametrize("stop", ["cancel", "source", "claim", "policy"])
def test_optional_synthesis_failure_cannot_publish_after_invalidation(db_session, section_job, monkeypatch, stop):
    from app.services.ai_egress_data_policy import AIEgressPolicyError
    from sqlalchemy import update
    item, article, run = section_job
    get_or_create_ai_settings(db_session).summary_enabled = True
    db_session.commit()
    def provider(_active, **kwargs):
        body = json.loads(kwargs["messages"][-1]["content"])
        if body["task"] != "item_section_synthesis":
            return result()
        if stop == "cancel":
            run.metadata_json = {**run.metadata_json, "cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        elif stop == "source":
            article.retrieved_at += timedelta(seconds=1)
            item.classification_required_version += 1
        elif stop == "claim":
            db_session.execute(update(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id).values(
                updated_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        elif stop == "policy":
            raise AIEgressPolicyError("Destination access revoked", retryable=False)
        db_session.flush()
        raise AIIntegrationError("Synthesis unavailable", provider_io_outcome="not_sent", retryable=False)
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    enriched = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=run.id)
    assert enriched.status != "ready"
    assert enriched.enrichment.structured_extraction_json is None


def test_short_article_repair_can_resume_proven_pre_io_failure(db_session, section_job, monkeypatch):
    item, article, run = section_job
    article.text = "Short article evidence."
    get_or_create_ai_settings(db_session).summary_enabled = True
    db_session.commit()
    def unavailable(*_args, **_kwargs):
        raise AIIntegrationError("Not delivered", provider_io_outcome="not_sent", retryable=False)
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", unavailable)
    failed = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=run.id)
    assert failed.status == "error"
    finish(db_session, item, run, failed)
    assert failed.enrichment.extraction_progress_json is None
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id))
    receipt.state, receipt.retryable = "voided", True
    receipt.pre_io_failure_count = 1
    receipt.last_pre_io_failure_at = datetime.now(timezone.utc)
    db_session.commit()
    assert sweep(db_session, monkeypatch) == {"queued": 1}
    start_ai_task_run(db_session, run_id=run.id, celery_task_id=run.celery_task_id)
    db_session.commit()
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_a, **_kw: result())
    recovered = run_item_ai_enrichment(db_session, item_id=item.id, task_run_id=run.id)
    assert recovered.status == "ready"
    assert recovered.enrichment.extraction_progress_json is None


def test_automatic_recovery_delivery_budget_cannot_be_reset_by_another_sweep(db_session, section_job, monkeypatch):
    item, _, run = section_job
    calls, progress = interrupted(db_session, item, run, monkeypatch, outcome="not_sent")
    run.metadata_json = {**run.metadata_json, "automatic_recovery_count": 3}
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id, AIProviderAttemptReceipt.state == "failed"))
    receipt.state, receipt.retryable = "voided", True
    receipt.pre_io_failure_count = 1
    receipt.last_pre_io_failure_at = datetime.now(timezone.utc)
    db_session.commit()
    assert sweep(db_session, monkeypatch) == {"queued": 0}
    assert sweep(db_session, monkeypatch) == {"queued": 0}
    assert calls == [0, 1]
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == progress
    assert run.metadata_json["automatic_recovery_count"] == 3
