"""Legacy webhooks keep their configuration without opting into article text."""

import json
import uuid

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import inspect, select

from app.db.base import Base
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.notification import NotificationWebhookWrite
from app.services.integration_events import (
    emit_integration_event,
    route_integration_event,
)
from app.services.notification_webhook_requests import rendered_request_from_delivery
from app.services.notification_webhook_storage import (
    build_notification_webhook,
    notification_webhook_write_from_model,
)
from tests.unit.test_migration_0107 import _migration
from tests.unit.test_migration_0115 import source


def _stored_row(db, model, identifier, *, exclude=()):
    columns = [
        column for column in model.__table__.columns if column.name not in exclude
    ]
    return dict(
        db.execute(select(*columns).where(model.id == identifier)).one()._mapping
    )


def _route_item(db, item):
    event = emit_integration_event(
        db,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"migration-webhook:{uuid.uuid4()}",
        payload={"item_id": str(item.id), "feed_id": str(item.feed_id)},
    )
    routed = route_integration_event(db, event_id=event.id)
    assert len(routed.webhook_delivery_ids) == 1
    return db.get(NotificationWebhookDelivery, routed.webhook_delivery_ids[0])


@pytest.mark.parametrize("payload_mode", ["template", "automation_v1"])
def test_article_text_upgrade_preserves_legacy_webhooks_and_delivery_snapshots(
    db_session, monkeypatch, seed_users, payload_mode
):
    item = source(db_session)
    feed = db_session.get(Feed, item.feed_id)
    db_session.add(
        Article(
            item_id=item.id,
            final_url=item.url,
            http_status=200,
            text="Private extracted article evidence",
        )
    )
    configuration = NotificationWebhookWrite.model_validate(
        {
            "name": "Retained SOC destination",
            "url_template": "https://siem.example/events",
            "payload_mode": payload_mode,
            "feed_scope": "selected",
            "feed_ids": [str(feed.id)],
            "headers": [{"key": "Authorization", "value": "Bearer fixture-secret"}],
            "query_params": [{"key": "api_key", "value": "fixture-query-secret"}],
            "body_fields": [{"key": "title", "value": "{{item.title}}"}],
            "conditions": {
                "op": "all",
                "conditions": [
                    {"field": "feed_id", "operator": "in", "value": [str(feed.id)]}
                ],
            },
            "timeout_seconds": 23,
        }
    )
    webhook = build_notification_webhook(seed_users["analyst"].id, configuration)
    db_session.add(webhook)
    db_session.flush()
    delivery = _route_item(db_session, item)
    assert delivery.delivery_state == "pending" and delivery.error is None
    webhook_id, delivery_id = webhook.id, delivery.id
    saved_configuration = _stored_row(
        db_session, NotificationWebhook, webhook_id, exclude={"include_article_text"}
    )
    saved_delivery = _stored_row(db_session, NotificationWebhookDelivery, delivery_id)
    saved_request = rendered_request_from_delivery(delivery)
    migration = _migration(db_session, monkeypatch, "0125_webhook_article_text")

    # This is a populated 0124 webhook, with its original encrypted fields and
    # retained request snapshot, when the new opt-in column is first added.
    migration.downgrade()
    assert "include_article_text" not in {
        column["name"]
        for column in inspect(db_session.connection()).get_columns(
            "notification_webhooks"
        )
    }
    assert (
        _stored_row(
            db_session,
            NotificationWebhook,
            webhook_id,
            exclude={"include_article_text"},
        )
        == saved_configuration
    )
    migration.upgrade()
    db_session.expire_all()
    webhook = db_session.get(NotificationWebhook, webhook_id)
    delivery = db_session.get(NotificationWebhookDelivery, delivery_id)
    assert webhook.include_article_text is False
    assert notification_webhook_write_from_model(webhook) == configuration
    assert (
        _stored_row(
            db_session,
            NotificationWebhook,
            webhook_id,
            exclude={"include_article_text"},
        )
        == saved_configuration
    )
    assert (
        _stored_row(db_session, NotificationWebhookDelivery, delivery_id)
        == saved_delivery
    )
    assert rendered_request_from_delivery(delivery) == saved_request

    # An existing subscription can still route new deliveries after upgrade;
    # extracted text is not added to the structured automation payload.
    fresh_item = Item(
        feed_id=feed.id,
        title="Fresh source after upgrade",
        url="https://source.example/fresh",
        dedupe_key=str(uuid.uuid4()),
        content_hash="b" * 64,
    )
    db_session.add(fresh_item)
    db_session.flush()
    db_session.add(
        Article(
            item_id=fresh_item.id,
            final_url=fresh_item.url,
            http_status=200,
            text="Private new article evidence",
        )
    )
    fresh_delivery = _route_item(db_session, fresh_item)
    assert fresh_delivery.delivery_state == "pending" and fresh_delivery.error is None
    rendered = rendered_request_from_delivery(fresh_delivery)
    assert rendered.timeout_seconds == configuration.timeout_seconds
    assert rendered.headers_dict["Authorization"] == "Bearer fixture-secret"
    if payload_mode == "template":
        assert json.loads(rendered.body) == {"title": fresh_item.title}
    else:
        assert "article_text" not in json.loads(rendered.body)["data"]
    assert "Private new article evidence" not in rendered.body

    names = {"notification_webhooks", "notification_webhook_deliveries"}
    context = MigrationContext.configure(
        db_session.connection(),
        opts={
            "include_object": lambda obj, name, kind, reflected, compared: (
                name in names
                if kind == "table"
                else getattr(getattr(obj, "table", None), "name", None) in names
            )
        },
    )
    assert compare_metadata(context, Base.metadata) == []
