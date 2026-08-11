from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.feed import Feed
from app.models.item import Item
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationEventType,
    NotificationWebhookField,
    NotificationWebhookWrite,
)
from app.services.notification_webhook_http import (
    THREATLENS_DELIVERY_ID_HEADER,
    canonicalize_headers,
    default_raw_content_type,
)
from app.services.notification_webhook_storage import (
    decrypt_notification_text,
    notification_fields_from_storage,
    upgrade_notification_webhook_delivery_secret_storage,
)
from app.services.notification_webhook_templates import (
    AlertMatchContext,
    DailyDigestContext,
    FailedWebhookContext,
    assign_nested_json_value,
    build_template_context,
    render_field,
    render_template,
)

THREATLENS_SOURCE_DELIVERY_ID_HEADER = "X-ThreatLens-Source-Delivery-ID"


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


def render_notification_request(
    payload: NotificationWebhookWrite,
    *,
    user: User | SimpleNamespace,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    event_type: NotificationEventType | None = None,
    triggered_at: datetime | None = None,
    delivery_id: uuid.UUID | None = None,
    source_delivery_id: uuid.UUID | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
) -> RenderedNotificationRequest:
    rendered_at = triggered_at or datetime.now(timezone.utc)
    delivery_uuid = delivery_id or uuid.uuid4()
    context = build_template_context(
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

    rendered_url = render_template(payload.url_template, context)
    rendered_query_params = [
        render_field(field, context) for field in payload.query_params
    ]
    rendered_headers = [render_field(field, context) for field in payload.headers]
    headers_dict = canonicalize_headers(rendered_headers)
    apply_notification_delivery_headers(
        headers_dict,
        delivery_id=delivery_uuid,
        source_delivery_id=source_delivery_id,
    )
    query_param_pairs = [(field.key, field.value) for field in rendered_query_params]

    body_text: str | None = None
    json_body: dict | None = None
    form_body: list[tuple[str, str]] | None = None
    raw_body: bytes | None = None

    if payload.body_mode == "json":
        json_body = {}
        for rendered_field in (
            render_field(field, context) for field in payload.body_fields
        ):
            assign_nested_json_value(
                json_body, rendered_field.key, rendered_field.value
            )
        body_text = json.dumps(json_body, ensure_ascii=True)
        headers_dict.setdefault("Content-Type", "application/json")
    elif payload.body_mode == "form":
        form_body = []
        for rendered_field in (
            render_field(field, context) for field in payload.body_fields
        ):
            form_body.append((rendered_field.key, rendered_field.value))
        body_text = urlencode(form_body)
        headers_dict.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif payload.body_mode == "raw":
        body_text = render_template(payload.body_template or "", context)
        raw_body = body_text.encode("utf-8")
        headers_dict.setdefault("Content-Type", default_raw_content_type(body_text))

    rendered_headers = [
        NotificationWebhookField(key=key, value=value)
        for key, value in headers_dict.items()
    ]
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


def rendered_request_from_delivery(
    delivery: NotificationWebhookDelivery,
) -> RenderedNotificationRequest:
    upgrade_notification_webhook_delivery_secret_storage(delivery)
    rendered_headers = notification_fields_from_storage(delivery.rendered_headers_json)
    rendered_query_params = notification_fields_from_storage(
        delivery.rendered_query_params_json
    )
    saved_url = decrypt_notification_text(delivery.rendered_url) or ""
    replay_url, query_param_pairs = restore_saved_request_target(
        saved_url, rendered_query_params
    )
    headers_dict = canonicalize_headers(rendered_headers)
    apply_notification_delivery_headers(
        headers_dict,
        delivery_id=delivery.id,
        source_delivery_id=delivery.source_delivery_id,
    )
    rendered_headers = [
        NotificationWebhookField(key=key, value=value)
        for key, value in headers_dict.items()
    ]
    body_text = decrypt_notification_text(delivery.rendered_body)
    return RenderedNotificationRequest(
        method=delivery.rendered_method,
        url=replay_url,
        headers=rendered_headers,
        query_params=rendered_query_params,
        body=body_text,
        headers_dict=headers_dict,
        query_param_pairs=query_param_pairs,
        json_body=None,
        form_body=None,
        raw_body=body_text.encode("utf-8") if body_text is not None else None,
        timeout_seconds=delivery.timeout_seconds,
    )


def restore_saved_request_target(
    saved_url: str,
    rendered_query_params: list[NotificationWebhookField],
) -> tuple[str, list[tuple[str, str]]]:
    query_param_pairs = [(field.key, field.value) for field in rendered_query_params]
    if not query_param_pairs:
        return saved_url, []

    split = urlsplit(saved_url)
    saved_query_pairs = parse_qsl(split.query, keep_blank_values=True)
    if not saved_query_pairs:
        return saved_url, query_param_pairs
    if saved_query_pairs == query_param_pairs:
        return urlunsplit(
            (split.scheme, split.netloc, split.path, "", split.fragment)
        ), query_param_pairs
    return saved_url, []


def apply_notification_delivery_headers(
    headers_dict: dict[str, str],
    *,
    delivery_id: uuid.UUID,
    source_delivery_id: uuid.UUID | None,
) -> None:
    headers_dict[THREATLENS_DELIVERY_ID_HEADER] = str(delivery_id)
    if source_delivery_id is not None and source_delivery_id != delivery_id:
        headers_dict[THREATLENS_SOURCE_DELIVERY_ID_HEADER] = str(source_delivery_id)
        return
    headers_dict.pop(THREATLENS_SOURCE_DELIVERY_ID_HEADER, None)
