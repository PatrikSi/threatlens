from __future__ import annotations

import uuid

from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.notification import (
    NotificationWebhookDeliveryResponse,
    NotificationWebhookField,
    NotificationWebhookResponse,
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services.secret_storage import (
    decrypt_json,
    decrypt_text,
    encrypt_json,
    encrypt_json_if_legacy,
    encrypt_text,
    encrypt_text_if_legacy,
)
from app.services.url_utils import redact_feed_url

SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    }
)
RENDER_FAILURE_ERROR_PREFIX = "render_error:"
POLICY_FAILURE_ERROR_PREFIX = "policy_error:"


def decrypt_notification_text(value: str | None) -> str | None:
    return decrypt_text(value)


def decrypt_notification_json(value):
    return decrypt_json(value)


def encrypt_notification_text(value: str | None) -> str | None:
    return encrypt_text(value)


def encrypt_notification_json(value) -> dict[str, str]:
    return encrypt_json(value)


def upgrade_notification_webhook_secret_storage(webhook: NotificationWebhook) -> bool:
    changed = False

    webhook.url_template, updated = encrypt_text_if_legacy(webhook.url_template)
    changed = changed or updated
    webhook.query_params_json, updated = encrypt_json_if_legacy(webhook.query_params_json)
    changed = changed or updated
    webhook.headers_json, updated = encrypt_json_if_legacy(webhook.headers_json)
    changed = changed or updated
    webhook.body_fields_json, updated = encrypt_json_if_legacy(webhook.body_fields_json)
    changed = changed or updated
    webhook.body_template, updated = encrypt_text_if_legacy(webhook.body_template)
    changed = changed or updated

    return changed


def upgrade_notification_webhook_delivery_secret_storage(delivery: NotificationWebhookDelivery) -> bool:
    changed = False

    delivery.rendered_url, updated = encrypt_text_if_legacy(delivery.rendered_url)
    changed = changed or updated
    delivery.rendered_headers_json, updated = encrypt_json_if_legacy(delivery.rendered_headers_json)
    changed = changed or updated
    delivery.rendered_query_params_json, updated = encrypt_json_if_legacy(delivery.rendered_query_params_json)
    changed = changed or updated
    delivery.rendered_body, updated = encrypt_text_if_legacy(delivery.rendered_body)
    changed = changed or updated
    delivery.response_body_preview, updated = encrypt_text_if_legacy(delivery.response_body_preview)
    changed = changed or updated

    return changed


def notification_fields_from_storage(value) -> list[NotificationWebhookField]:
    decrypted = decrypt_notification_json(value) or []
    return [NotificationWebhookField.model_validate(entry) for entry in decrypted]


def notification_fields_to_storage(fields: list[NotificationWebhookField]) -> dict[str, str]:
    return encrypt_notification_json([field.model_dump() for field in fields])


def notification_feed_ids_from_storage(value) -> list[uuid.UUID]:
    return [uuid.UUID(entry) for entry in (value or [])]


def is_sensitive_header_name(header_name: str) -> bool:
    lowered = header_name.strip().lower().replace("_", "-")
    if lowered in SENSITIVE_HEADER_NAMES:
        return True
    return any(marker in lowered for marker in ("token", "secret", "password", "signature", "credential", "auth"))


def redact_notification_field_values(fields: list[NotificationWebhookField]) -> list[NotificationWebhookField]:
    redacted: list[NotificationWebhookField] = []
    for field in fields:
        value = "REDACTED" if is_sensitive_header_name(field.key) else field.value
        redacted.append(NotificationWebhookField(key=field.key, value=value))
    return redacted


def redact_notification_query_params(fields: list[NotificationWebhookField]) -> list[NotificationWebhookField]:
    redacted: list[NotificationWebhookField] = []
    for field in fields:
        lowered = field.key.strip().lower().replace("-", "_")
        if any(marker in lowered for marker in ("token", "secret", "password", "credential", "signature", "auth")):
            redacted.append(NotificationWebhookField(key=field.key, value="REDACTED"))
            continue
        redacted.append(field)
    return redacted


def redact_delivery_body_preview(value: str | None) -> str | None:
    if value is None:
        return None
    return f"Stored body withheld ({len(value)} chars)"


def notification_error_for_display(error: str | None) -> str | None:
    if error is None:
        return None
    for prefix in (RENDER_FAILURE_ERROR_PREFIX, POLICY_FAILURE_ERROR_PREFIX):
        if error.startswith(prefix):
            return error[len(prefix) :]
    return error


def redact_notification_test_response(
    result: NotificationWebhookTestResponse,
) -> NotificationWebhookTestResponse:
    return NotificationWebhookTestResponse(
        success=result.success,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        rendered_url=redact_feed_url(result.rendered_url),
        rendered_method=result.rendered_method,
        rendered_headers=redact_notification_field_values(result.rendered_headers),
        rendered_query_params=redact_notification_query_params(result.rendered_query_params),
        rendered_body=redact_delivery_body_preview(result.rendered_body),
        response_body_preview=redact_delivery_body_preview(result.response_body_preview),
        error=notification_error_for_display(result.error),
    )


def notification_webhook_write_from_model(webhook: NotificationWebhook) -> NotificationWebhookWrite:
    upgrade_notification_webhook_secret_storage(webhook)
    return NotificationWebhookWrite(
        name=webhook.name,
        enabled=webhook.enabled,
        event_type=webhook.event_type,
        url_template=decrypt_notification_text(webhook.url_template) or "",
        method=webhook.method,
        feed_scope=webhook.feed_scope,
        feed_ids=notification_feed_ids_from_storage(webhook.feed_ids_json),
        query_params=notification_fields_from_storage(webhook.query_params_json),
        headers=notification_fields_from_storage(webhook.headers_json),
        body_mode=webhook.body_mode,
        body_fields=notification_fields_from_storage(webhook.body_fields_json),
        body_template=decrypt_notification_text(webhook.body_template),
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
    upgrade_notification_webhook_delivery_secret_storage(delivery)
    rendered_headers = redact_notification_field_values(notification_fields_from_storage(delivery.rendered_headers_json))
    rendered_query_params = redact_notification_query_params(
        notification_fields_from_storage(delivery.rendered_query_params_json)
    )
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
        delivery_state=delivery.delivery_state,
        attempt_count=delivery.attempt_count,
        not_before=delivery.not_before,
        claimed_at=delivery.claimed_at,
        success=delivery.success,
        status_code=delivery.status_code,
        duration_ms=delivery.duration_ms,
        timeout_seconds=delivery.timeout_seconds,
        rendered_url=redact_feed_url(decrypt_notification_text(delivery.rendered_url)),
        rendered_method=delivery.rendered_method,
        rendered_headers=rendered_headers,
        rendered_query_params=rendered_query_params,
        rendered_body=redact_delivery_body_preview(decrypt_notification_text(delivery.rendered_body)),
        response_body_preview=redact_delivery_body_preview(decrypt_notification_text(delivery.response_body_preview)),
        error=notification_error_for_display(delivery.error),
        attempted_at=delivery.attempted_at,
    )


def build_notification_webhook(user_id: uuid.UUID, payload: NotificationWebhookWrite) -> NotificationWebhook:
    return NotificationWebhook(
        user_id=user_id,
        name=payload.name,
        enabled=payload.enabled,
        event_type=payload.event_type,
        url_template=encrypt_notification_text(payload.url_template) or "",
        method=payload.method,
        feed_scope=payload.feed_scope,
        feed_ids_json=[str(feed_id) for feed_id in payload.feed_ids],
        query_params_json=notification_fields_to_storage(payload.query_params),
        headers_json=notification_fields_to_storage(payload.headers),
        body_mode=payload.body_mode,
        body_fields_json=notification_fields_to_storage(payload.body_fields),
        body_template=encrypt_notification_text(payload.body_template),
        timeout_seconds=payload.timeout_seconds,
    )


def apply_notification_webhook_updates(webhook: NotificationWebhook, payload: NotificationWebhookWrite) -> None:
    webhook.name = payload.name
    webhook.enabled = payload.enabled
    webhook.event_type = payload.event_type
    webhook.url_template = encrypt_notification_text(payload.url_template) or ""
    webhook.method = payload.method
    webhook.feed_scope = payload.feed_scope
    webhook.feed_ids_json = [str(feed_id) for feed_id in payload.feed_ids]
    webhook.query_params_json = notification_fields_to_storage(payload.query_params)
    webhook.headers_json = notification_fields_to_storage(payload.headers)
    webhook.body_mode = payload.body_mode
    webhook.body_fields_json = notification_fields_to_storage(payload.body_fields)
    webhook.body_template = encrypt_notification_text(payload.body_template)
    webhook.timeout_seconds = payload.timeout_seconds
