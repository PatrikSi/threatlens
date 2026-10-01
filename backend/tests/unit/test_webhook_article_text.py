"""Webhook content is bounded, opt-in and pinned to accepted source identity."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sqlalchemy_event

from app.models.article import Article
from app.models.feed import Feed
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.notification import NotificationWebhookWrite
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_events import (
    emit_integration_event,
    route_integration_event,
)
from app.services.notification_webhook_requests import render_notification_request
from app.services.notification_webhook_storage import (
    apply_notification_webhook_updates,
    build_notification_webhook,
    decrypt_notification_text,
    notification_webhook_response_from_model,
)
from app.services.notification_webhooks import (
    reserve_notification_webhook_delivery_from_saved_request,
)
from app.services.webhook_article_text import (
    MAX_ARTICLE_TEXT_BYTES,
    ArticleTextSnapshot,
    add_article_reference,
    article_text_for_item,
    automation_payload_with_article_text,
    capture_article_reference,
    load_article_text,
)
from app.services.webhook_automation import MAX_AUTOMATION_BYTES, automation_envelope


def source(db, text="Full article evidence"):
    feed = Feed(name="Source", url=f"https://example.com/{uuid.uuid4()}")
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title="Article",
        url="https://example.com/article",
        dedupe_key=str(uuid.uuid4()),
        content_hash="a" * 64,
    )
    db.add(item)
    db.flush()
    article = Article(item_id=item.id, text=text, final_url=item.url, http_status=200)
    db.add(article)
    db.flush()
    return feed, item, article


def configuration(**changes):
    return NotificationWebhookWrite.model_validate(
        {
            "name": "SOC webhook",
            "url_template": "https://siem.example/events",
            "body_fields": [
                {"key": "text", "value": "{{item.full_text}}"},
                {"key": "status", "value": "{{item.full_text_status}}"},
            ],
            **changes,
        }
    )


def test_default_template_does_not_read_article_text():
    result = article_text_for_item(
        object(),
        item=SimpleNamespace(id=uuid.uuid4()),
        payload=configuration(body_fields=[]),
    )
    assert result == ArticleTextSnapshot()


def test_reference_contains_no_body_and_sql_read_is_bounded(db_session):
    _feed, item, article = source(db_session, "🎯" * (MAX_ARTICLE_TEXT_BYTES + 5))
    payload = {"item_id": str(item.id), "item": {"id": str(item.id)}}
    add_article_reference(db_session, payload=payload)
    assert "🎯" not in json.dumps(payload)
    assert (
        payload["article_text_reference"] == payload["item"]["article_text_reference"]
    )
    statements = []

    def listener(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    with db_session.no_autoflush:
        sqlalchemy_event.listen(db_session.bind, "before_cursor_execute", listener)
        try:
            result = load_article_text(
                db_session, item_id=item.id, reference=payload["article_text_reference"]
            )
        finally:
            sqlalchemy_event.remove(db_session.bind, "before_cursor_execute", listener)
    assert len(statements) == 1 and "substr(" in statements[0]
    assert result.status == "truncated"
    assert result.text == "🎯" * (MAX_ARTICLE_TEXT_BYTES // 4)
    assert result.included_bytes == MAX_ARTICLE_TEXT_BYTES
    assert result.original_characters == MAX_ARTICLE_TEXT_BYTES + 5
    assert result.article_id == str(article.id)


@pytest.mark.parametrize("change", ["item_revision", "retrieved_at", "delete"])
def test_old_reference_never_reads_new_body(db_session, change):
    _feed, item, article = source(db_session)
    reference = capture_article_reference(db_session, item.id)
    if change == "item_revision":
        item.classification_required_version += 1
    elif change == "retrieved_at":
        article.retrieved_at += timedelta(seconds=1)
    else:
        db_session.delete(article)
    if change != "delete":
        article.text = "NEW SECRET CONTENT"
    db_session.flush()
    result = load_article_text(db_session, item_id=item.id, reference=reference)
    assert result.status == "source_changed"
    assert result.text == ""
    assert result.source_revision == 1


def test_event_before_article_retrieval_stays_unavailable(db_session):
    _feed, item, article = source(db_session)
    reference = {"source_revision": 1, "article_id": None, "article_retrieved_at": None}
    result = load_article_text(db_session, item_id=item.id, reference=reference)
    assert article.text and result.status == "unavailable" and result.text == ""
    assert (
        load_article_text(db_session, item_id=item.id, reference=None).status
        == "unavailable"
    )


def test_large_text_renders_json_without_configuration_field_limit():
    text = 'Evidence "quoted"\n' * 1000
    rendered = render_notification_request(
        configuration(),
        user=SimpleNamespace(id=uuid.uuid4()),
        feed=None,
        item=None,
        article_text=ArticleTextSnapshot(
            text=text,
            status="available",
            original_characters=len(text),
            included_bytes=len(text),
        ),
    )
    assert json.loads(rendered.body) == {"text": text, "status": "available"}


@pytest.mark.parametrize("body_mode", ["json", "form", "raw"])
def test_repeated_text_cannot_blow_up_rendered_body(body_mode):
    cfg = configuration(
        body_mode=body_mode,
        body_template="{{item.full_text}}" * 10 if body_mode == "raw" else None,
        body_fields=[{"key": "text", "value": "{{item.full_text}}" * 10}],
    )
    with pytest.raises(ValueError, match="264 KiB"):
        render_notification_request(
            cfg,
            user=SimpleNamespace(),
            feed=None,
            item=None,
            article_text=ArticleTextSnapshot(text="x" * MAX_ARTICLE_TEXT_BYTES),
        )


@pytest.mark.parametrize(
    "config",
    [
        {"url_template": "https://siem.example/{{ item.full_text }}"},
        {"headers": [{"key": "X-Content", "value": "{{item.full_text}}"}]},
        {"query_params": [{"key": "content", "value": "{{item.full_text}}"}]},
        {"body_fields": [{"key": "{{item.full_text}}", "value": "text"}]},
    ],
)
def test_full_text_requires_body_value(config):
    with pytest.raises(ValueError, match="only.*body values"):
        render_notification_request(
            configuration(**config), user=SimpleNamespace(), feed=None, item=None
        )


def test_automation_text_uses_remaining_envelope_budget_and_opt_in(db_session):
    _feed, item, article = source(db_session, '"🎯\\\n' * 50000)
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="article.ai.ready",
        created_at=article.retrieved_at,
        source_type="item",
        source_id=str(item.id),
    )
    data = {
        "item_id": str(item.id),
        "article_text_reference": capture_article_reference(db_session, item.id),
        "padding": "p" * 210000,
    }
    assert "article_text" not in automation_payload_with_article_text(
        object(), event=event, payload=data, include=False
    )
    result = automation_payload_with_article_text(
        db_session, event=event, payload=data, include=True
    )
    included = result["article_text"]
    wire = json.dumps(
        automation_envelope(event, payload=result),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(wire) <= MAX_AUTOMATION_BYTES
    assert included["status"] == "truncated"
    assert (
        included["included_bytes"]
        == len(included["text"].encode("utf-8"))
        < MAX_ARTICLE_TEXT_BYTES
    )
    assert included["original_characters"] == len(article.text)
    assert data.get("article_text") is None


def test_opt_in_preserved_by_older_editor_and_fenced_by_schema(db_session, seed_users):
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        configuration(include_article_text=True, body_fields=[]),
    )
    db_session.add(webhook)
    db_session.flush()
    instance, _subscription = ensure_webhook_integration(db_session, webhook)
    assert instance.schema_version == 3
    assert (
        notification_webhook_response_from_model(webhook).include_article_text is True
    )
    apply_notification_webhook_updates(
        webhook, configuration(name="Old editor", body_fields=[])
    )
    assert webhook.include_article_text is True
    apply_notification_webhook_updates(
        webhook, configuration(include_article_text=False, body_fields=[])
    )
    assert webhook.include_article_text is False


def test_delivery_and_retry_retain_exact_text_after_refresh_and_edit(
    db_session, seed_users
):
    feed, item, article = source(db_session, "ORIGINAL EVIDENCE" * 1000)
    webhook = build_notification_webhook(seed_users["analyst"].id, configuration())
    db_session.add(webhook)
    db_session.flush()
    event = emit_integration_event(
        db_session,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"text-test:{uuid.uuid4()}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    routed = route_integration_event(db_session, event_id=event.id)
    delivery = db_session.get(
        NotificationWebhookDelivery, routed.webhook_delivery_ids[0]
    )
    original = decrypt_notification_text(delivery.rendered_body)
    assert json.loads(original)["text"] == article.text
    assert (
        db_session.get(IntegrationInstance, webhook.integration_id).schema_version == 3
    )
    generic = db_session.get(IntegrationDelivery, delivery.integration_delivery_id)
    assert generic.payload_json["webhook_article_text_snapshot"] is True
    article.text = "NEW CONTENT MUST NOT LEAK"
    article.retrieved_at += timedelta(seconds=1)
    apply_notification_webhook_updates(
        webhook,
        configuration(body_fields=[{"key": "title", "value": "{{item.title}}"}]),
    )
    retry = reserve_notification_webhook_delivery_from_saved_request(
        db_session, webhook=webhook, delivery=delivery
    )
    assert decrypt_notification_text(retry.rendered_body) == original


def test_ai_relevance_templates_preserve_zero_and_missing():
    cfg = configuration(
        body_fields=[
            {"key": "score", "value": "{{ai.relevance_score}}"},
            {"key": "label", "value": "{{ai.relevance_label}}"},
        ]
    )
    rendered = render_notification_request(
        cfg,
        user=SimpleNamespace(),
        feed=None,
        item=SimpleNamespace(ai_relevance={"score": 0, "label": "low"}),
    )
    assert json.loads(rendered.body) == {"score": "0", "label": "low"}
    rendered = render_notification_request(
        cfg, user=SimpleNamespace(), feed=None, item=None
    )
    assert json.loads(rendered.body) == {"score": "", "label": ""}


def test_ai_summary_and_reasons_are_frozen_template_values_with_disclosure():
    cfg = configuration(
        body_fields=[
            {"key": "summary", "value": "{{ai.summary}}"},
            {"key": "reasons", "value": "{{ai.relevance_reasons}}"},
            {
                "key": "truncated",
                "value": "{{ai.summary_truncated}} / {{ai.relevance_reasons_truncated}}",
            },
        ]
    )
    rendered = render_notification_request(
        cfg,
        user=SimpleNamespace(),
        feed=None,
        item=SimpleNamespace(
            ai_relevance={
                "summary": "Retained analysis",
                "relevance_reasons": ["Active exploitation", "Known infrastructure"],
                "summary_truncated": True,
                "relevance_reasons_truncated": False,
            }
        ),
    )
    assert json.loads(rendered.body) == {
        "summary": "Retained analysis",
        "reasons": "Active exploitation\nKnown infrastructure",
        "truncated": "true / false",
    }


def test_full_utf8_text_budget_fits_json_without_ascii_escape_inflation():
    text = "🎯" * (MAX_ARTICLE_TEXT_BYTES // 4)
    rendered = render_notification_request(
        configuration(),
        user=SimpleNamespace(),
        feed=None,
        item=None,
        article_text=ArticleTextSnapshot(
            text=text, status="available", included_bytes=MAX_ARTICLE_TEXT_BYTES
        ),
    )
    assert json.loads(rendered.body)["text"] == text
    assert len(rendered.body.encode("utf-8")) < MAX_AUTOMATION_BYTES
