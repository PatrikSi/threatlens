import copy
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.ai import AISettingsUpdate
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_integration import run_item_ai_enrichment
from app.services.ai_provider_client import AICompletionResult
from tests.unit.test_ai_extraction import ARTICLE, extraction_payload


@pytest.fixture()
def extraction_item(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    feed = Feed(name="Research", url="https://example.org/feed", enabled=True)
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id, source_guid="extraction-item", url="https://example.org/article",
        title="Research report", summary="Source report.", dedupe_key="extraction-item",
        content_hash="a" * 64, status="content_fetched",
    )
    db_session.add(item)
    db_session.flush()
    article = Article(item_id=item.id, final_url=item.url, http_status=200, text=ARTICLE)
    db_session.add(article)
    settings = get_or_create_ai_settings(db_session)
    apply_ai_settings_update(settings, AISettingsUpdate(
        base_url="http://localhost:11434/v1", model="test-model", request_max_retries=0,
        summary_enabled=False, relevance_enabled=False, structured_extraction_enabled=True,
    ))
    db_session.commit()
    return item, article


def completion(payload=None):
    return AICompletionResult(
        payload={"structured_extraction": extraction_payload() if payload is None else payload},
        provider="openai_compatible", model="test-model", latency_ms=5,
        prompt_tokens=300, completion_tokens=80, total_tokens=380,
    )


def test_extraction_only_runs_and_invalid_retry_preserves_last_verified_result(
    db_session, extraction_item, monkeypatch,
):
    item, article = extraction_item
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_args, **_kwargs: completion())
    initial = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert initial.status == "ready"
    assert initial.enrichment.summary_text is None
    verified = copy.deepcopy(initial.enrichment.structured_extraction_json)
    provenance = copy.deepcopy(initial.enrichment.result_provenance_json)
    assert verified["article_id"] == str(article.id)
    assert verified["source_version"] == item.classification_required_version

    invalid = extraction_payload()
    invalid["entities"][0]["name"] = "InventedActor"
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_args, **_kwargs: completion(invalid))
    failed = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert failed.status == "error"
    assert failed.enrichment.structured_extraction_json == verified
    assert failed.enrichment.result_provenance_json == provenance
    assert "InventedActor" not in failed.enrichment.error
    usages = db_session.scalars(select(AIUsageEvent).order_by(AIUsageEvent.created_at)).all()
    assert [usage.success for usage in usages] == [True, False]
    assert usages[-1].failure_category == "invalid_output"


def test_source_refresh_during_provider_call_keeps_original_extraction_revision(
    db_session, extraction_item, monkeypatch,
):
    item, article = extraction_item
    original_article_time = article.retrieved_at.isoformat()
    original_version = item.classification_required_version

    def refresh_source(*_args, **_kwargs):
        article.text = "Updated article no longer contains the prior claims."
        article.retrieved_at += timedelta(seconds=1)
        item.classification_required_version += 1
        db_session.flush()
        return completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", refresh_source)
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert result.status == "ready"
    verified = result.enrichment.structured_extraction_json
    assert verified["source_version"] == original_version
    assert verified["article_retrieved_at"] == original_article_time
    assert item.classification_required_version == original_version + 1


def test_disabling_extraction_preserves_historical_evidence_during_summary_refresh(
    db_session, extraction_item, monkeypatch, client, auth_headers,
):
    item, _article = extraction_item
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_args, **_kwargs: completion())
    first = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    previous = copy.deepcopy(first.enrichment.structured_extraction_json)
    settings = get_or_create_ai_settings(db_session)
    settings.structured_extraction_enabled = False
    settings.summary_enabled = True
    db_session.commit()

    def summary_only(_active, *, messages):
        assert "structured_extraction" not in json.loads(messages[1]["content"])["requested_output"]
        return replace(completion(), payload={"summary_text": "The source reports the actor and affected product."})

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", summary_only)
    refreshed = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert refreshed.status == "ready"
    assert refreshed.enrichment.summary_text == "The source reports the actor and affected product."
    assert refreshed.enrichment.structured_extraction_json == previous
    response = client.get(f"/items/{item.id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    assert response.json()["ai_insight"]["structured_extraction"]["entities"] == previous["entities"]
    assert response.json()["ai_insight"]["structured_extraction_stale"] is True


def test_superseded_enrichment_claim_cannot_publish_extraction(
    db_session, extraction_item, monkeypatch,
):
    item, _article = extraction_item
    replacement = {"replacement_marker": str(uuid.uuid4())}

    def replace_claim(*_args, **_kwargs):
        db_session.execute(update(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item.id).values(
            status="ready", updated_at=datetime.now(timezone.utc) + timedelta(seconds=1),
            structured_extraction_json=replacement,
        ))
        return completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", replace_claim)
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert result.status == "skipped"
    assert result.reason == "stale_result_discarded"
    assert result.enrichment.structured_extraction_json == replacement


def test_item_api_exposes_shared_extraction_and_server_freshness(
    db_session, extraction_item, monkeypatch, client, auth_headers,
):
    item, _article = extraction_item
    monkeypatch.setattr("app.services.ai_integration._call_ai_json", lambda *_args, **_kwargs: completion())
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    response = client.get(f"/items/{item.id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    insight = response.json()["ai_insight"]
    assert insight["structured_extraction"]["entities"][2]["indicator_role"] == "reference"
    assert insight["structured_extraction_stale"] is False
    result.enrichment.status = "error"
    db_session.commit()
    response = client.get(f"/items/{item.id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    assert response.json()["ai_insight"]["structured_extraction_stale"] is True


def test_legacy_settings_save_preserves_new_toggles_and_explicit_disable_works(
    db_session, extraction_item, client, auth_headers,
):
    settings = get_or_create_ai_settings(db_session)
    settings.hunt_suggestions_enabled = True
    db_session.commit()
    legacy_payload = {"base_url": "http://localhost:11434/v1", "model": "test-model"}
    response = client.put("/ai/settings", json=legacy_payload, headers=auth_headers["admin"])
    assert response.status_code == 200
    assert response.json()["structured_extraction_enabled"] is True
    assert response.json()["hunt_suggestions_enabled"] is True
    assert "shared source intelligence" in response.json()["prompt_previews"]["item_enrichment"]["system_prompt"]
    response = client.put("/ai/settings", json={
        **legacy_payload, "structured_extraction_enabled": False, "hunt_suggestions_enabled": False,
    }, headers=auth_headers["admin"])
    assert response.status_code == 200
    assert response.json()["structured_extraction_enabled"] is False
    assert response.json()["hunt_suggestions_enabled"] is False


def test_long_article_uses_bounded_runtime_calls_and_exposes_tail_evidence(
    db_session, extraction_item, monkeypatch, client, auth_headers,
):
    item, article = extraction_item
    article.text = "Ordinary introduction. " * 500 + ARTICLE
    db_session.commit()
    calls = []

    def extract_section(_active, *, messages, **_kwargs):
        source = json.loads(messages[-1]["content"])["item"]["article_text"]
        calls.append(source)
        return completion(extraction_payload() if ARTICLE in source else {"entities": [], "relationships": []})

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", extract_section)
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert result.status == "ready"
    assert len(calls) == 2
    assert result.enrichment.total_tokens == 760
    response = client.get(f"/items/{item.id}", headers=auth_headers["viewer"])
    assert response.status_code == 200
    insight = response.json()["ai_insight"]
    assert insight["structured_extraction"]["coverage"]["uncovered_chars"] == 0
    assert insight["extraction_progress"]["sections"][-1]["status"] == "completed"
    assert "completion" not in json.dumps(insight["extraction_progress"])
    assert insight["structured_extraction"]["entities"][0]["name"] == "CloudBear"


def test_long_article_stops_if_source_changes_before_section_checkpoint(db_session, extraction_item, monkeypatch):
    item, article = extraction_item
    article.text = "Ordinary introduction. " * 500 + ARTICLE
    db_session.commit()
    calls = []

    def refresh(_active, *, messages, **_kwargs):
        calls.append(messages)
        item.classification_required_version += 1
        db_session.flush()
        return completion({"entities": [], "relationships": []})

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", refresh)
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert result.status == "skipped"
    assert result.reason == "stale_result_discarded"
    assert len(calls) == 1
    assert result.enrichment.structured_extraction_json is None


@pytest.mark.parametrize("checkpoint_committed", [True, False])
def test_worker_recovery_reuses_completed_sections_but_blocks_uncheckpointed_success(
    db_session, extraction_item, monkeypatch, checkpoint_committed,
):
    from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
    from app.services import ai_ops
    from app.services.ai_workflow_recovery import recover_stale_workflow
    from app.services import ai_extraction_sections

    item, article = extraction_item
    article.text = "Ordinary introduction. " * 500 + ARTICLE
    run = ai_ops.queue_ai_task_run(db_session, task_type="item_enrichment", trigger_source="manual", item_id=item.id)
    ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id="first-worker")
    db_session.commit()
    calls = []
    original_save = ai_extraction_sections._save_progress

    def provider(_active, *, messages, **_kwargs):
        calls.append(messages)
        return completion({"entities": [], "relationships": []})

    def interrupted_save(db, **kwargs):
        sections = kwargs["progress"]["sections"]
        if sections[0]["status"] == "completed" and not checkpoint_committed:
            raise RuntimeError("worker crashed before durable section")
        original_save(db, **kwargs)
        if sections[0]["status"] == "completed":
            raise RuntimeError("worker crashed after durable section")

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    monkeypatch.setattr(ai_extraction_sections, "_save_progress", interrupted_save)
    with pytest.raises(RuntimeError, match="worker crashed"):
        run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.rollback()
    receipts = db_session.scalars(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id)).all()
    assert len(receipts) == 1 and receipts[0].state == "succeeded"
    if not checkpoint_committed:
        assert recover_stale_workflow(db_session, run) is None
        assert len(calls) == 1
        return
    assert recover_stale_workflow(db_session, run) == "guarded"
    db_session.commit()
    ai_ops.start_ai_task_run(db_session, run_id=run.id, celery_task_id=run.celery_task_id)
    db_session.commit()
    monkeypatch.setattr(ai_extraction_sections, "_save_progress", original_save)
    resumed = run_item_ai_enrichment(db_session, item_id=item.id, force=True, task_run_id=run.id)
    db_session.commit()
    assert resumed.status == "ready"
    assert len(calls) == 2  # First-section paid I/O is never repeated.
    assert resumed.enrichment.structured_extraction_json["coverage"]["uncovered_chars"] == 0
