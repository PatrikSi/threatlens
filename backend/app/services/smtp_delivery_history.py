from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.integration import IntegrationInstance
from app.models.item import Item
from app.schemas.notification import NotificationEventType
from app.services.audit import record_audit
from app.services.integration_storage import (
    INTEGRATION_HEALTH_ERROR,
    INTEGRATION_HEALTH_HEALTHY,
)
from app.services.notification_webhooks import try_acquire_notification_delivery_lock
from app.services.smtp_delivery_results import SMTPNotificationResult

SMTP_DELIVERY_AUDIT_ACTION = "integrations.smtp.delivery"
SMTP_DELIVERY_RESOURCE_TYPE = "integration_instance"


def record_smtp_delivery_audit(
    db: Session,
    *,
    instance: IntegrationInstance,
    result: SMTPNotificationResult,
    event_type: NotificationEventType,
    delivery_kind: str,
    dedupe_key: str,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    source_delivery_id: uuid.UUID | None,
    scope_key: str | None,
) -> None:
    metadata = {
        "event_type": event_type,
        "delivery_kind": delivery_kind,
        "delivery_id": str(result.delivery_id),
        "dedupe_key": dedupe_key,
        "recipient_count": result.recipient_count,
        "accepted_count": result.accepted_count,
        "delivery_outcome": result.delivery_outcome,
        "refused_count": len(result.refused_recipients),
        "unknown_count": len(result.unknown_recipients),
        "duration_ms": result.duration_ms,
        "error_code": result.error_code,
        "error": result.error,
        "has_server_message": bool(result.server_message),
        "feed_id": str(getattr(feed, "id", "")) or None,
        "item_id": str(getattr(item, "id", "")) or None,
        "source_delivery_id": str(source_delivery_id) if source_delivery_id else None,
        "scope_key": scope_key,
    }
    record_audit(
        db,
        actor_user_id=None,
        action=SMTP_DELIVERY_AUDIT_ACTION,
        resource_type=SMTP_DELIVERY_RESOURCE_TYPE,
        resource_id=str(instance.id),
        success=result.success,
        metadata=metadata,
    )


def apply_smtp_delivery_result(
    instance: IntegrationInstance, result: SMTPNotificationResult
) -> None:
    if result.success:
        instance.health_status = INTEGRATION_HEALTH_HEALTHY
        instance.last_success_at = result.attempted_at
        instance.last_error = None
    else:
        instance.health_status = INTEGRATION_HEALTH_ERROR
        instance.last_error_at = result.attempted_at
        instance.last_error = result.error


def smtp_delivery_attempt_skip_reason(
    db: Session,
    *,
    instance_id: uuid.UUID,
    dedupe_key: str,
    event_type: NotificationEventType,
    delivery_kind: str,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    source_delivery_id: uuid.UUID | None,
    scope_key: str | None,
) -> str | None:
    if _has_smtp_delivery_attempt(db, instance_id=instance_id, dedupe_key=dedupe_key):
        return "duplicate_delivery"
    if not try_acquire_notification_delivery_lock(
        db,
        webhook_id=instance_id,
        event_type=event_type,
        delivery_kind=delivery_kind,
        item_id=getattr(item, "id", None),
        feed_id=getattr(feed, "id", None),
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
    ):
        return "delivery_lock_unavailable"
    if _has_smtp_delivery_attempt(db, instance_id=instance_id, dedupe_key=dedupe_key):
        return "duplicate_delivery"
    return None


def _has_smtp_delivery_attempt(
    db: Session, *, instance_id: uuid.UUID, dedupe_key: str
) -> bool:
    return (
        db.scalar(
            select(AuditLog.id)
            .where(
                AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION,
                AuditLog.resource_type == SMTP_DELIVERY_RESOURCE_TYPE,
                AuditLog.resource_id == str(instance_id),
                AuditLog.metadata_json["dedupe_key"].as_string() == dedupe_key,
            )
            .limit(1)
        )
        is not None
    )


def smtp_delivery_dedupe_key(
    *,
    instance_id: uuid.UUID,
    event_type: NotificationEventType,
    delivery_kind: str,
    item_id: uuid.UUID | None,
    feed_id: uuid.UUID | None,
    source_delivery_id: uuid.UUID | None,
    scope_key: str | None,
) -> str:
    return "|".join(
        [
            "smtp",
            str(instance_id),
            event_type,
            delivery_kind,
            f"item:{item_id or ''}",
            f"feed:{feed_id or ''}",
            f"source:{source_delivery_id or ''}",
            f"scope:{scope_key or ''}",
        ]
    )
