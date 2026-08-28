from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.integration_compat import lock_notification_webhook


def lock_webhook_replay_context(
    db: Session,
    *,
    source_snapshot: IntegrationDelivery,
) -> tuple[IntegrationDelivery, NotificationWebhookDelivery]:
    legacy_id, webhook_id = _find_webhook_replay_identity(
        db,
        source_snapshot=source_snapshot,
    )
    webhook = lock_notification_webhook(db, webhook_id, refresh_existing=True)
    if webhook is None:
        raise _missing_history_error()

    legacy = db.scalar(
        select(NotificationWebhookDelivery)
        .where(NotificationWebhookDelivery.id == legacy_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if legacy is None or legacy.webhook_id != webhook.id:
        raise _missing_history_error()

    source = db.scalar(
        select(IntegrationDelivery)
        .where(IntegrationDelivery.id == source_snapshot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise ValueError("Integration delivery not found")
    if source.connector_type != "webhook":
        raise _missing_history_error()
    if legacy.integration_delivery_id not in {None, source.id}:
        raise _missing_history_error()
    if (
        legacy.integration_delivery_id is None
        and _legacy_delivery_id_from_payload(source) != legacy.id
    ):
        raise _missing_history_error()
    return source, legacy


def clone_webhook_replay(
    *,
    source: NotificationWebhookDelivery,
    replay_id: uuid.UUID,
) -> NotificationWebhookDelivery:
    return NotificationWebhookDelivery(
        id=replay_id,
        integration_delivery_id=replay_id,
        webhook_id=source.webhook_id,
        user_id=source.user_id,
        event_type_snapshot=source.event_type_snapshot,
        item_id=source.item_id,
        feed_id=source.feed_id,
        source_delivery_id=source.source_delivery_id or source.id,
        scope_key=source.scope_key,
        delivery_kind="retry",
        delivery_state="pending",
        attempt_count=0,
        not_before=None,
        claimed_at=None,
        success=False,
        status_code=None,
        duration_ms=None,
        timeout_seconds=source.timeout_seconds,
        rendered_url=source.rendered_url,
        rendered_method=source.rendered_method,
        rendered_headers_json=copy.deepcopy(source.rendered_headers_json or []),
        rendered_query_params_json=copy.deepcopy(
            source.rendered_query_params_json or []
        ),
        rendered_body=source.rendered_body,
        response_body_preview=None,
        error=None,
        item_title_snapshot=source.item_title_snapshot,
        feed_name_snapshot=source.feed_name_snapshot,
        attempted_at=datetime.now(timezone.utc),
    )


def _find_webhook_replay_identity(
    db: Session,
    *,
    source_snapshot: IntegrationDelivery,
) -> tuple[uuid.UUID, uuid.UUID]:
    linked = db.execute(
        select(
            NotificationWebhookDelivery.id,
            NotificationWebhookDelivery.webhook_id,
        ).where(
            NotificationWebhookDelivery.integration_delivery_id
            == source_snapshot.id
        )
    ).one_or_none()
    if linked is not None:
        return linked.id, linked.webhook_id

    legacy_id = _legacy_delivery_id_from_payload(source_snapshot)
    candidate = db.execute(
        select(
            NotificationWebhookDelivery.id,
            NotificationWebhookDelivery.webhook_id,
            NotificationWebhookDelivery.integration_delivery_id,
        ).where(NotificationWebhookDelivery.id == legacy_id)
    ).one_or_none()
    if candidate is None or candidate.integration_delivery_id not in {
        None,
        source_snapshot.id,
    }:
        raise _missing_history_error()
    return candidate.id, candidate.webhook_id


def _legacy_delivery_id_from_payload(source: IntegrationDelivery) -> uuid.UUID:
    payload = source.payload_json if isinstance(source.payload_json, dict) else {}
    candidate = payload.get("legacy_webhook_delivery_id")
    try:
        return uuid.UUID(str(candidate))
    except (TypeError, ValueError):
        return source.id


def _missing_history_error() -> ValueError:
    return ValueError("Webhook delivery history is unavailable and cannot be replayed")
