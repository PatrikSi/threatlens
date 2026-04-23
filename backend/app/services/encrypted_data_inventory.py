from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.feed import Feed
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.health import (
    EncryptedDataInventoryCategory,
    EncryptedDataInventoryResponse,
    EncryptedDataInventorySummary,
    EncryptedDataStartupScan,
)
from app.services.feed_storage import try_decrypt_feed_url
from app.services.notification_webhook_storage import decrypt_notification_json, decrypt_notification_text
from app.services.secret_storage import is_encrypted_json, is_encrypted_text

_startup_inventory_lock = Lock()
_startup_inventory_state = EncryptedDataStartupScan()


def scan_encrypted_data_inventory(
    db: Session,
    *,
    settings: Settings | None = None,
) -> EncryptedDataInventoryResponse:
    active_settings = settings or get_settings()
    feeds = _scan_feeds(db)
    notification_webhooks = _scan_notification_webhooks(db)
    notification_delivery_snapshots = _scan_notification_delivery_snapshots(db)
    summary = _build_summary(feeds, notification_webhooks, notification_delivery_snapshots)

    warnings: list[str] = []
    if active_settings.app_data_encryption_key_was_derived:
        warnings.append(
            "APP_DATA_ENCRYPTION_KEY is using a derived development fallback. Set an explicit persistent value before relying on durable data."
        )

    status = _resolve_inventory_status(
        total_unreadable_fields=summary.unreadable_fields,
        warnings=warnings,
    )
    return EncryptedDataInventoryResponse(
        ok=summary.unreadable_fields == 0,
        status=status,
        scanned_at=datetime.now(timezone.utc),
        warnings=warnings,
        require_explicit_app_data_encryption_key=active_settings.require_explicit_data_encryption_key,
        using_derived_app_data_encryption_key=active_settings.app_data_encryption_key_was_derived,
        startup_scan=get_startup_encrypted_data_inventory(),
        feeds=feeds,
        notification_webhooks=notification_webhooks,
        notification_delivery_snapshots=notification_delivery_snapshots,
        summary=summary,
    )


def refresh_startup_encrypted_data_inventory(
    db: Session,
    *,
    settings: Settings | None = None,
) -> EncryptedDataInventoryResponse:
    snapshot = scan_encrypted_data_inventory(db, settings=settings)
    _store_startup_inventory_success(snapshot)
    return snapshot


def get_startup_encrypted_data_inventory() -> EncryptedDataStartupScan:
    with _startup_inventory_lock:
        return _startup_inventory_state.model_copy(deep=True)


def record_startup_encrypted_data_inventory_error(error: str) -> None:
    with _startup_inventory_lock:
        _startup_inventory_state.completed_at = datetime.now(timezone.utc)
        _startup_inventory_state.status = "critical"
        _startup_inventory_state.error = error
        _startup_inventory_state.total_unreadable_records = None
        _startup_inventory_state.total_unreadable_fields = None


def _store_startup_inventory_success(snapshot: EncryptedDataInventoryResponse) -> None:
    with _startup_inventory_lock:
        _startup_inventory_state.completed_at = snapshot.scanned_at
        _startup_inventory_state.status = snapshot.status
        _startup_inventory_state.error = None
        _startup_inventory_state.total_unreadable_records = snapshot.summary.unreadable_records
        _startup_inventory_state.total_unreadable_fields = snapshot.summary.unreadable_fields


def _scan_feeds(db: Session) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = db.execute(select(Feed._url_encrypted))
    for (encrypted_url,) in rows:
        category.total_records += 1
        if not is_encrypted_text(encrypted_url):
            continue
        category.encrypted_records += 1
        category.encrypted_fields += 1
        _plaintext, error = try_decrypt_feed_url(encrypted_url)
        if error:
            category.unreadable_records += 1
            category.unreadable_fields += 1
    return category


def _scan_notification_webhooks(db: Session) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = db.execute(
        select(
            NotificationWebhook.url_template,
            NotificationWebhook.query_params_json,
            NotificationWebhook.headers_json,
            NotificationWebhook.body_fields_json,
            NotificationWebhook.body_template,
        )
    )
    for row in rows:
        category.total_records += 1
        encrypted_fields = 0
        unreadable_fields = 0
        encrypted_fields += _count_text_field(row.url_template)
        unreadable_fields += _count_unreadable_text_field(row.url_template)
        encrypted_fields += _count_json_field(row.query_params_json)
        unreadable_fields += _count_unreadable_json_field(row.query_params_json)
        encrypted_fields += _count_json_field(row.headers_json)
        unreadable_fields += _count_unreadable_json_field(row.headers_json)
        encrypted_fields += _count_json_field(row.body_fields_json)
        unreadable_fields += _count_unreadable_json_field(row.body_fields_json)
        encrypted_fields += _count_text_field(row.body_template)
        unreadable_fields += _count_unreadable_text_field(row.body_template)
        _apply_record_counts(category, encrypted_fields=encrypted_fields, unreadable_fields=unreadable_fields)
    return category


def _scan_notification_delivery_snapshots(db: Session) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = db.execute(
        select(
            NotificationWebhookDelivery.rendered_url,
            NotificationWebhookDelivery.rendered_headers_json,
            NotificationWebhookDelivery.rendered_query_params_json,
            NotificationWebhookDelivery.rendered_body,
            NotificationWebhookDelivery.response_body_preview,
        )
    )
    for row in rows:
        category.total_records += 1
        encrypted_fields = 0
        unreadable_fields = 0
        encrypted_fields += _count_text_field(row.rendered_url)
        unreadable_fields += _count_unreadable_text_field(row.rendered_url)
        encrypted_fields += _count_json_field(row.rendered_headers_json)
        unreadable_fields += _count_unreadable_json_field(row.rendered_headers_json)
        encrypted_fields += _count_json_field(row.rendered_query_params_json)
        unreadable_fields += _count_unreadable_json_field(row.rendered_query_params_json)
        encrypted_fields += _count_text_field(row.rendered_body)
        unreadable_fields += _count_unreadable_text_field(row.rendered_body)
        encrypted_fields += _count_text_field(row.response_body_preview)
        unreadable_fields += _count_unreadable_text_field(row.response_body_preview)
        _apply_record_counts(category, encrypted_fields=encrypted_fields, unreadable_fields=unreadable_fields)
    return category


def _apply_record_counts(
    category: EncryptedDataInventoryCategory,
    *,
    encrypted_fields: int,
    unreadable_fields: int,
) -> None:
    category.encrypted_fields += encrypted_fields
    category.unreadable_fields += unreadable_fields
    if encrypted_fields > 0:
        category.encrypted_records += 1
    if unreadable_fields > 0:
        category.unreadable_records += 1


def _count_text_field(value: str | None) -> int:
    return 1 if is_encrypted_text(value) else 0


def _count_json_field(value) -> int:
    return 1 if is_encrypted_json(value) else 0


def _count_unreadable_text_field(value: str | None) -> int:
    if not is_encrypted_text(value):
        return 0
    try:
        decrypt_notification_text(value)
        return 0
    except ValueError:
        return 1


def _count_unreadable_json_field(value) -> int:
    if not is_encrypted_json(value):
        return 0
    try:
        decrypt_notification_json(value)
        return 0
    except ValueError:
        return 1


def _build_summary(
    feeds: EncryptedDataInventoryCategory,
    notification_webhooks: EncryptedDataInventoryCategory,
    notification_delivery_snapshots: EncryptedDataInventoryCategory,
) -> EncryptedDataInventorySummary:
    categories = (feeds, notification_webhooks, notification_delivery_snapshots)
    return EncryptedDataInventorySummary(
        total_records=sum(category.total_records for category in categories),
        encrypted_records=sum(category.encrypted_records for category in categories),
        unreadable_records=sum(category.unreadable_records for category in categories),
        encrypted_fields=sum(category.encrypted_fields for category in categories),
        unreadable_fields=sum(category.unreadable_fields for category in categories),
    )


def _resolve_inventory_status(*, total_unreadable_fields: int, warnings: list[str]) -> str:
    if total_unreadable_fields > 0:
        return "critical"
    if warnings:
        return "warning"
    return "healthy"
