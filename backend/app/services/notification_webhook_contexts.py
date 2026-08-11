from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.notification import NotificationEventType, NotificationWebhookWrite
from app.services.daily_brief_notifications import (
    DailyBriefNotificationContextError,
    daily_brief_context_from_payload,
)
from app.services.notification_webhook_storage import notification_error_for_display
from app.services.notification_webhook_templates import (
    AlertMatchContext,
    DailyDigestContext,
    FailedWebhookContext,
)

FEED_FAILING_NOTIFICATION_THRESHOLD = 3


def resolve_sample_feed_and_item(
    db: Session,
    *,
    payload: NotificationWebhookWrite,
    sample_item_id: uuid.UUID | None,
    sample_feed_id: uuid.UUID | None,
) -> tuple[Feed | SimpleNamespace, Item | SimpleNamespace]:
    if sample_item_id is not None:
        item = db.scalar(select(Item).where(Item.id == sample_item_id))
        if item is None:
            raise ValueError("Sample item not found")
        feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))
        if feed is None:
            raise ValueError("Sample feed not found")
        return feed, item

    feed: Feed | None = None
    item: Item | None = None
    if sample_feed_id is not None:
        feed = db.scalar(select(Feed).where(Feed.id == sample_feed_id))
        if feed is None:
            raise ValueError("Sample feed not found")
        item = db.scalar(
            select(Item)
            .where(Item.feed_id == sample_feed_id)
            .order_by(Item.first_seen_at.desc())
        )
    elif payload.feed_scope == "selected" and payload.feed_ids:
        candidate_feed_id = payload.feed_ids[0]
        feed = db.scalar(select(Feed).where(Feed.id == candidate_feed_id))
        if feed is not None:
            item = db.scalar(
                select(Item)
                .where(Item.feed_id == candidate_feed_id)
                .order_by(Item.first_seen_at.desc())
            )
    else:
        item = db.scalar(select(Item).order_by(Item.first_seen_at.desc()))
        if item is not None:
            feed = db.scalar(select(Feed).where(Feed.id == item.feed_id))

    if feed is None:
        feed = SimpleNamespace(
            id=uuid.uuid4(),
            name="Example Feed",
            url="https://example.com/feed.xml",
            site_url="https://example.com",
        )
    if item is None:
        item = SimpleNamespace(
            id=uuid.uuid4(),
            title="Example ThreatLens item",
            url="https://example.com/articles/example-threatlens-item",
            canonical_url="https://example.com/articles/example-threatlens-item",
            summary="ThreatLens generated this sample payload so you can test the webhook before saving it.",
            status="new",
            published_at=datetime(2026, 3, 25, 9, 15, tzinfo=timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
        )
    return feed, item


def feed_ids_for_webhook_payload(
    payload: NotificationWebhookWrite,
) -> list[uuid.UUID] | None:
    if payload.feed_scope != "selected":
        return None
    return list(payload.feed_ids)


def build_sample_feed_for_event(
    feed: Feed | SimpleNamespace,
    event_type: NotificationEventType,
) -> Feed | SimpleNamespace:
    if event_type != "feed_failing":
        return feed

    return SimpleNamespace(
        id=getattr(feed, "id", uuid.uuid4()),
        name=getattr(feed, "name", "") or "Example Feed",
        url=getattr(feed, "url", "") or "https://example.com/feed.xml",
        site_url=getattr(feed, "site_url", "") or "https://example.com",
        error_count=max(
            int(getattr(feed, "error_count", 0) or 0),
            FEED_FAILING_NOTIFICATION_THRESHOLD,
        ),
        last_error=getattr(feed, "last_error", "") or "http_status:500",
        last_fetch_at=getattr(feed, "last_fetch_at", None)
        or datetime.now(timezone.utc),
        last_success_at=getattr(feed, "last_success_at", None),
    )


def build_alert_match_context_for_item(
    db: Session,
    *,
    user_id: uuid.UUID | None = None,
    item: Item | SimpleNamespace,
) -> AlertMatchContext | None:
    item_id = getattr(item, "id", None)
    classification = (
        db.scalar(
            select(ItemClassification).where(ItemClassification.item_id == item_id)
        )
        if item_id is not None
        else None
    )
    haystack = build_item_haystack(
        title=getattr(item, "title", "") or "",
        summary=getattr(item, "summary", None),
        url=getattr(item, "url", "") or "",
        canonical_url=getattr(item, "canonical_url", None),
        classification=getattr(classification, "primary_category", None),
    )

    alert_query = select(AlertInterest).where(AlertInterest.enabled.is_(True))
    if user_id is not None:
        alert_query = alert_query.where(AlertInterest.user_id == user_id)
    alerts = db.scalars(alert_query.order_by(AlertInterest.created_at.asc())).all()

    matched_names: list[str] = []
    matched_categories: list[str] = []
    matched_keywords: list[str] = []
    for alert in alerts:
        keywords = [
            keyword
            for keyword in (alert.keywords or [])
            if keyword and keyword in haystack
        ]
        if not keywords:
            continue
        matched_names.append(alert.name)
        matched_categories.append(alert.category)
        for keyword in keywords:
            if keyword not in matched_keywords:
                matched_keywords.append(keyword)

    if not matched_names:
        return None
    return AlertMatchContext(
        count=len(matched_names),
        primary_name=matched_names[0],
        names=matched_names,
        categories=matched_categories,
        matched_keywords=matched_keywords,
    )


def daily_brief_context_for_webhook_delivery(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
) -> DailyDigestContext | None:
    if delivery.integration_delivery_id is None:
        return None
    generic_delivery = db.get(IntegrationDelivery, delivery.integration_delivery_id)
    if generic_delivery is None or generic_delivery.event_id is None:
        return None
    event = db.get(IntegrationEvent, generic_delivery.event_id)
    if event is None:
        return None
    try:
        return daily_brief_context_from_payload(event.payload_json)
    except DailyBriefNotificationContextError:
        return None


def build_failed_webhook_retry_context(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
) -> FailedWebhookContext | None:
    if delivery.source_delivery_id is None:
        return None
    source_delivery = db.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.id == delivery.source_delivery_id
        )
    )
    if source_delivery is None:
        return None
    source_webhook = db.scalar(
        select(NotificationWebhook).where(
            NotificationWebhook.id == source_delivery.webhook_id
        )
    )
    if source_webhook is None:
        return None
    return FailedWebhookContext(
        id=source_webhook.id,
        name=source_webhook.name,
        event_type=source_delivery.event_type_snapshot,
        status_code=source_delivery.status_code,
        error=notification_error_for_display(source_delivery.error),
        attempted_at=source_delivery.attempted_at,
    )


def build_item_haystack(
    *,
    title: str,
    summary: str | None,
    url: str,
    canonical_url: str | None,
    classification: str | None,
) -> str:
    return " ".join(
        [title, summary or "", url, canonical_url or "", classification or ""]
    ).lower()
