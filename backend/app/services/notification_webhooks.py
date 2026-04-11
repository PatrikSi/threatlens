from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.alert_interest import AlertInterest
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationAnalyticsEventSummary,
    NotificationAnalyticsResponse,
    NotificationAnalyticsWebhookSummary,
    NotificationEventType,
    NotificationWebhookDeliveryResponse,
    NotificationTemplateVariable,
    NotificationWebhookField,
    NotificationWebhookResponse,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.safe_fetch import REDIRECT_STATUS_CODES, RedirectError, SafeFetchError, build_safe_http_client
from app.services.url_utils import ensure_runtime_fetchable_url

logger = logging.getLogger(__name__)
settings = get_settings()
FEED_FAILING_NOTIFICATION_THRESHOLD = 3
FEED_FAILING_NOTIFICATION_COOLDOWN_HOURS = 12
DAILY_DIGEST_WINDOW_HOURS = 24

TEMPLATE_VARIABLES: tuple[NotificationTemplateVariable, ...] = (
    NotificationTemplateVariable(key="event.type", description="Notification event type.", example="rss_item_new"),
    NotificationTemplateVariable(
        key="event.triggered_at",
        description="UTC timestamp for this delivery attempt.",
        example="2026-03-25T12:30:00+00:00",
    ),
    NotificationTemplateVariable(
        key="event.delivery_id",
        description="Unique identifier for this webhook delivery.",
        example="8a95c9a9-70bc-4a9f-b1a3-13c9b92f17f4",
    ),
    NotificationTemplateVariable(key="user.id", description="Owner of the webhook.", example="9c24d6bd-23c2-4c4d-9097-fd8c88d9eafd"),
    NotificationTemplateVariable(key="user.email", description="Owner email address.", example="analyst@example.com"),
    NotificationTemplateVariable(key="feed.id", description="Feed identifier.", example="01b5d2a8-4734-4b95-8c7c-51171f260432"),
    NotificationTemplateVariable(key="feed.name", description="Feed display name.", example="Unit42 RSS"),
    NotificationTemplateVariable(key="feed.url", description="Feed source URL.", example="https://example.com/feed.xml"),
    NotificationTemplateVariable(key="feed.site_url", description="Feed website URL when known.", example="https://example.com"),
    NotificationTemplateVariable(key="feed.error_count", description="Consecutive feed fetch failures.", example="3"),
    NotificationTemplateVariable(key="feed.last_error", description="Latest feed error message.", example="http_status:500"),
    NotificationTemplateVariable(
        key="feed.last_fetch_at",
        description="Last fetch attempt timestamp for the feed.",
        example="2026-03-25T12:29:00+00:00",
    ),
    NotificationTemplateVariable(
        key="feed.last_success_at",
        description="Last successful fetch timestamp for the feed.",
        example="2026-03-24T22:15:00+00:00",
    ),
    NotificationTemplateVariable(key="item.id", description="New item identifier.", example="5e2db70d-0a5b-428e-a9bf-0b4be612cbab"),
    NotificationTemplateVariable(key="item.title", description="Item title.", example="New campaign targeting exposed edge devices"),
    NotificationTemplateVariable(key="item.url", description="Original item URL.", example="https://example.com/articles/campaign"),
    NotificationTemplateVariable(key="item.canonical_url", description="Canonical item URL when known.", example="https://example.com/articles/campaign"),
    NotificationTemplateVariable(key="item.summary", description="Feed summary text.", example="Researchers observed a fresh wave of exploitation."),
    NotificationTemplateVariable(key="item.status", description="ThreatLens item status.", example="new"),
    NotificationTemplateVariable(key="item.published_at", description="Published timestamp when provided by the feed.", example="2026-03-25T09:15:00+00:00"),
    NotificationTemplateVariable(key="item.first_seen_at", description="First time ThreatLens saw the item.", example="2026-03-25T09:16:02+00:00"),
    NotificationTemplateVariable(key="alert.count", description="Number of alert interests matched by the item.", example="2"),
    NotificationTemplateVariable(key="alert.primary_name", description="Name of the first matched alert.", example="Ransomware Watch"),
    NotificationTemplateVariable(key="alert.names", description="Comma-separated matched alert names.", example="Ransomware Watch, Credential Theft"),
    NotificationTemplateVariable(key="alert.categories", description="Comma-separated matched alert categories.", example="malware, identity"),
    NotificationTemplateVariable(
        key="alert.matched_keywords",
        description="Comma-separated keywords that matched across all alerts.",
        example="lockbit, initial access",
    ),
    NotificationTemplateVariable(
        key="failed_webhook.id",
        description="Identifier of the webhook delivery source that failed.",
        example="bf8916bf-b537-4f66-941a-c126b204f826",
    ),
    NotificationTemplateVariable(key="failed_webhook.name", description="Name of the webhook that failed.", example="Slack critical feed"),
    NotificationTemplateVariable(
        key="failed_webhook.event_type",
        description="Event type handled by the webhook that failed.",
        example="rss_item_new",
    ),
    NotificationTemplateVariable(key="failed_webhook.status_code", description="HTTP status code from the failed delivery.", example="500"),
    NotificationTemplateVariable(key="failed_webhook.error", description="Failure message from the failed delivery.", example="HTTP 500"),
    NotificationTemplateVariable(
        key="failed_webhook.attempted_at",
        description="Timestamp of the failed delivery attempt.",
        example="2026-03-25T12:35:00+00:00",
    ),
    NotificationTemplateVariable(
        key="digest.window_start",
        description="Start of the digest coverage window in UTC.",
        example="2026-03-24T12:00:00+00:00",
    ),
    NotificationTemplateVariable(
        key="digest.window_end",
        description="End of the digest coverage window in UTC.",
        example="2026-03-25T12:00:00+00:00",
    ),
    NotificationTemplateVariable(key="digest.total_items", description="Total items included in the digest.", example="18"),
    NotificationTemplateVariable(key="digest.total_feeds", description="Number of feeds represented in the digest.", example="6"),
    NotificationTemplateVariable(
        key="digest.feed_names",
        description="Comma-separated feed names represented in the digest.",
        example="Unit42 RSS, CISA",
    ),
    NotificationTemplateVariable(
        key="digest.top_titles",
        description="Newline-separated sample titles from the digest window.",
        example="Threat report one\nThreat report two",
    ),
)
TEMPLATE_VARIABLE_KEYS = frozenset(variable.key for variable in TEMPLATE_VARIABLES)
TEMPLATE_PATTERN = __import__("re").compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
MAX_RESPONSE_PREVIEW_CHARS = 4000
BLOCKED_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "expect",
        "host",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class TemplateRenderError(ValueError):
    pass


@dataclass(frozen=True)
class AlertMatchContext:
    count: int
    primary_name: str
    names: list[str]
    categories: list[str]
    matched_keywords: list[str]


@dataclass(frozen=True)
class FailedWebhookContext:
    id: uuid.UUID
    name: str
    event_type: str
    status_code: int | None
    error: str | None
    attempted_at: datetime


@dataclass(frozen=True)
class DailyDigestContext:
    window_start: datetime
    window_end: datetime
    total_items: int
    total_feeds: int
    feed_names: list[str]
    top_titles: list[str]


@dataclass
class RenderedNotificationRequest:
    method: str
    url: str
    headers: list[NotificationWebhookField]
    query_params: list[NotificationWebhookField]
    body: str | None
    headers_dict: dict[str, str]
    query_param_pairs: list[tuple[str, str]]
    json_body: dict | None
    form_body: list[tuple[str, str]] | None
    raw_body: bytes | None
    timeout_seconds: int


@dataclass
class NotificationWebhookDeliveryAttempt:
    result: NotificationWebhookTestResponse
    delivery: NotificationWebhookDelivery


def list_template_variables() -> list[NotificationTemplateVariable]:
    return list(TEMPLATE_VARIABLES)


def notification_webhook_write_from_model(webhook: NotificationWebhook) -> NotificationWebhookWrite:
    return NotificationWebhookWrite(
        name=webhook.name,
        enabled=webhook.enabled,
        event_type=webhook.event_type,
        url_template=webhook.url_template,
        method=webhook.method,
        feed_scope=webhook.feed_scope,
        feed_ids=[uuid.UUID(value) for value in (webhook.feed_ids_json or [])],
        query_params=[NotificationWebhookField.model_validate(entry) for entry in (webhook.query_params_json or [])],
        headers=[NotificationWebhookField.model_validate(entry) for entry in (webhook.headers_json or [])],
        body_mode=webhook.body_mode,
        body_fields=[NotificationWebhookField.model_validate(entry) for entry in (webhook.body_fields_json or [])],
        body_template=webhook.body_template,
        timeout_seconds=webhook.timeout_seconds,
    )


def notification_webhook_response_from_model(webhook: NotificationWebhook) -> NotificationWebhookResponse:
    payload = notification_webhook_write_from_model(webhook)
    return NotificationWebhookResponse(
        id=webhook.id,
        user_id=webhook.user_id,
        name=payload.name,
        enabled=payload.enabled,
        event_type=payload.event_type,
        url_template=payload.url_template,
        method=payload.method,
        feed_scope=payload.feed_scope,
        feed_ids=payload.feed_ids,
        query_params=payload.query_params,
        headers=payload.headers,
        body_mode=payload.body_mode,
        body_fields=payload.body_fields,
        body_template=payload.body_template,
        timeout_seconds=payload.timeout_seconds,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
    )


def notification_webhook_delivery_response_from_model(
    delivery: NotificationWebhookDelivery,
) -> NotificationWebhookDeliveryResponse:
    return NotificationWebhookDeliveryResponse(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        user_id=delivery.user_id,
        event_type=delivery.event_type_snapshot,
        item_id=delivery.item_id,
        feed_id=delivery.feed_id,
        item_title=delivery.item_title_snapshot,
        feed_name=delivery.feed_name_snapshot,
        delivery_kind=delivery.delivery_kind,
        success=delivery.success,
        status_code=delivery.status_code,
        duration_ms=delivery.duration_ms,
        timeout_seconds=delivery.timeout_seconds,
        rendered_url=delivery.rendered_url,
        rendered_method=delivery.rendered_method,
        rendered_headers=[
            NotificationWebhookField.model_validate(entry) for entry in (delivery.rendered_headers_json or [])
        ],
        rendered_query_params=[
            NotificationWebhookField.model_validate(entry) for entry in (delivery.rendered_query_params_json or [])
        ],
        rendered_body=delivery.rendered_body,
        response_body_preview=delivery.response_body_preview,
        error=delivery.error,
        attempted_at=delivery.attempted_at,
    )


def build_notification_webhook(user_id: uuid.UUID, payload: NotificationWebhookWrite) -> NotificationWebhook:
    return NotificationWebhook(
        user_id=user_id,
        name=payload.name,
        enabled=payload.enabled,
        event_type=payload.event_type,
        url_template=payload.url_template,
        method=payload.method,
        feed_scope=payload.feed_scope,
        feed_ids_json=[str(feed_id) for feed_id in payload.feed_ids],
        query_params_json=[field.model_dump() for field in payload.query_params],
        headers_json=[field.model_dump() for field in payload.headers],
        body_mode=payload.body_mode,
        body_fields_json=[field.model_dump() for field in payload.body_fields],
        body_template=payload.body_template,
        timeout_seconds=payload.timeout_seconds,
    )


def apply_notification_webhook_updates(webhook: NotificationWebhook, payload: NotificationWebhookWrite) -> None:
    webhook.name = payload.name
    webhook.enabled = payload.enabled
    webhook.event_type = payload.event_type
    webhook.url_template = payload.url_template
    webhook.method = payload.method
    webhook.feed_scope = payload.feed_scope
    webhook.feed_ids_json = [str(feed_id) for feed_id in payload.feed_ids]
    webhook.query_params_json = [field.model_dump() for field in payload.query_params]
    webhook.headers_json = [field.model_dump() for field in payload.headers]
    webhook.body_mode = payload.body_mode
    webhook.body_fields_json = [field.model_dump() for field in payload.body_fields]
    webhook.body_template = payload.body_template
    webhook.timeout_seconds = payload.timeout_seconds


def validate_notification_webhook_payload(payload: NotificationWebhookWrite, available_feed_ids: set[uuid.UUID]) -> None:
    if payload.feed_scope == "selected":
        invalid_feed_ids = [str(feed_id) for feed_id in payload.feed_ids if feed_id not in available_feed_ids]
        if invalid_feed_ids:
            raise ValueError(f"Unknown feed ids: {', '.join(sorted(invalid_feed_ids))}")

    unknown_variables = sorted(_find_unknown_template_variables(payload))
    if unknown_variables:
        raise ValueError(f"Unknown template variable(s): {', '.join(unknown_variables)}")


def render_notification_request(
    payload: NotificationWebhookWrite,
    *,
    user: User | SimpleNamespace,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    event_type: NotificationEventType | None = None,
    triggered_at: datetime | None = None,
    delivery_id: uuid.UUID | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
) -> RenderedNotificationRequest:
    rendered_at = triggered_at or datetime.now(timezone.utc)
    delivery_uuid = delivery_id or uuid.uuid4()
    context = _build_template_context(
        user=user,
        feed=feed,
        item=item,
        event_type=event_type or payload.event_type,
        triggered_at=rendered_at,
        delivery_id=delivery_uuid,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )

    rendered_url = _render_template(payload.url_template, context)
    rendered_query_params = [_render_field(field, context) for field in payload.query_params]
    rendered_headers = [_render_field(field, context) for field in payload.headers]
    headers_dict = _canonicalize_headers(rendered_headers)
    query_param_pairs = [(field.key, field.value) for field in rendered_query_params]

    body_text: str | None = None
    json_body: dict | None = None
    form_body: list[tuple[str, str]] | None = None
    raw_body: bytes | None = None

    if payload.body_mode == "json":
        json_body = {}
        for rendered_field in (_render_field(field, context) for field in payload.body_fields):
            _assign_nested_json_value(json_body, rendered_field.key, rendered_field.value)
        body_text = json.dumps(json_body, ensure_ascii=True)
        headers_dict.setdefault("Content-Type", "application/json")
    elif payload.body_mode == "form":
        form_body = []
        for rendered_field in (_render_field(field, context) for field in payload.body_fields):
            form_body.append((rendered_field.key, rendered_field.value))
        body_text = urlencode(form_body)
        headers_dict.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif payload.body_mode == "raw":
        body_text = _render_template(payload.body_template or "", context)
        raw_body = body_text.encode("utf-8")
        headers_dict.setdefault("Content-Type", _default_raw_content_type(body_text))

    rendered_headers = [NotificationWebhookField(key=key, value=value) for key, value in headers_dict.items()]

    return RenderedNotificationRequest(
        method=payload.method,
        url=rendered_url,
        headers=rendered_headers,
        query_params=rendered_query_params,
        body=body_text,
        headers_dict=headers_dict,
        query_param_pairs=query_param_pairs,
        json_body=json_body,
        form_body=form_body,
        raw_body=raw_body,
        timeout_seconds=payload.timeout_seconds,
    )


def test_notification_webhook(
    db: Session,
    *,
    user: User,
    payload: NotificationWebhookWrite,
    sample_item_id: uuid.UUID | None = None,
    sample_feed_id: uuid.UUID | None = None,
) -> NotificationWebhookTestResponse:
    feed, item = _resolve_sample_feed_and_item(
        db,
        payload=payload,
        sample_item_id=sample_item_id,
        sample_feed_id=sample_feed_id,
    )
    sample_feed = _build_sample_feed_for_event(feed, payload.event_type)
    alert_context = None
    failed_webhook_context = None
    digest_context = None

    if payload.event_type == "alert_match":
        alert_context = build_alert_match_context_for_item(db, user_id=user.id, item=item)
        if alert_context is None:
            alert_context = AlertMatchContext(
                count=1,
                primary_name="Threat activity",
                names=["Threat activity"],
                categories=["monitoring"],
                matched_keywords=["credential theft"],
            )
    elif payload.event_type == "webhook_failed":
        failed_webhook_context = FailedWebhookContext(
            id=uuid.uuid4(),
            name="Example monitored webhook",
            event_type="rss_item_new",
            status_code=500,
            error="HTTP 500",
            attempted_at=datetime.now(timezone.utc),
        )
    elif payload.event_type == "daily_digest":
        digest_context = build_daily_digest_context(
            db,
            user_id=user.id,
            feed_ids=_feed_ids_for_webhook_payload(payload),
        )
        if digest_context is None:
            now = datetime.now(timezone.utc)
            digest_context = DailyDigestContext(
                window_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                window_end=now,
                total_items=7,
                total_feeds=2,
                feed_names=["Example Feed", "CISA"],
                top_titles=["ThreatLens sample digest item", "Second sample digest item"],
            )

    rendered = render_notification_request(
        payload,
        user=user,
        feed=sample_feed,
        item=item,
        event_type=payload.event_type,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    return _send_rendered_notification_request(rendered)


def get_matching_notification_webhooks_for_feed(db: Session, *, feed_id: uuid.UUID) -> list[NotificationWebhook]:
    return get_matching_notification_webhooks(db, event_type="rss_item_new", feed_id=feed_id)


def get_matching_notification_webhooks(
    db: Session,
    *,
    event_type: NotificationEventType,
    feed_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> list[NotificationWebhook]:
    query = select(NotificationWebhook).where(
        NotificationWebhook.enabled.is_(True),
        NotificationWebhook.event_type == event_type,
    )
    if user_id is not None:
        query = query.where(NotificationWebhook.user_id == user_id)

    enabled_webhooks = db.scalars(query.order_by(NotificationWebhook.created_at.asc())).all()
    if feed_id is None:
        return [webhook for webhook in enabled_webhooks if webhook.feed_scope == "all"]
    return [
        webhook
        for webhook in enabled_webhooks
        if webhook.feed_scope == "all" or str(feed_id) in (webhook.feed_ids_json or [])
    ]


def send_notification_webhook(
    db: Session,
    *,
    webhook: NotificationWebhook,
    user: User,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
    item: Item | SimpleNamespace | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
    delivery_kind: str = "live",
    item_title: str | None = None,
    feed_name: str | None = None,
) -> NotificationWebhookDeliveryAttempt:
    payload = notification_webhook_write_from_model(webhook)
    rendered = render_notification_request(
        payload,
        user=user,
        feed=feed,
        item=item,
        event_type=event_type,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    result = _send_rendered_notification_request(rendered)
    delivery = _record_notification_webhook_delivery(
        db,
        webhook=webhook,
        event_type=event_type,
        rendered=rendered,
        result=result,
        delivery_kind=delivery_kind,
        item_id=getattr(item, "id", None),
        feed_id=getattr(feed, "id", None),
        item_title=item_title if item_title is not None else getattr(item, "title", None),
        feed_name=feed_name if feed_name is not None else getattr(feed, "name", None),
    )
    return NotificationWebhookDeliveryAttempt(result=result, delivery=delivery)


def send_notification_webhook_for_item(
    db: Session,
    *,
    webhook: NotificationWebhook,
    item: Item,
    feed: Feed,
    user: User,
) -> NotificationWebhookTestResponse:
    attempt = send_notification_webhook(
        db,
        webhook=webhook,
        user=user,
        event_type="rss_item_new",
        item=item,
        feed=feed,
    )
    return attempt.result


def retry_notification_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    delivery: NotificationWebhookDelivery,
) -> NotificationWebhookDelivery:
    rendered = _rendered_request_from_delivery(delivery)
    result = _send_rendered_notification_request(rendered)
    return _record_notification_webhook_delivery(
        db,
        webhook=webhook,
        event_type=delivery.event_type_snapshot,
        rendered=rendered,
        result=result,
        delivery_kind="retry",
        item_id=delivery.item_id,
        feed_id=delivery.feed_id,
        item_title=delivery.item_title_snapshot,
        feed_name=delivery.feed_name_snapshot,
    )


def _resolve_sample_feed_and_item(
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


def _feed_ids_for_webhook_payload(payload: NotificationWebhookWrite) -> list[uuid.UUID] | None:
    if payload.feed_scope != "selected":
        return None
    return list(payload.feed_ids)


def _build_sample_feed_for_event(
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
        error_count=max(int(getattr(feed, "error_count", 0) or 0), FEED_FAILING_NOTIFICATION_THRESHOLD),
        last_error=getattr(feed, "last_error", "") or "http_status:500",
        last_fetch_at=getattr(feed, "last_fetch_at", None) or datetime.now(timezone.utc),
        last_success_at=getattr(feed, "last_success_at", None),
    )


def build_alert_match_context_for_item(
    db: Session,
    *,
    user_id: uuid.UUID,
    item: Item | SimpleNamespace,
) -> AlertMatchContext | None:
    classification = None
    item_id = getattr(item, "id", None)
    if item_id is not None:
        classification = db.scalar(select(ItemClassification).where(ItemClassification.item_id == item_id))

    haystack = _build_item_haystack(
        title=getattr(item, "title", "") or "",
        summary=getattr(item, "summary", None),
        url=getattr(item, "url", "") or "",
        canonical_url=getattr(item, "canonical_url", None),
        classification=getattr(classification, "primary_category", None),
    )

    alerts = db.scalars(
        select(AlertInterest)
        .where(AlertInterest.user_id == user_id, AlertInterest.enabled.is_(True))
        .order_by(AlertInterest.created_at.asc())
    ).all()

    matched_names: list[str] = []
    matched_categories: list[str] = []
    matched_keywords: list[str] = []

    for alert in alerts:
        keywords = [keyword for keyword in (alert.keywords or []) if keyword and keyword in haystack]
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


def build_daily_digest_context(
    db: Session,
    *,
    user_id: uuid.UUID,
    feed_ids: list[uuid.UUID] | None,
    now: datetime | None = None,
) -> DailyDigestContext | None:
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=DAILY_DIGEST_WINDOW_HOURS)

    query = (
        select(Item.title, Feed.name)
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.first_seen_at >= window_start, Item.first_seen_at <= window_end)
        .order_by(Item.first_seen_at.desc())
    )
    if feed_ids:
        query = query.where(Item.feed_id.in_(feed_ids))

    rows = db.execute(query.limit(50)).all()
    if not rows:
        return None

    feed_names: list[str] = []
    top_titles: list[str] = []
    for title, feed_name in rows:
        if feed_name and feed_name not in feed_names:
            feed_names.append(feed_name)
        if title and title not in top_titles:
            top_titles.append(title)
        if len(top_titles) >= 5 and len(feed_names) >= 10:
            break

    total_items_query = select(func.count()).select_from(Item).where(
        Item.first_seen_at >= window_start,
        Item.first_seen_at <= window_end,
    )
    if feed_ids:
        total_items_query = total_items_query.where(Item.feed_id.in_(feed_ids))
    total_items = int(db.scalar(total_items_query) or 0)
    total_feeds_query = select(func.count(func.distinct(Item.feed_id))).where(
        Item.first_seen_at >= window_start,
        Item.first_seen_at <= window_end,
    )
    if feed_ids:
        total_feeds_query = total_feeds_query.where(Item.feed_id.in_(feed_ids))
    total_feeds = int(db.scalar(total_feeds_query) or 0)

    return DailyDigestContext(
        window_start=window_start,
        window_end=window_end,
        total_items=total_items,
        total_feeds=total_feeds,
        feed_names=feed_names,
        top_titles=top_titles[:5],
    )


def has_recent_notification_delivery(
    db: Session,
    *,
    webhook_id: uuid.UUID,
    event_type: NotificationEventType,
    since: datetime | None = None,
    item_id: uuid.UUID | None = None,
    feed_id: uuid.UUID | None = None,
    delivery_kind: str = "live",
    success_only: bool = False,
) -> bool:
    query = select(NotificationWebhookDelivery.id).where(
        NotificationWebhookDelivery.webhook_id == webhook_id,
        NotificationWebhookDelivery.event_type_snapshot == event_type,
        NotificationWebhookDelivery.delivery_kind == delivery_kind,
    )
    if since is not None:
        query = query.where(NotificationWebhookDelivery.attempted_at >= since)
    if item_id is not None:
        query = query.where(NotificationWebhookDelivery.item_id == item_id)
    if feed_id is not None:
        query = query.where(NotificationWebhookDelivery.feed_id == feed_id)
    if success_only:
        query = query.where(NotificationWebhookDelivery.success.is_(True))
    return db.scalar(query.limit(1)) is not None


def get_notification_analytics(db: Session, *, user_id: uuid.UUID) -> NotificationAnalyticsResponse:
    total_deliveries = int(
        db.scalar(select(func.count()).select_from(NotificationWebhookDelivery).where(NotificationWebhookDelivery.user_id == user_id))
        or 0
    )
    successful_deliveries = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(NotificationWebhookDelivery.user_id == user_id, NotificationWebhookDelivery.success.is_(True))
        )
        or 0
    )
    failed_deliveries = total_deliveries - successful_deliveries
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    failures_last_24h = int(
        db.scalar(
            select(func.count())
            .select_from(NotificationWebhookDelivery)
            .where(
                NotificationWebhookDelivery.user_id == user_id,
                NotificationWebhookDelivery.success.is_(False),
                NotificationWebhookDelivery.attempted_at >= cutoff,
            )
        )
        or 0
    )

    event_rows = db.execute(
        select(
            NotificationWebhookDelivery.event_type_snapshot,
            NotificationWebhookDelivery.success,
            func.count().label("count"),
        )
        .where(NotificationWebhookDelivery.user_id == user_id)
        .group_by(NotificationWebhookDelivery.event_type_snapshot, NotificationWebhookDelivery.success)
        .order_by(NotificationWebhookDelivery.event_type_snapshot.asc())
    ).all()
    events_by_type: dict[str, dict[str, int]] = {}
    for event_type, success, count in event_rows:
        bucket = events_by_type.setdefault(event_type, {"total": 0, "failed": 0})
        bucket["total"] += int(count)
        if not success:
            bucket["failed"] += int(count)

    events = [
        NotificationAnalyticsEventSummary(
            event_type=event_type, total_deliveries=stats["total"], failed_deliveries=stats["failed"]
        )
        for event_type, stats in sorted(events_by_type.items())
    ]

    failing_webhook_row = db.execute(
        select(
            NotificationWebhookDelivery.webhook_id,
            NotificationWebhook.name,
            func.count().label("failed_count"),
            func.max(NotificationWebhookDelivery.attempted_at).label("last_failure_at"),
        )
        .join(NotificationWebhook, NotificationWebhook.id == NotificationWebhookDelivery.webhook_id)
        .where(NotificationWebhookDelivery.user_id == user_id, NotificationWebhookDelivery.success.is_(False))
        .group_by(NotificationWebhookDelivery.webhook_id, NotificationWebhook.name)
        .order_by(func.count().desc(), func.max(NotificationWebhookDelivery.attempted_at).desc())
        .limit(1)
    ).first()

    most_failing_webhook = None
    if failing_webhook_row is not None:
        most_failing_webhook = NotificationAnalyticsWebhookSummary(
            webhook_id=failing_webhook_row.webhook_id,
            webhook_name=failing_webhook_row.name,
            failed_deliveries=int(failing_webhook_row.failed_count or 0),
            last_failure_at=failing_webhook_row.last_failure_at,
        )

    success_rate_pct = round((successful_deliveries / total_deliveries) * 100, 1) if total_deliveries else 0.0
    return NotificationAnalyticsResponse(
        total_deliveries=total_deliveries,
        successful_deliveries=successful_deliveries,
        failed_deliveries=failed_deliveries,
        success_rate_pct=success_rate_pct,
        failures_last_24h=failures_last_24h,
        most_failing_webhook=most_failing_webhook,
        events=events,
    )


def _build_template_context(
    *,
    user: User | SimpleNamespace,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    event_type: NotificationEventType,
    triggered_at: datetime,
    delivery_id: uuid.UUID,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
) -> dict[str, str]:
    return {
        "event.type": event_type,
        "event.triggered_at": _isoformat(triggered_at),
        "event.delivery_id": str(delivery_id),
        "user.id": str(getattr(user, "id", "")),
        "user.email": getattr(user, "email", "") or "",
        "feed.id": str(getattr(feed, "id", "")),
        "feed.name": getattr(feed, "name", "") or "",
        "feed.url": getattr(feed, "url", "") or "",
        "feed.site_url": getattr(feed, "site_url", "") or "",
        "feed.error_count": str(getattr(feed, "error_count", "") or ""),
        "feed.last_error": getattr(feed, "last_error", "") or "",
        "feed.last_fetch_at": _isoformat(getattr(feed, "last_fetch_at", None)),
        "feed.last_success_at": _isoformat(getattr(feed, "last_success_at", None)),
        "item.id": str(getattr(item, "id", "")),
        "item.title": getattr(item, "title", "") or "",
        "item.url": getattr(item, "url", "") or "",
        "item.canonical_url": getattr(item, "canonical_url", "") or "",
        "item.summary": getattr(item, "summary", "") or "",
        "item.status": getattr(item, "status", "") or "",
        "item.published_at": _isoformat(getattr(item, "published_at", None)),
        "item.first_seen_at": _isoformat(getattr(item, "first_seen_at", None)),
        "alert.count": str(alert_context.count if alert_context else 0),
        "alert.primary_name": alert_context.primary_name if alert_context else "",
        "alert.names": ", ".join(alert_context.names) if alert_context else "",
        "alert.categories": ", ".join(alert_context.categories) if alert_context else "",
        "alert.matched_keywords": ", ".join(alert_context.matched_keywords) if alert_context else "",
        "failed_webhook.id": str(failed_webhook_context.id) if failed_webhook_context else "",
        "failed_webhook.name": failed_webhook_context.name if failed_webhook_context else "",
        "failed_webhook.event_type": failed_webhook_context.event_type if failed_webhook_context else "",
        "failed_webhook.status_code": str(failed_webhook_context.status_code or "") if failed_webhook_context else "",
        "failed_webhook.error": (failed_webhook_context.error or "") if failed_webhook_context else "",
        "failed_webhook.attempted_at": _isoformat(failed_webhook_context.attempted_at if failed_webhook_context else None),
        "digest.window_start": _isoformat(digest_context.window_start if digest_context else None),
        "digest.window_end": _isoformat(digest_context.window_end if digest_context else None),
        "digest.total_items": str(digest_context.total_items if digest_context else 0),
        "digest.total_feeds": str(digest_context.total_feeds if digest_context else 0),
        "digest.feed_names": ", ".join(digest_context.feed_names) if digest_context else "",
        "digest.top_titles": "\n".join(digest_context.top_titles) if digest_context else "",
    }


def _isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _build_item_haystack(
    *,
    title: str,
    summary: str | None,
    url: str,
    canonical_url: str | None,
    classification: str | None,
) -> str:
    return " ".join([title, summary or "", url, canonical_url or "", classification or ""]).lower()


def _find_unknown_template_variables(payload: NotificationWebhookWrite) -> set[str]:
    template_fragments = [payload.url_template]

    for field in payload.query_params:
        template_fragments.extend([field.key, field.value])
    for field in payload.headers:
        template_fragments.extend([field.key, field.value])
    for field in payload.body_fields:
        template_fragments.extend([field.key, field.value])
    if payload.body_template:
        template_fragments.append(payload.body_template)

    unknown: set[str] = set()
    for fragment in template_fragments:
        for match in TEMPLATE_PATTERN.findall(fragment):
            if match not in TEMPLATE_VARIABLE_KEYS:
                unknown.add(match)
    return unknown


def _render_field(field: NotificationWebhookField, context: dict[str, str]) -> NotificationWebhookField:
    return NotificationWebhookField(
        key=_render_template(field.key, context),
        value=_render_template(field.value, context),
    )


def _render_template(template: str, context: dict[str, str]) -> str:
    def replace(match):
        variable_name = match.group(1).strip()
        if variable_name not in context:
            raise TemplateRenderError(f"Unknown template variable: {variable_name}")
        return context[variable_name]

    return TEMPLATE_PATTERN.sub(replace, template)


def _assign_nested_json_value(target: dict, key_path: str, value: str) -> None:
    if not key_path.strip():
        raise TemplateRenderError("JSON body field key cannot be empty")

    parts = [part for part in key_path.split(".") if part]
    if not parts:
        raise TemplateRenderError("JSON body field key cannot be empty")

    cursor = target
    for part in parts[:-1]:
        existing = cursor.get(part)
        if existing is None:
            existing = {}
            cursor[part] = existing
        elif not isinstance(existing, dict):
            raise TemplateRenderError(f"Conflicting JSON body field path: {key_path}")
        cursor = existing
    cursor[parts[-1]] = value


def _default_raw_content_type(body_text: str) -> str:
    stripped = body_text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "application/json"
    return "text/plain; charset=utf-8"


def _canonicalize_headers(fields: list[NotificationWebhookField]) -> dict[str, str]:
    canonical_headers: dict[str, str] = {}
    seen_names: set[str] = set()

    for field in fields:
        header_name = field.key.strip()
        if not header_name:
            raise TemplateRenderError("Header name cannot be empty")

        normalized_name = header_name.lower()
        if normalized_name in BLOCKED_REQUEST_HEADERS:
            raise TemplateRenderError(f"Header is not allowed: {header_name}")
        if normalized_name in seen_names:
            raise TemplateRenderError(f"Duplicate header: {header_name}")

        seen_names.add(normalized_name)
        canonical_headers[_canonical_header_name(header_name)] = field.value

    return canonical_headers


def _canonical_header_name(header_name: str) -> str:
    return "-".join(part[:1].upper() + part[1:] for part in header_name.split("-"))


def _send_rendered_notification_request(rendered: RenderedNotificationRequest) -> NotificationWebhookTestResponse:
    timeout = httpx.Timeout(
        connect=rendered.timeout_seconds,
        read=rendered.timeout_seconds,
        write=rendered.timeout_seconds,
        pool=rendered.timeout_seconds,
    )
    started_at = time.perf_counter()

    try:
        with build_safe_http_client(
            timeout=timeout,
            headers={"User-Agent": settings.fetch_user_agent},
            allow_private_network=settings.allow_private_network_webhooks,
        ) as client:
            response = _send_request_with_redirects(
                client,
                method=rendered.method,
                url=rendered.url,
                headers=rendered.headers_dict,
                params=rendered.query_param_pairs,
                json_body=rendered.json_body,
                form_body=rendered.form_body,
                raw_body=rendered.raw_body,
            )
    except (TemplateRenderError, SafeFetchError, httpx.HTTPError, ValueError) as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        return NotificationWebhookTestResponse(
            success=False,
            status_code=None,
            duration_ms=duration_ms,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=None,
            error=str(exc),
        )

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        response_body_preview = _read_response_preview(response, max_bytes=MAX_RESPONSE_PREVIEW_CHARS)
        return NotificationWebhookTestResponse(
            success=200 <= response.status_code < 400,
            status_code=response.status_code,
            duration_ms=duration_ms,
            rendered_url=str(response.request.url),
            rendered_method=response.request.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=response_body_preview,
            error=None if 200 <= response.status_code < 400 else f"HTTP {response.status_code}",
        )
    finally:
        response.close()


def _read_response_preview(response: httpx.Response, *, max_bytes: int) -> str:
    preview_chunks: list[bytes] = []
    remaining = max_bytes

    for chunk in response.iter_bytes():
        if remaining <= 0:
            break

        if len(chunk) <= remaining:
            preview_chunks.append(chunk)
            remaining -= len(chunk)
            continue

        preview_chunks.append(chunk[:remaining])
        remaining = 0
        break

    return b"".join(preview_chunks).decode("utf-8", errors="replace")


def _send_request_with_redirects(
    client: httpx.Client,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    params: list[tuple[str, str]],
    json_body: dict | None,
    form_body: list[tuple[str, str]] | None,
    raw_body: bytes | None,
) -> httpx.Response:
    current_url = url
    current_method = method.upper()
    redirects = 0
    current_json_body = json_body
    current_form_body = form_body
    current_raw_body = raw_body
    current_params = list(params)

    while True:
        ensure_runtime_fetchable_url(current_url, allow_private_network=settings.allow_private_network_webhooks)
        request_url = _merge_request_url(current_url, current_params)
        request = client.build_request(
            current_method,
            request_url,
            headers=headers,
            json=current_json_body,
            data=current_form_body if current_form_body is not None else current_raw_body,
        )
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("location")
        if not location:
            response.close()
            raise RedirectError("Redirect missing location header")

        redirects += 1
        if redirects > settings.outbound_max_redirects:
            response.close()
            raise RedirectError("Too many redirects")

        redirect_status = response.status_code
        response.close()
        redirect_url = urljoin(current_url, location)
        if _origin_tuple(redirect_url) != _origin_tuple(current_url):
            raise RedirectError("Cross-origin redirects are not allowed")
        current_url = redirect_url
        current_params = []
        if redirect_status in {301, 302, 303} and current_method not in {"GET", "HEAD"}:
            current_method = "GET"
            current_json_body = None
            current_form_body = None
            current_raw_body = None


def _merge_request_url(url: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return url

    split = urlsplit(url)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)
    query_pairs.extend(params)
    merged_query = urlencode(query_pairs, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path, merged_query, split.fragment))


def _origin_tuple(url: str) -> tuple[str, str, int | None]:
    split = urlsplit(url)
    try:
        port = split.port
    except ValueError as exc:
        raise RedirectError("Redirect target URL is invalid") from exc

    scheme = split.scheme.lower()
    if port is None:
        if scheme == "http":
            port = 80
        elif scheme == "https":
            port = 443

    return scheme, (split.hostname or "").lower(), port


def _record_notification_webhook_delivery(
    db: Session,
    *,
    webhook: NotificationWebhook,
    event_type: NotificationEventType,
    rendered: RenderedNotificationRequest,
    result: NotificationWebhookTestResponse,
    delivery_kind: str,
    item_id: uuid.UUID | None,
    feed_id: uuid.UUID | None,
    item_title: str | None,
    feed_name: str | None,
) -> NotificationWebhookDelivery:
    delivery = NotificationWebhookDelivery(
        webhook_id=webhook.id,
        user_id=webhook.user_id,
        event_type_snapshot=event_type,
        item_id=item_id,
        feed_id=feed_id,
        delivery_kind=delivery_kind,
        success=result.success,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        timeout_seconds=rendered.timeout_seconds,
        rendered_url=result.rendered_url,
        rendered_method=result.rendered_method,
        rendered_headers_json=[field.model_dump() for field in result.rendered_headers],
        rendered_query_params_json=[field.model_dump() for field in result.rendered_query_params],
        rendered_body=result.rendered_body,
        response_body_preview=result.response_body_preview,
        error=result.error,
        item_title_snapshot=item_title,
        feed_name_snapshot=feed_name,
    )
    db.add(delivery)
    db.flush()
    return delivery


def _rendered_request_from_delivery(delivery: NotificationWebhookDelivery) -> RenderedNotificationRequest:
    rendered_headers = [
        NotificationWebhookField.model_validate(entry) for entry in (delivery.rendered_headers_json or [])
    ]
    rendered_query_params = [
        NotificationWebhookField.model_validate(entry) for entry in (delivery.rendered_query_params_json or [])
    ]
    body_text = delivery.rendered_body

    return RenderedNotificationRequest(
        method=delivery.rendered_method,
        url=delivery.rendered_url,
        headers=rendered_headers,
        query_params=rendered_query_params,
        body=body_text,
        headers_dict=_canonicalize_headers(rendered_headers),
        query_param_pairs=[],
        json_body=None,
        form_body=None,
        raw_body=body_text.encode("utf-8") if body_text is not None else None,
        timeout_seconds=delivery.timeout_seconds,
    )
