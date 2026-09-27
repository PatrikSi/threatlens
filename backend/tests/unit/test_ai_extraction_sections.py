import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.ai_extraction import StructuredExtractionResponse
from app.services.ai_extraction import build_verified_extraction
from app.services.ai_extraction_sections import (
    MAX_SECTIONS, TOTAL_TOKEN_BUDGET, extraction_progress_response,
    merge_extractions, plan_sections, run_section_extraction,
    section_plan_fingerprint,
)
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_request_runtime import AITaskRunStoppedError
from tests.unit.test_ai_extraction import ARTICLE, extraction_payload, source_messages
from tests.unit.test_ai_extraction_workflow import extraction_item as extraction_fixture

extraction_item = extraction_fixture


def test_section_plan_bounds_and_exact_contiguous_coverage():
    text = "Sentence boundary. " * 10000
    sections = plan_sections(text)
    assert len(sections) == MAX_SECTIONS
    assert sections[0]["start"] == 0
    for index, section in enumerate(sections):
        assert section["end"] - section["start"] <= 8000
        if index:
            assert section["start"] == sections[index - 1]["end"]
    assert sections[-1]["end"] < len(text)
    assert plan_sections("") == []


def test_merge_deduplicates_equivalent_entities_but_preserves_conflicting_roles():
    value = build_verified_extraction(extraction_payload(), messages=source_messages(), article_id=uuid4(),
        article_retrieved_at=datetime.now(timezone.utc), source_version=1, source_hash="a" * 64,
        article_text_length=len(ARTICLE))
    other = copy.deepcopy(value)
    other["entities"][2]["indicator_role"] = "unknown"
    merged, limited = merge_extractions([value, other])
    assert not limited
    assert len(merged["entities"]) == 5
    assert len(merged["relationships"]) == 1
    assert {e["indicator_role"] for e in merged["entities"] if e["kind"] == "indicator"} == {"reference", "unknown"}
    StructuredExtractionResponse.model_validate(merged)


def setup_run(db, item, article, text):
    claimed = datetime.now(timezone.utc)
    db.add(ItemAIEnrichment(item_id=item.id, status="pending", source_hash="a" * 64, updated_at=claimed))
    db.commit()
    snapshot = dict(article_id=article.id, article_retrieved_at=article.retrieved_at,
                    source_version=1, source_hash="a" * 64, article_text_length=len(text))
    return dict(item_id=item.id, task_run_id=uuid4(), claim_updated_at=claimed,
                messages=source_messages(text[:8000]), article_text=text, snapshot=snapshot,
                checkpoint=lambda: None)


def empty_completion(**kwargs):
    return AICompletionResult(payload={"structured_extraction": {"entities": [], "relationships": []}},
        provider="openai_compatible", model="test", latency_ms=10, prompt_tokens=100,
        completion_tokens=20, total_tokens=120, **kwargs)


def test_long_article_tail_is_processed_with_exact_offsets(db_session, extraction_item):  # noqa: F811
    item, article = extraction_item
    text = "Ordinary introductory content. " * 400 + ARTICLE
    arguments = setup_run(db_session, item, article, text)
    prompts = []
    def request(_db, _active, **kwargs):
        prompt = json.loads(kwargs["messages"][-1]["content"])
        prompts.append(prompt)
        assert kwargs["max_provider_attempts"] == 1
        result = empty_completion()
        if ARTICLE in prompt["item"]["article_text"]:
            result.payload["structured_extraction"] = extraction_payload()
        return result
    completion, extraction = run_section_extraction(db_session, SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=2048),
        request=request, **arguments)
    assert len(prompts) == 2
    assert completion.total_tokens == 240
    assert extraction["coverage"]["uncovered_chars"] == 0
    assert not extraction["truncated"]
    for entity in extraction["entities"]:
        for passage in entity["evidence"]:
            assert text[passage["start"]:passage["end"]] == passage["quote"]
    StructuredExtractionResponse.model_validate(extraction)


def test_completed_sections_resume_without_repeating_calls(db_session, extraction_item):  # noqa: F811
    item, article = extraction_item
    arguments = setup_run(db_session, item, article, "x" * 17000)
    called = []
    def request(_db, _active, **kwargs):
        called.append(kwargs["provider_operation_scope"])
        if len(called) == 2:
            raise AIIntegrationError("Simulated safe admission failure", provider_io_outcome="not_sent")
        return empty_completion()
    with pytest.raises(AIIntegrationError):
        run_section_extraction(db_session, SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=2048), request=request, **arguments)
    progress = db_session.get(ItemAIEnrichment, item.id).extraction_progress_json
    assert [section["status"] for section in progress["sections"]] == ["completed", "started", "pending"]
    reserved = progress["reserved_tokens"]
    _, extraction = run_section_extraction(db_session, SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=2048), request=request, **arguments)
    assert called == ["item_extraction_section:0", "item_extraction_section:1", "item_extraction_section:1", "item_extraction_section:2"]
    assert extraction["coverage"]["reserved_tokens"] < reserved * 2
    assert extraction["coverage"]["uncovered_chars"] == 0


@pytest.mark.parametrize("change", ["rendered_prompt", "source_hash", "model", "legacy_checkpoint"])
def test_same_run_plan_change_retains_checkpoints_and_budget_without_paid_replay(
    db_session, extraction_item, change,
):  # noqa: F811
    item, article = extraction_item
    arguments = setup_run(db_session, item, article, "x" * 17000)
    active = SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=2048)
    calls = []
    def request(_db, _active, **kwargs):
        calls.append(kwargs["provider_operation_scope"])
        if len(calls) == 2:
            raise AIIntegrationError("Safe admission deferral", provider_io_outcome="not_sent")
        return empty_completion()
    with pytest.raises(AIIntegrationError):
        run_section_extraction(db_session, active, request=request, **arguments)
    row = db_session.get(ItemAIEnrichment, item.id)
    if change == "rendered_prompt":
        arguments["messages"].insert(0, {"role": "system", "content": "Changed extraction instructions."})
    elif change == "source_hash":
        arguments["snapshot"]["source_hash"] = "b" * 64
    elif change == "model":
        active.model = "replacement-model"
    else:
        progress = copy.deepcopy(row.extraction_progress_json)
        progress.pop("plan_fingerprint")
        row.extraction_progress_json = progress
        db_session.commit()
    previous = copy.deepcopy(row.extraction_progress_json)
    with pytest.raises(AIIntegrationError) as stopped:
        run_section_extraction(db_session, active, request=request, **arguments)
    assert stopped.value.failure_category == "extraction_plan_changed"
    assert stopped.value.provider_io_outcome == "not_sent"
    assert stopped.value.retryable is False
    assert calls == ["item_extraction_section:0", "item_extraction_section:1"]
    db_session.expire_all()
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == previous


def test_plan_identity_includes_source_beyond_the_bounded_section_plan():
    active = SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=2048)
    text = "x" * 80000
    arguments = dict(item_id=uuid4(), messages=source_messages(text[:8000]), text=text,
        snapshot=dict(article_id=uuid4(), article_retrieved_at=datetime.now(timezone.utc),
                      source_version=1, source_hash="a" * 64))
    first = section_plan_fingerprint(active, **arguments)
    assert section_plan_fingerprint(active, **arguments) == first
    arguments["text"] = text[:-1] + "y"
    assert section_plan_fingerprint(active, **arguments) != first


def test_total_budget_limits_calls_and_discloses_uncovered_text(db_session, extraction_item):  # noqa: F811
    item, article = extraction_item
    arguments = setup_run(db_session, item, article, "x" * 100000)
    calls = []
    def request(*_args, **kwargs):
        calls.append(kwargs)
        return empty_completion()
    _, extraction = run_section_extraction(db_session, SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=128000), request=request, **arguments)
    assert 1 <= len(calls) <= MAX_SECTIONS
    assert extraction["coverage"]["reserved_tokens"] <= TOTAL_TOKEN_BUDGET
    assert extraction["coverage"]["uncovered_chars"] > 0
    assert extraction["truncated"]
    assert all(call["max_completion_tokens"] == 8192 for call in calls)


def test_superseded_claim_cannot_checkpoint_or_send(db_session, extraction_item):  # noqa: F811
    item, article = extraction_item
    arguments = setup_run(db_session, item, article, "x" * 9000)
    row = db_session.get(ItemAIEnrichment, item.id)
    row.updated_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(AITaskRunStoppedError, match="stale_result_discarded"):
        run_section_extraction(db_session, SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=2048),
            request=lambda *_a, **_kw: pytest.fail("must not call provider"), **arguments)


def test_progress_excludes_private_provider_payloads_and_handles_invalid_history():
    progress = {"task_run_id": "secret", "source_hash": "a" * 64, "text_chars": 10,
        "reserved_tokens": 100, "sections": [{"index": 0, "start": 0, "end": 10,
            "status": "completed", "completion": {"payload": "private"}}]}
    value = extraction_progress_response(progress)
    assert value.processed_chars == 10
    assert "private" not in value.model_dump_json()
    assert "secret" not in value.model_dump_json()
    assert extraction_progress_response({"sections": []}) is None


def test_authorized_continuation_reuses_all_completed_sections(db_session, extraction_item, monkeypatch):  # noqa: F811
    from app.services.ai_article_continuation import progress_digest
    item, article = extraction_item
    arguments = setup_run(db_session, item, article, "x" * 100000)
    active = SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=128)
    calls = []
    def request(_db, _active, **kwargs):
        calls.append(kwargs["provider_operation_scope"])
        result = empty_completion()
        result.payload["summary_text"] = f"Summary for {calls[-1]}"
        return result
    run_section_extraction(db_session, active, request=request, **arguments)
    previous = db_session.get(ItemAIEnrichment, item.id).extraction_progress_json
    assert len(calls) == 8
    authority = SimpleNamespace(section_limit=16, token_budget=128000,
        expected_progress_digest=progress_digest(previous))
    monkeypatch.setattr("app.services.ai_article_continuation.continuation_authority", lambda *_args, **_kwargs: authority)
    arguments["task_run_id"] = uuid4()
    completion, extraction = run_section_extraction(db_session, active, request=request, **arguments)
    assert len(calls) == 13
    assert len(set(calls)) == 13
    assert extraction["coverage"]["uncovered_chars"] == 0
    assert extraction["coverage"]["reserved_tokens"] > previous["reserved_tokens"]
    assert "item_extraction_section:12" in completion.payload["summary_text"]
    # Redelivery of the same continuation sends no completed provider call.
    run_section_extraction(db_session, active, request=request, **arguments)
    assert len(calls) == 13


def test_continuation_does_not_reuse_modified_source_or_provider(db_session, extraction_item, monkeypatch):  # noqa: F811
    from app.services.ai_article_continuation import progress_digest
    item, article = extraction_item
    arguments = setup_run(db_session, item, article, "x" * 100000)
    active = SimpleNamespace(provider_type="openai_compatible", model="test", max_completion_tokens=128)
    run_section_extraction(db_session, active, request=lambda *_args, **_kwargs: empty_completion(), **arguments)
    previous = db_session.get(ItemAIEnrichment, item.id).extraction_progress_json
    authority = SimpleNamespace(section_limit=16, token_budget=128000, expected_progress_digest=progress_digest(previous))
    monkeypatch.setattr("app.services.ai_article_continuation.continuation_authority", lambda *_args, **_kwargs: authority)
    arguments["task_run_id"] = uuid4()
    active.model = "changed"
    with pytest.raises(AIIntegrationError, match="Provider settings"):
        run_section_extraction(db_session, active, request=lambda *_a, **_kw: pytest.fail("must not send"), **arguments)
    assert db_session.get(ItemAIEnrichment, item.id).extraction_progress_json == previous
