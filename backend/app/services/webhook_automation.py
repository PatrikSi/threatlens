"""Versioned automation payloads and event routing without fetching live evidence."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from sqlalchemy import select
from pydantic import ValidationError

from app.models.integration import IntegrationEvent
from app.schemas.webhook_automation import WebhookConditionGroup
from app.services.webhook_conditions import evaluate_conditions, event_condition_values
from app.services.team_access import team_access_predicate

AUTOMATION_EVENTS = frozenset(
    {"intel.extraction.ready", "intel.indicators.changed", "hunt.approved"}
)
MAX_AUTOMATION_BYTES = 270_336


def automation_envelope(
    event: IntegrationEvent, *, payload: dict | None = None
) -> dict:
    data = payload if payload is not None else event.payload_json
    result = {
        "schema_version": "threatlens.automation.v1",
        "event_id": str(event.id),
        "event_type": event.event_type,
        "occurred_at": event.created_at.isoformat(),
        "action_id": str(data.get("action_id") or event.id),
        "source": {"type": event.source_type, "id": str(event.source_id)},
        "data": data,
    }
    if (
        len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        > MAX_AUTOMATION_BYTES
    ):
        raise ValueError("Automation event exceeds the bounded payload limit")
    return result


def event_matches_webhook(db, *, event: IntegrationEvent, webhook) -> bool:
    if event.event_type != webhook.event_type:
        return False
    from app.services.integration_events import delivery_payload_for_owner
    from app.services.integration_connectors.base import IntegrationEventContextError

    try:
        payload = delivery_payload_for_owner(event, owner_user_id=webhook.user_id)
    except IntegrationEventContextError:
        return False
    if (
        event.event_type not in {"daily_digest", "report_ready"}
        and webhook.feed_scope == "selected"
        and str(payload.get("feed_id")) not in (webhook.feed_ids_json or [])
    ):
        return False
    if (
        event.event_type in AUTOMATION_EVENTS
        and payload.get("indicators_complete") is False
    ):
        return False
    if event.event_type == "hunt.approved" or payload.get("team_id") is not None:
        try:
            team_id = uuid.UUID(str(payload.get("team_id")))
        except (TypeError, ValueError):
            return False
        if not db.scalar(select(team_access_predicate(team_id, webhook.user_id))):
            return False
    if event.event_type in AUTOMATION_EVENTS:
        from app.services.intel_event_eligibility import automation_event_current

        if not automation_event_current(db, payload, event.event_type):
            return False
    try:
        condition = (
            WebhookConditionGroup.model_validate(webhook.conditions_json)
            if webhook.conditions_json
            else None
        )
    except ValidationError as exc:
        raise IntegrationEventContextError(
            "Saved webhook conditions are invalid; edit the subscription before retrying"
        ) from exc
    return evaluate_conditions(
        condition,
        event_condition_values(
            payload, created_at=event.created_at, event_type=event.event_type
        ),
    )[0]


def reserve_automation_deliveries(db, *, event: IntegrationEvent, webhooks):
    from app.models.user import User
    from app.services.notification_webhooks import (
        NotificationDeliveryReservationBatch,
        reserve_notification_webhook_delivery,
        has_recent_notification_delivery,
        try_acquire_notification_delivery_lock,
    )

    ids = []
    payload = event.payload_json
    item_data = payload.get("item") or {}
    feed_data = payload.get("feed") or {}
    item = SimpleNamespace(
        id=uuid.UUID(payload["item_id"]),
        title=item_data.get("title", "Intelligence update"),
        **{
            key: item_data.get(key)
            for key in ("summary", "url", "canonical_url", "status")
        },
    )
    feed = SimpleNamespace(
        id=uuid.UUID(payload["feed_id"]),
        name=feed_data.get("name", "Source feed"),
        url=feed_data.get("url", ""),
    )
    scope = f"automation:{event.id}"
    for webhook in webhooks:
        if not event_matches_webhook(db, event=event, webhook=webhook):
            continue
        user = db.get(User, webhook.user_id)
        if user is None or not user.is_active or not user.is_approved:
            continue
        if not try_acquire_notification_delivery_lock(
            db,
            webhook_id=webhook.id,
            event_type=event.event_type,
            item_id=item.id,
            scope_key=scope,
        ):
            raise RuntimeError(
                "Automation event routing is waiting for its delivery lock"
            )
        if has_recent_notification_delivery(
            db,
            webhook_id=webhook.id,
            event_type=event.event_type,
            item_id=item.id,
            scope_key=scope,
        ):
            continue
        delivery = reserve_notification_webhook_delivery(
            db,
            webhook=webhook,
            user=user,
            event_type=event.event_type,
            item=item,
            feed=feed,
            scope_key=scope,
        )
        ids.append(delivery.id)
    return NotificationDeliveryReservationBatch(
        delivery_ids=ids,
        matched_webhooks=len(webhooks),
        skipped=len(webhooks) - len(ids),
    )


def store_automation_snapshot(delivery, event, *, payload: dict) -> None:
    from app.services.notification_webhook_storage import encrypt_notification_text

    delivery.rendered_body = encrypt_notification_text(
        json.dumps(
            automation_envelope(event, payload=payload),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    delivery.rendered_method = "POST"
    from app.schemas.notification import NotificationWebhookField
    from app.services.notification_webhook_storage import (
        notification_fields_from_storage,
        notification_fields_to_storage,
    )

    headers = [
        field
        for field in notification_fields_from_storage(delivery.rendered_headers_json)
        if field.key.lower() != "content-type"
    ]
    headers.append(
        NotificationWebhookField(key="Content-Type", value="application/json")
    )
    delivery.rendered_headers_json = notification_fields_to_storage(headers)
