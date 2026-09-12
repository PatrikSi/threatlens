from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services import ai_integration
from app.services.ai_config import get_or_create_ai_settings
from app.services.ai_ops import queue_ai_task_run


@pytest.fixture()
def configured_item(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()
    settings = get_or_create_ai_settings(db_session)
    settings.base_url = "http://localhost:11434/v1"
    settings.model = "test-model"
    settings.request_max_retries = 0
    feed = Feed(id=uuid.uuid4(), name="Synthetic security feed", url="https://example.com/feed.xml")
    db_session.add(feed)
    db_session.flush()
    item = Item(
        id=uuid.uuid4(), feed_id=feed.id, source_guid="synthetic-1",
        url="https://example.com/item", canonical_url="https://example.com/item",
        title="Synthetic security bulletin", summary="A security bulletin.",
        published_at=datetime.now(timezone.utc), dedupe_key="synthetic-1",
        content_hash="a" * 64, status="content_fetched",
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(Article(
        item_id=item.id, final_url=item.url, http_status=200,
        text="A security bulletin describing a vulnerability and recommended updates.",
        extraction_method="readable",
    ))
    db_session.commit()
    return item, settings


def _provider(monkeypatch, responses):
    remaining = iter(responses)
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        content = next(remaining)
        return httpx.Response(200, request=request, json={
            "model": "test-model",
            "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    monkeypatch.setattr(ai_integration, "build_safe_http_client", lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(ai_integration, "_provider_retry_delay_seconds", lambda **_kwargs: 0)
    return sent


def _run(db, *, feature, item):
    run = queue_ai_task_run(
        db, task_type=feature, trigger_source="manual",
        item_id=item.id if feature == "item_enrichment" else None,
    )
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    if feature == "item_enrichment":
        result = ai_integration.run_item_ai_enrichment(db, item_id=item.id, force=True, task_run_id=run.id)
        resource = result.enrichment
    else:
        result = ai_integration.run_daily_brief_generation(db, force=True, task_run_id=run.id, emit_notification=False)
        resource = result.brief
    db.commit()
    return run, result, resource


@pytest.mark.parametrize("feature, payload, field", [
    ("item_enrichment", {}, "summary_text"),
    ("item_enrichment", {"summary_text": "Summary", "relevance_score": "NaN"}, "relevance_score"),
    ("item_enrichment", {"summary_text": {}, "relevance_score": 0.5}, "summary_text"),
    ("daily_brief", {}, "brief_text"),
    ("daily_brief", {"brief_text": "   "}, "brief_text"),
])
def test_invalid_output_records_failure_and_usage_without_publishing_ready(
    db_session, configured_item, monkeypatch, feature, payload, field,
):
    item, _settings = configured_item
    sent = _provider(monkeypatch, [payload])
    run, result, resource = _run(db_session, feature=feature, item=item)
    assert len(sent) == 1
    assert result.status == resource.status == "error"
    assert field in resource.error
    events = list(db_session.scalars(select(AIUsageEvent).where(AIUsageEvent.task_run_id_snapshot == run.id)))
    assert len(events) == 1
    assert events[0].success is False
    assert events[0].total_tokens == 150
    assert events[0].latency_ms is not None
    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id)))
    assert len(receipts) == 1
    assert receipts[0].state == "failed"
    assert receipts[0].io_outcome == "response_received"


@pytest.mark.parametrize("feature, valid", [
    ("item_enrichment", {"summary_text": "Grounded summary", "relevance_score": 0.7}),
    ("daily_brief", {"brief_text": "Grounded briefing", "key_points": [{"text": "A supported point"}]}),
])
def test_invalid_output_can_retry_and_settle_each_receipt_once(
    db_session, configured_item, monkeypatch, feature, valid,
):
    item, settings = configured_item
    settings.request_max_retries = 1
    db_session.commit()
    sent = _provider(monkeypatch, [{}, valid])
    run, result, resource = _run(db_session, feature=feature, item=item)
    assert len(sent) == 2
    assert result.status == resource.status == "ready"
    events = list(db_session.scalars(select(AIUsageEvent).where(AIUsageEvent.task_run_id_snapshot == run.id)))
    assert sorted(event.success for event in events) == [False, True]
    assert sum(event.total_tokens for event in events) == 300
    receipts = list(db_session.scalars(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id)))
    assert sorted(receipt.state for receipt in receipts) == ["failed", "succeeded"]
    assert all(receipt.io_outcome == "response_received" for receipt in receipts)


@pytest.mark.parametrize("summary, relevance, payload", [
    (False, True, {"relevance_score": 0}),
    (True, False, {"summary_text": "Summary"}),
])
def test_output_validation_only_requires_enabled_enrichment_fields(
    db_session, configured_item, monkeypatch, summary, relevance, payload,
):
    item, settings = configured_item
    settings.summary_enabled = summary
    settings.relevance_enabled = relevance
    db_session.commit()
    _provider(monkeypatch, [payload])
    _run_row, result, resource = _run(db_session, feature="item_enrichment", item=item)
    assert result.status == resource.status == "ready"


@pytest.mark.parametrize("feature, payload, field", [
    ("item_enrichment", {"summary_text": "Summary\x00text", "relevance_score": 0.7}, "Unicode"),
    ("item_enrichment", {"summary_text": "Summary\ud800text", "relevance_score": 0.7}, "Unicode"),
    ("item_enrichment", {"summary_text": "Summary", "relevance_score": 0.7, "unused": float("nan")}, "finite"),
    ("daily_brief", {"brief_text": "Brief\x00text"}, "Unicode"),
    ("daily_brief", {"brief_text": "Brief text", "title": "x" * 256}, "title"),
    ("daily_brief", {"brief_text": "Brief text", "title": {"text": "Invalid title type"}}, "title"),
])
def test_unstorable_output_settles_received_failure_and_known_usage(
    db_session, configured_item, monkeypatch, feature, payload, field,
):
    item, _settings = configured_item
    sent = _provider(monkeypatch, [payload])
    run, result, resource = _run(db_session, feature=feature, item=item)
    assert len(sent) == 1
    assert result.status == resource.status == "error"
    assert field in resource.error
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id))
    assert (receipt.state, receipt.io_outcome) == ("failed", "response_received")
    usage = db_session.scalar(select(AIUsageEvent).where(AIUsageEvent.task_run_id_snapshot == run.id))
    assert usage.success is False and usage.total_tokens == 150
    assert usage.failure_category == "invalid_output"


@pytest.mark.parametrize("status", [200, 401])
def test_malformed_optional_metadata_cannot_poison_receipts_or_usage(
    db_session, configured_item, monkeypatch, status,
):
    from app.models.ai_task_event import AITaskEvent
    from app.services.ai_output_storage import validate_output_storage

    item, settings = configured_item
    calls = []
    response = {
        "model": "synthetic\x00model", "bad\ud800key": True,
        "choices": [{"message": {"content": json.dumps({"summary_text": "Valid summary", "relevance_score": 0.7})},
                     "finish_reason": "unknown\x00reason"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    if status != 200:
        response["error"] = {"message": "Rejected\x00request", "param": {"untrusted": float("nan")},
                             "type": "type\ud800", "code": ["arbitrary", {"nested": True}]}

    def transport(request):
        calls.append(request)
        return httpx.Response(status, request=request, content=json.dumps(response).encode(), headers={"Content-Type": "application/json"})

    monkeypatch.setattr(ai_integration, "build_safe_http_client", lambda **kwargs: httpx.Client(transport=httpx.MockTransport(transport)))
    run, result, resource = _run(db_session, feature="item_enrichment", item=item)
    expected_success = status == 200
    assert len(calls) == 1
    assert result.status == resource.status == ("ready" if expected_success else "error")
    receipt = db_session.scalar(select(AIProviderAttemptReceipt).where(AIProviderAttemptReceipt.task_run_id_snapshot == run.id))
    assert receipt.state == ("succeeded" if expected_success else "failed")
    assert receipt.io_outcome == "response_received"
    usage = db_session.scalar(select(AIUsageEvent).where(AIUsageEvent.task_run_id_snapshot == run.id))
    assert usage.success is expected_success and usage.total_tokens == 150
    assert usage.model == settings.model
    for event in db_session.scalars(select(AITaskEvent).where(AITaskEvent.task_run_id == run.id)):
        validate_output_storage(event.payload_json, max_bytes=10000)
        assert "\x00" not in (event.message or "")
