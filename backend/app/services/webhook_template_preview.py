"""Render custom payload bodies from an authorized stored event without sending."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.integration import IntegrationEvent
from app.models.user import User
from app.schemas.notification import NotificationWebhookWrite
from app.services.daily_brief_notifications import daily_brief_context_from_payload
from app.services.integration_events import hydrate_integration_event_payload_resources
from app.services.notification_webhook_requests import render_notification_request
from app.services.webhook_article_text import article_text_for_item


def preview_template_body(
    db: Session,
    *,
    event: IntegrationEvent,
    data: dict,
    payload: NotificationWebhookWrite,
    user: User,
) -> str | None:
    item, feed = None, None
    alert_context, digest_context = None, None
    if event.event_type in {"rss_item_new", "alert_match", "feed_failing"}:
        if event.schema_version < 2:
            raise ValueError(
                "This older event has no retained content snapshot; choose a newer event"
            )
        resources = hydrate_integration_event_payload_resources(
            db,
            event_type=event.event_type,
            schema_version=event.schema_version,
            payload=data,
        )
        item, feed = resources.item, resources.feed
        alert_context = resources.alert_context_for_owner(user.id)
    elif event.event_type == "article.ai.ready":
        item_values = dict(data.get("item") or {})
        item_values.update(
            id=uuid.UUID(data["item_id"]),
            article_text_reference=data.get("article_text_reference"),
            ai_relevance=data.get("ai_relevance"),
        )
        item = SimpleNamespace(**item_values)
        feed = SimpleNamespace(**(data.get("feed") or {}))
    elif event.event_type in {"daily_digest", "report_ready"}:
        digest_context = daily_brief_context_from_payload(data)
    else:
        raise ValueError(
            "A retained template body preview is unavailable for this event type"
        )
    return render_notification_request(
        payload,
        user=user,
        item=item,
        feed=feed,
        event_type=event.event_type,
        triggered_at=event.created_at,
        delivery_id=event.id,
        alert_context=alert_context,
        digest_context=digest_context,
        article_text=article_text_for_item(db, item=item, payload=payload),
    ).body
