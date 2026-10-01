"""Exercise AI completion routing, source provenance and delivery lock fences."""

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.notification import NotificationWebhookWrite
from app.schemas.webhook_automation import WebhookConditionGroup
from app.services.ai_enrichment_provenance import enrichment_result_provenance
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_events import emit_integration_event, route_integration_event
from app.services.intel_event_eligibility import IntelEventBusy
from app.services.notification_webhook_storage import build_notification_webhook, decrypt_notification_text
from app.services.webhook_ai_events import (
    AI_READY_EVENT, ai_event_current, condition_values_with_current_ai,
    current_relevance_snapshot, emit_article_ai_ready,
)
from app.services.webhook_conditions import evaluate_conditions
from tests.integration.test_ai_feature_output_validation import (
    configured_item as configured_item, _provider, _run,
)


def _ready_article(db):
    feed = Feed(name="AI routing source", url=f"https://example.com/{uuid.uuid4()}")
    db.add(feed)
    db.flush()
    item = Item(feed_id=feed.id, title="Observed credential theft", url="https://example.com/article",
                summary="Current bulletin", dedupe_key=str(uuid.uuid4()), content_hash="a" * 64)
    db.add(item)
    db.flush()
    article = Article(item_id=item.id, text="Current primary evidence", final_url=item.url, http_status=200)
    db.add(article)
    db.flush()
    generated = datetime.now(timezone.utc)
    proof = enrichment_result_provenance(
        active=SimpleNamespace(provider_id=None, provider_version=None, model="test-model"),
        item=item, article=article, classification=None, feed_name=feed.name, tag_names=[],
        source_hash="b" * 64, generated_at=generated,
    )
    enrichment = ItemAIEnrichment(
        item_id=item.id, status="ready", source_hash="b" * 64, result_provenance_json=proof,
        relevance_score=0.9, relevance_label="high", generated_at=generated,
    )
    db.add(enrichment)
    db.flush()
    return item, article, enrichment


def _event(db):
    item, article, enrichment = _ready_article(db)
    event_id = emit_article_ai_ready(db, item_id=item.id)
    assert event_id is not None
    return item, article, enrichment, db.get(IntegrationEvent, event_id)


def test_completion_emits_without_indicators_and_deduplicates_same_result(db_session):
    item, _article, _enrichment, event = _event(db_session)
    assert "indicators" not in event.payload_json
    assert event.payload_json["ai_relevance"]["score"] == 0.9
    assert ai_event_current(db_session, event.payload_json)
    assert emit_article_ai_ready(db_session, item_id=item.id) == event.id
    assert list(db_session.scalars(select(IntegrationEvent.id).where(
        IntegrationEvent.event_type == AI_READY_EVENT,
        IntegrationEvent.source_id == str(item.id),
    ))) == [event.id]


def test_ai_ready_can_combine_relevance_with_captured_tags(db_session):
    from app.models.tag import ItemTag, Tag

    item, _article, enrichment = _ready_article(db_session)
    tag = Tag(name="Endpoint")
    db_session.add(tag)
    db_session.flush()
    db_session.add(ItemTag(item_id=item.id, tag_id=tag.id))
    enrichment.result_provenance_json = {
        **enrichment.result_provenance_json,
        "tag_fingerprint": hashlib.sha256(b"8:Endpoint").hexdigest(),
    }
    db_session.flush()
    event_id = emit_article_ai_ready(db_session, item_id=item.id)
    event = db_session.get(IntegrationEvent, event_id)
    condition = WebhookConditionGroup.model_validate({"op": "all", "conditions": [
        {"field": "ai_relevance_score", "operator": "gte", "value": 0.8},
        {"field": "tag_id", "operator": "in", "value": [str(tag.id)]},
    ]})
    values = condition_values_with_current_ai(
        db_session, payload=event.payload_json, created_at=event.created_at,
        event_type=event.event_type, conditions=condition,
    )
    assert evaluate_conditions(condition, values)[0]
    assert event.payload_json["filter_metadata"]["tags"] == ["Endpoint"]


def test_successful_relevance_only_provider_run_publishes_transactional_event(
    db_session, configured_item, monkeypatch,
):
    item, settings = configured_item
    settings.summary_enabled = False
    settings.structured_extraction_enabled = False
    db_session.commit()
    _provider(monkeypatch, [{"relevance_score": 0.9}])
    _run_id, result, _resource = _run(db_session, feature="item_enrichment", item=item)
    assert result.status == "ready"
    events = list(db_session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == AI_READY_EVENT, IntegrationEvent.source_id == str(item.id),
    )))
    assert len(events) == 1
    assert events[0].payload_json["ai_relevance"]["score"] == 0.9
    assert ai_event_current(db_session, events[0].payload_json)


@pytest.mark.parametrize("oversized_reasons", [False, True])
def test_shared_summary_and_rationale_are_bounded_and_revision_pinned(db_session, oversized_reasons):
    item, _article, enrichment = _ready_article(db_session)
    enrichment.summary_text = "A" * 12000
    enrichment.relevance_reasons_json = ["R" * (10000 if oversized_reasons else 600)] * 5
    db_session.flush()
    event_id = emit_article_ai_ready(db_session, item_id=item.id)
    event = db_session.get(IntegrationEvent, event_id)
    snapshot = event.payload_json["ai_relevance"]
    assert len(snapshot["summary"]) == 8000 and snapshot["summary_truncated"]
    assert snapshot["relevance_reasons_truncated"]
    assert snapshot["relevance_reasons"] == ([] if oversized_reasons else ["R" * 500] * 4)
    assert ai_event_current(db_session, event.payload_json)
    enrichment.summary_text = "A corrected synthesis"
    db_session.flush()
    assert not ai_event_current(db_session, event.payload_json)
    assert snapshot["summary"] == "A" * 8000


def test_legacy_events_keep_compact_relevance_even_with_large_successful_metadata(db_session):
    item, _article, enrichment = _ready_article(db_session)
    enrichment.summary_text = "A" * 12000
    enrichment.relevance_reasons_json = ["R" * 5000]
    enrichment.result_provenance_json = {
        **enrichment.result_provenance_json, "model": "M" * 100000,
    }
    db_session.flush()
    event = emit_integration_event(
        db_session, event_type="rss_item_new", source_type="item", source_id=item.id,
        idempotency_key=str(uuid.uuid4()), payload={"item_id": str(item.id), "feed_id": str(item.feed_id)},
    )
    snapshot = event.payload_json["ai_relevance"]
    assert "summary" not in snapshot and "relevance_reasons" not in snapshot
    assert "model" not in snapshot["provenance"]
    assert len(snapshot["provenance_digest"]) == 64
    assert ai_event_current(db_session, event.payload_json)
    enrichment.result_provenance_json = {**enrichment.result_provenance_json, "model": "changed"}
    db_session.flush()
    assert not ai_event_current(db_session, event.payload_json)


@pytest.mark.parametrize("change", ["source", "article", "purge", "failed", "replacement", "feed", "proof"])
def test_source_or_result_changes_suppress_historical_ai(db_session, change):
    item, article, enrichment, event = _event(db_session)
    if change == "source":
        item.classification_required_version += 1
    elif change == "article":
        article.retrieved_at += timedelta(microseconds=1)
    elif change == "purge":
        article.content_purged_at = datetime.now(timezone.utc)
        article.text = None
        article.extraction_method = "retention_purged"
    elif change == "failed":
        enrichment.status = "error"
    elif change == "replacement":
        enrichment.generated_at += timedelta(seconds=1)
    elif change == "feed":
        db_session.get(Feed, item.feed_id).name = "Changed context"
    else:
        enrichment.result_provenance_json = None
    db_session.flush()
    assert not ai_event_current(db_session, event.payload_json)
    condition = WebhookConditionGroup.model_validate({
        "op": "not", "conditions": [{"field": "ai_relevance_score", "operator": "lte", "value": 0.5}],
    })
    values = condition_values_with_current_ai(
        db_session, payload=event.payload_json, created_at=event.created_at,
        event_type=event.event_type, conditions=condition,
    )
    assert evaluate_conditions(condition, values)[0] is False
    assert evaluate_conditions(condition, values)[2] == ["ai_relevance_score"]


def test_early_event_never_acquires_future_ai_relevance(db_session):
    item, _article, enrichment = _ready_article(db_session)
    enrichment.status = "pending"
    db_session.flush()
    event = emit_integration_event(
        db_session, event_type="rss_item_new", source_type="item", source_id=item.id,
        idempotency_key=str(uuid.uuid4()), payload={"item_id": str(item.id), "feed_id": str(item.feed_id)},
    )
    assert event.payload_json["ai_relevance"] is None
    enrichment.status = "ready"
    db_session.flush()
    assert current_relevance_snapshot(db_session, item.id) is not None
    assert event.payload_json["ai_relevance"] is None


def test_ai_ready_routes_custom_templates_without_indicator_inventory(db_session, seed_users, monkeypatch):
    monkeypatch.setattr("app.services.notification_webhook_validation.validate_notification_target_url", lambda *_: None)
    item, _article, _enrichment, event = _event(db_session)
    webhook = build_notification_webhook(seed_users["analyst"].id, NotificationWebhookWrite(
        name="High relevance", event_type=AI_READY_EVENT, url_template="https://soc.example/notify",
        body_fields=[{"key": "title", "value": "{{item.title}}"}],
        conditions={"op": "all", "conditions": [{"field": "ai_relevance_score", "operator": "gte", "value": 0.8}]},
    ))
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    result = route_integration_event(db_session, event_id=event.id)
    assert len(result.webhook_delivery_ids) == 1, result
    from app.models.notification_webhook_delivery import NotificationWebhookDelivery

    delivery = db_session.get(NotificationWebhookDelivery, result.webhook_delivery_ids[0])
    assert item.title in decrypt_notification_text(delivery.rendered_body)


def test_ai_ready_preview_reports_supersession(client, db_session, auth_headers):
    item, _article, enrichment, event = _event(db_session)
    db_session.commit()
    request = {"event_id": str(event.id), "webhook": {
        "name": "AI", "event_type": AI_READY_EVENT, "payload_mode": "automation_v1",
        "url_template": "https://soc.example/notify",
        "conditions": {"op": "all", "conditions": [
            {"field": "ai_relevance_label", "operator": "in", "value": ["high"]},
        ]},
    }}
    response = client.post("/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request)
    assert response.status_code == 200, response.text
    assert response.json()["matches"]
    enrichment.status = "error"
    db_session.commit()
    response = client.post("/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request)
    assert response.status_code == 200, response.text
    assert not response.json()["matches"]
    assert "ai_relevance_label" in response.json()["missing_fields"]


def test_ai_ready_preview_uses_existing_item_read_scope(client, db_session, auth_headers, seed_users):
    from app.models.api_token import ApiToken

    _item, _article, _enrichment, event = _event(db_session)
    token = db_session.scalar(select(ApiToken).where(ApiToken.user_id == seed_users["analyst"].id))
    token.scopes = ["read:notifications"]
    db_session.commit()
    request = {"event_id": str(event.id), "webhook": {
        "name": "AI", "event_type": AI_READY_EVENT, "url_template": "https://soc.example/notify",
    }}
    response = client.post("/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request)
    assert response.status_code == 403
    token.scopes = ["read:notifications", "read:items"]
    db_session.commit()
    response = client.post("/notifications/webhooks/preview", headers=auth_headers["analyst"], json=request)
    assert response.status_code == 200, response.text


def test_ai_ready_never_sends_synthetic_test(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.services.notification_webhook_http.send_rendered_notification_request",
                        lambda *_args, **_kwargs: pytest.fail("Synthetic AI event must not be sent"))
    response = client.post("/notifications/webhooks/test", headers=auth_headers["analyst"], json={
        "webhook": {"name": "AI", "event_type": AI_READY_EVENT, "url_template": "https://soc.example/notify"},
    })
    assert response.status_code == 422
    assert "stored-event preview" in response.text


@pytest.mark.parametrize("locked_model", [Item, Article, ItemAIEnrichment])
def test_busy_source_or_provider_result_is_retryable_without_waiting(database_engine, locked_model):
    with Session(database_engine) as db:
        item, _article, _enrichment, event = _event(db)
        item_id, feed_id, event_id, payload = item.id, item.feed_id, event.id, event.payload_json
        db.commit()
    key = locked_model.id if locked_model is Item else locked_model.item_id
    try:
        with Session(database_engine) as writer, Session(database_engine) as reader:
            writer.execute(select(key).where(key == item_id).with_for_update()).all()
            with pytest.raises(IntelEventBusy, match="retry"):
                ai_event_current(reader, payload, lock=True)
            # NOWAIT only aborts the validation savepoint, not the caller's transaction.
            assert reader.scalar(select(Item.id).where(Item.id == item_id)) == item_id
            writer.rollback()
            assert ai_event_current(reader, payload, lock=True)
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(delete(IntegrationEvent).where(IntegrationEvent.id == event_id))
            cleanup.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup.commit()
