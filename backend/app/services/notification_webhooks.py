from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlencode, urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.user import User
from app.schemas.notification import (
    NotificationTemplateVariable,
    NotificationWebhookField,
    NotificationWebhookResponse,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.safe_fetch import REDIRECT_STATUS_CODES, RedirectError, SafeFetchError
from app.services.url_utils import ensure_runtime_fetchable_url

logger = logging.getLogger(__name__)
settings = get_settings()

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
    NotificationTemplateVariable(key="item.id", description="New item identifier.", example="5e2db70d-0a5b-428e-a9bf-0b4be612cbab"),
    NotificationTemplateVariable(key="item.title", description="Item title.", example="New campaign targeting exposed edge devices"),
    NotificationTemplateVariable(key="item.url", description="Original item URL.", example="https://example.com/articles/campaign"),
    NotificationTemplateVariable(key="item.canonical_url", description="Canonical item URL when known.", example="https://example.com/articles/campaign"),
    NotificationTemplateVariable(key="item.summary", description="Feed summary text.", example="Researchers observed a fresh wave of exploitation."),
    NotificationTemplateVariable(key="item.status", description="ThreatLens item status.", example="new"),
    NotificationTemplateVariable(key="item.published_at", description="Published timestamp when provided by the feed.", example="2026-03-25T09:15:00+00:00"),
    NotificationTemplateVariable(key="item.first_seen_at", description="First time ThreatLens saw the item.", example="2026-03-25T09:16:02+00:00"),
)
TEMPLATE_VARIABLE_KEYS = frozenset(variable.key for variable in TEMPLATE_VARIABLES)
TEMPLATE_PATTERN = __import__("re").compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
MAX_RESPONSE_PREVIEW_CHARS = 4000


class TemplateRenderError(ValueError):
    pass


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
    feed: Feed | SimpleNamespace,
    item: Item | SimpleNamespace,
    triggered_at: datetime | None = None,
    delivery_id: uuid.UUID | None = None,
) -> RenderedNotificationRequest:
    rendered_at = triggered_at or datetime.now(timezone.utc)
    delivery_uuid = delivery_id or uuid.uuid4()
    context = _build_template_context(user=user, feed=feed, item=item, triggered_at=rendered_at, delivery_id=delivery_uuid)

    rendered_url = _render_template(payload.url_template, context)
    rendered_query_params = [_render_field(field, context) for field in payload.query_params]
    rendered_headers = [_render_field(field, context) for field in payload.headers]

    headers_dict = {field.key: field.value for field in rendered_headers}
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
        headers_dict.setdefault("Content-Type", "text/plain; charset=utf-8")

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
    rendered = render_notification_request(payload, user=user, feed=feed, item=item)
    return _send_rendered_notification_request(rendered)


def get_matching_notification_webhooks_for_feed(db: Session, *, feed_id: uuid.UUID) -> list[NotificationWebhook]:
    enabled_webhooks = db.scalars(
        select(NotificationWebhook)
        .where(NotificationWebhook.enabled.is_(True), NotificationWebhook.event_type == "rss_item_new")
        .order_by(NotificationWebhook.created_at.asc())
    ).all()
    return [webhook for webhook in enabled_webhooks if webhook.feed_scope == "all" or str(feed_id) in (webhook.feed_ids_json or [])]


def send_notification_webhook_for_item(db: Session, *, webhook: NotificationWebhook, item: Item, feed: Feed, user: User) -> NotificationWebhookTestResponse:
    payload = notification_webhook_write_from_model(webhook)
    rendered = render_notification_request(payload, user=user, feed=feed, item=item)
    return _send_rendered_notification_request(rendered)


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


def _build_template_context(
    *,
    user: User | SimpleNamespace,
    feed: Feed | SimpleNamespace,
    item: Item | SimpleNamespace,
    triggered_at: datetime,
    delivery_id: uuid.UUID,
) -> dict[str, str]:
    return {
        "event.type": "rss_item_new",
        "event.triggered_at": _isoformat(triggered_at),
        "event.delivery_id": str(delivery_id),
        "user.id": str(getattr(user, "id", "")),
        "user.email": getattr(user, "email", "") or "",
        "feed.id": str(getattr(feed, "id", "")),
        "feed.name": getattr(feed, "name", "") or "",
        "feed.url": getattr(feed, "url", "") or "",
        "feed.site_url": getattr(feed, "site_url", "") or "",
        "item.id": str(getattr(item, "id", "")),
        "item.title": getattr(item, "title", "") or "",
        "item.url": getattr(item, "url", "") or "",
        "item.canonical_url": getattr(item, "canonical_url", "") or "",
        "item.summary": getattr(item, "summary", "") or "",
        "item.status": getattr(item, "status", "") or "",
        "item.published_at": _isoformat(getattr(item, "published_at", None)),
        "item.first_seen_at": _isoformat(getattr(item, "first_seen_at", None)),
    }


def _isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


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


def _send_rendered_notification_request(rendered: RenderedNotificationRequest) -> NotificationWebhookTestResponse:
    timeout = httpx.Timeout(
        connect=rendered.timeout_seconds,
        read=rendered.timeout_seconds,
        write=rendered.timeout_seconds,
        pool=rendered.timeout_seconds,
    )
    started_at = time.perf_counter()

    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": settings.fetch_user_agent}) as client:
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
    response_body_preview = response.text[:MAX_RESPONSE_PREVIEW_CHARS]
    return NotificationWebhookTestResponse(
        success=200 <= response.status_code < 400,
        status_code=response.status_code,
        duration_ms=duration_ms,
        rendered_url=str(response.request.url),
        rendered_method=rendered.method,
        rendered_headers=rendered.headers,
        rendered_query_params=rendered.query_params,
        rendered_body=rendered.body,
        response_body_preview=response_body_preview,
        error=None if 200 <= response.status_code < 400 else f"HTTP {response.status_code}",
    )


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

    while True:
        ensure_runtime_fetchable_url(current_url, allow_private_network=settings.allow_private_network_fetch)
        response = client.request(
            current_method,
            current_url,
            headers=headers,
            params=params,
            json=current_json_body,
            data=current_form_body if current_form_body is not None else current_raw_body,
            follow_redirects=False,
        )
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
        current_url = urljoin(current_url, location)
        if redirect_status in {301, 302, 303} and current_method not in {"GET", "HEAD"}:
            current_method = "GET"
            current_json_body = None
            current_form_body = None
            current_raw_body = None
