import copy
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.schemas.ai_extraction import StructuredExtractionResponse
from app.services.ai_extraction import (
    ExtractionValidationError,
    build_verified_extraction,
    extraction_input,
    item_extraction_response,
    validate_structured_extraction,
)
from app.services.ai_output_validation import validate_feature_completion
from app.services.ai_prompting import build_item_enrichment_messages
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError


ARTICLE = "Researchers reported CloudBear used Ember malware. Vendor documentation is at docs.example.org. ProductX versions 4.1 and 4.2 were affected."


def extraction_payload():
    return {
        "entities": [
            {"id": "e1", "kind": "actor", "name": "CloudBear", "description": "Source-reported actor.",
             "assertion": "reported", "evidence": [{"source": "article_text", "quote": "Researchers reported CloudBear used Ember malware."}]},
            {"id": "e2", "kind": "malware", "name": "Ember", "description": "Source-reported malware.",
             "assertion": "reported", "evidence": [{"source": "article_text", "quote": "CloudBear used Ember malware."}]},
            {"id": "e3", "kind": "indicator", "name": "docs.example.org", "description": "Vendor documentation reference.",
             "assertion": "reported", "indicator_role": "reference",
             "evidence": [{"source": "article_text", "quote": "Vendor documentation is at docs.example.org."}]},
            {"id": "e4", "kind": "product", "name": "ProductX", "description": "Affected product.",
             "assertion": "reported", "versions": ["4.1", "4.2"],
             "evidence": [{"source": "article_text", "quote": "ProductX versions 4.1 and 4.2 were affected."}]},
        ],
        "relationships": [
            {"source_entity_id": "e1", "target_entity_id": "e2", "relationship": "uses",
             "description": "The source attributes malware use to this actor.", "assertion": "reported",
             "evidence": [{"source": "article_text", "quote": "CloudBear used Ember malware."}]},
        ],
        "information_gaps": ["No independent corroboration was supplied."],
    }


def source_messages(article_text=ARTICLE):
    return [{"role": "user", "content": json.dumps({"task": "item_enrichment", "item": {
        "title": "Research report", "summary": None, "article_text": article_text,
    }})}]


def test_extraction_keeps_reference_roles_and_verified_offsets_with_revision():
    article_id = uuid.uuid4()
    retrieved_at = datetime(2026, 9, 16, tzinfo=timezone.utc)
    verified = build_verified_extraction(
        extraction_payload(), messages=source_messages(), article_id=article_id,
        article_retrieved_at=retrieved_at, source_version=7, source_hash="a" * 64,
        article_text_length=len(ARTICLE),
    )
    parsed = StructuredExtractionResponse.model_validate(verified)
    assert parsed.article_id == article_id
    assert parsed.source_version == 7
    assert parsed.article_retrieved_at == retrieved_at
    assert not parsed.truncated
    assert parsed.entities[2].indicator_role == "reference"
    assert parsed.entities[3].versions == ["4.1", "4.2"]
    assert len(parsed.input_sha256) == 64
    for entity in parsed.entities:
        for evidence in entity.evidence:
            assert ARTICLE[evidence.start:evidence.end] == evidence.quote


@pytest.mark.parametrize("change", [
    lambda p: p["entities"][0]["evidence"][0].update(quote="Invented source statement."),
    lambda p: p["entities"][0].update(name="InventedActor"),
    lambda p: p["entities"][3].update(versions=["9.9"]),
    lambda p: p["entities"][2].pop("indicator_role"),
    lambda p: p["entities"][2].update(indicator_role="malicious"),
    lambda p: p["entities"][0].update(assertion="confirmed"),
    lambda p: p["entities"][0].update(versions=["4.1"]),
    lambda p: p["entities"][0].update(indicator_role="unknown"),
    lambda p: p["entities"][1].update(id="e1"),
    lambda p: p["relationships"][0].update(target_entity_id="e99"),
    lambda p: p["relationships"][0].update(target_entity_id="e1"),
    lambda p: p["entities"][0]["evidence"][0].update(source="company_context"),
    lambda p: p["entities"][0]["evidence"][0].update(start=1),
    lambda p: p.update(entities=p["entities"] * 7),
    lambda p: p.update(information_gaps=["x"] * 9),
])
def test_invalid_extraction_fails_with_safe_diagnostics(change):
    payload = extraction_payload()
    change(payload)
    with pytest.raises(ExtractionValidationError) as caught:
        validate_structured_extraction(payload, source=extraction_input(source_messages()))
    assert "structured_extraction" in str(caught.value)
    assert "InventedActor" not in str(caught.value)
    assert "Invented source" not in str(caught.value)


def test_extraction_cannot_quote_unseen_tail_or_company_context():
    payload = extraction_payload()
    messages = source_messages(ARTICLE[:30])
    with pytest.raises(ExtractionValidationError):
        validate_structured_extraction(payload, source=extraction_input(messages))
    with pytest.raises(ExtractionValidationError):
        extraction_input([{"role": "system", "content": ARTICLE}])


def test_empty_extraction_and_explicit_inference_are_accepted():
    assert validate_structured_extraction({"entities": [], "relationships": []}, source={}) == {
        "entities": [], "relationships": [], "information_gaps": [],
    }
    payload = extraction_payload()
    payload["relationships"][0]["assertion"] = "inferred"
    verified = validate_structured_extraction(payload, source=extraction_input(source_messages()))
    assert verified["relationships"][0]["assertion"] == "inferred"


def test_extraction_validation_precedes_provider_success_and_keeps_usage_metadata():
    active = SimpleNamespace(summary_enabled=False, relevance_enabled=False, structured_extraction_enabled=True)
    completion = AICompletionResult(
        payload={"structured_extraction": extraction_payload()}, provider="openai_compatible", model="test",
        latency_ms=5, prompt_tokens=300, completion_tokens=80, total_tokens=380,
    )
    validate_feature_completion(active, feature_type="item_enrichment", completion=completion, messages=source_messages())
    completion.payload["structured_extraction"]["entities"][0]["name"] = "FabricatedActor"
    with pytest.raises(AIIntegrationError) as caught:
        validate_feature_completion(active, feature_type="item_enrichment", completion=completion, messages=source_messages())
    assert caught.value.failure_category == "invalid_output"
    assert caught.value.provider_io_outcome == "response_received"
    assert caught.value.total_tokens == 380
    assert "FabricatedActor" not in str(caught.value)


def test_disabled_extraction_preserves_legacy_provider_contract():
    active = SimpleNamespace(summary_enabled=True, relevance_enabled=False, structured_extraction_enabled=False)
    completion = AICompletionResult(
        payload={"summary_text": "A legacy summary."}, provider="openai_compatible", model="test",
        latency_ms=1, prompt_tokens=1, completion_tokens=1, total_tokens=2,
    )
    validate_feature_completion(active, feature_type="item_enrichment", completion=completion)


def test_extraction_prompt_is_bounded_and_separates_facts_from_profile():
    active = SimpleNamespace(
        summary_enabled=False, relevance_enabled=False, structured_extraction_enabled=True,
        item_enrichment_system_prompt=None, global_instructions=None, item_summary_instructions=None,
        relevance_instructions=None, company_name="GlobalCo", company_industry=None, company_regions=[],
        company_stack=[], company_priority_topics=[], company_keywords=[], company_exclusions=[], company_profile_text=None,
    )
    item = SimpleNamespace(title="Research", summary="Summary", canonical_url=None, url="https://example.org", published_at=None)
    article = SimpleNamespace(text="Research text.\n" * 2000)
    messages = build_item_enrichment_messages(active, item=item, article=article, classification=None, feed=None, tag_names=[])
    prompt = json.loads(messages[1]["content"])
    assert len(prompt["item"]["article_text"]) == 8000
    assert "structured_extraction" in prompt["requested_output"]
    assert "independently of any organization or team profile" in messages[0]["content"]
    assert "untrusted source data" in messages[0]["content"]
    disabled = copy.copy(active)
    disabled.structured_extraction_enabled = False
    legacy = build_item_enrichment_messages(disabled, item=item, article=article, classification=None, feed=None, tag_names=[])
    assert "structured_extraction" not in json.loads(legacy[1]["content"])["requested_output"]


@pytest.mark.parametrize("change", ["failure", "pending", "article", "revision", "time", "hash", "purged", "missing"])
def test_historical_extraction_is_marked_stale_after_source_or_execution_changes(change):
    article_id = uuid.uuid4()
    retrieved_at = datetime(2026, 9, 16, tzinfo=timezone.utc)
    value = build_verified_extraction(extraction_payload(), messages=source_messages(), article_id=article_id,
                                     article_retrieved_at=retrieved_at, source_version=1, source_hash="a" * 64,
                                     article_text_length=len(ARTICLE))
    item = SimpleNamespace(id=uuid.uuid4(), classification_required_version=1)
    article = SimpleNamespace(id=article_id, retrieved_at=retrieved_at, text=ARTICLE)
    enrichment = SimpleNamespace(status="ready", source_hash="a" * 64, structured_extraction_json=value)
    assert item_extraction_response(enrichment, item=item, article=article)[1] is False
    if change in {"failure", "pending"}:
        enrichment.status = "error" if change == "failure" else "pending"
    elif change == "article":
        article.id = uuid.uuid4()
    elif change == "revision":
        item.classification_required_version += 1
    elif change == "time":
        article.retrieved_at = retrieved_at.replace(year=2027)
    elif change == "hash":
        enrichment.source_hash = "b" * 64
    elif change == "purged":
        article.text = None
    elif change == "missing":
        article = None
    response, stale = item_extraction_response(enrichment, item=item, article=article)
    assert stale
    assert response.entities[0].name == "CloudBear"


def test_legacy_and_invalid_stored_extractions_do_not_break_item_responses(caplog, monkeypatch):
    # Alembic's logging setup can disable existing loggers in earlier tests.
    monkeypatch.setattr("app.services.ai_extraction.logger.disabled", False)
    item = SimpleNamespace(id=uuid.uuid4())
    assert item_extraction_response(None, item=item, article=None) == (None, False)
    empty = SimpleNamespace(structured_extraction_json=None)
    assert item_extraction_response(empty, item=item, article=None) == (None, False)
    corrupted = SimpleNamespace(structured_extraction_json={"private_provider_text": "Do not log this"})
    assert item_extraction_response(corrupted, item=item, article=None) == (None, True)
    assert "ai_extraction_stored_result_invalid" in caplog.text
    assert "Do not log this" not in caplog.text
