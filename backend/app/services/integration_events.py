from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
)
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.notification import NotificationEventType
from app.services.integration_connectors import IntegrationEventContextError
from app.services.integration_registry import iter_integration_connectors

settings = get_settings()

EVENT_PENDING = "pending"
EVENT_ROUTING = "routing"
EVENT_ROUTED = "routed"
EVENT_FAILED = "failed"
EVENT_DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class RoutedIntegrationEvent:
    event_id: uuid.UUID
    status: str
    webhook_delivery_ids: list[uuid.UUID]
    integration_delivery_ids: list[uuid.UUID]


def emit_integration_event(
    db: Session,
    *,
    event_type: NotificationEventType,
    source_type: str,
    source_id: str | uuid.UUID | None,
    idempotency_key: str,
    payload: dict,
    actor_user_id: uuid.UUID | None = None,
    available_at: datetime | None = None,
) -> IntegrationEvent:
    existing = db.scalar(select(IntegrationEvent).where(IntegrationEvent.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    event = IntegrationEvent(
        event_type=event_type,
        source_type=source_type,
        source_id=str(source_id) if source_id is not None else None,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        payload_json=dict(payload),
        routing_state=EVENT_PENDING,
        available_at=available_at or datetime.now(timezone.utc),
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(IntegrationEvent).where(IntegrationEvent.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    return event


def route_integration_event(db: Session, *, event_id: uuid.UUID) -> RoutedIntegrationEvent:
    event = db.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise IntegrationEventContextError("Integration event not found")
    if event.routing_state == EVENT_ROUTED:
        return _routed_event_result(db, event)
    if event.routing_state == EVENT_DEAD_LETTER:
        return RoutedIntegrationEvent(event.id, EVENT_DEAD_LETTER, [], [])

    event.routing_state = EVENT_ROUTING
    event.claimed_at = datetime.now(timezone.utc)
    event.routing_attempt_count = max(0, int(event.routing_attempt_count or 0)) + 1
    event.last_error = None
    db.add(event)

    compatibility_delivery_ids: list[uuid.UUID] = []
    integration_delivery_ids: list[uuid.UUID] = []
    for connector in iter_integration_connectors():
        routed = connector.route_event(db, event=event)
        integration_delivery_ids.extend(routed.delivery_ids)
        compatibility_delivery_ids.extend(routed.compatibility_delivery_ids)
    event.routing_state = EVENT_ROUTED
    event.routed_at = datetime.now(timezone.utc)
    event.claimed_at = None
    db.add(event)
    db.flush()
    return RoutedIntegrationEvent(
        event_id=event.id,
        status=EVENT_ROUTED,
        webhook_delivery_ids=compatibility_delivery_ids,
        integration_delivery_ids=integration_delivery_ids,
    )


def record_integration_event_failure(
    db: Session,
    *,
    event_id: uuid.UUID,
    error: str,
    terminal: bool,
    now: datetime | None = None,
) -> IntegrationEvent | None:
    current_time = now or datetime.now(timezone.utc)
    event = db.scalar(select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update())
    if event is None or event.routing_state == EVENT_ROUTED:
        return event
    attempts = max(0, int(event.routing_attempt_count or 0)) + 1
    max_attempts = max(1, int(settings.integration_event_routing_max_attempts))
    dead_letter = terminal or attempts >= max_attempts
    event.routing_state = EVENT_DEAD_LETTER if dead_letter else EVENT_FAILED
    event.routing_attempt_count = attempts
    event.claimed_at = None
    event.last_error = error[:4000]
    event.available_at = current_time + timedelta(seconds=_routing_backoff_seconds(attempts))
    db.add(event)
    return event


def list_recoverable_integration_event_ids(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = now or datetime.now(timezone.utc)
    stale_cutoff = current_time - timedelta(seconds=settings.integration_event_routing_stale_after_seconds)
    batch_size = max(1, int(limit or settings.integration_event_routing_batch_size))
    return list(
        db.scalars(
            select(IntegrationEvent.id)
            .where(
                or_(
                    and_(
                        IntegrationEvent.routing_state.in_([EVENT_PENDING, EVENT_FAILED]),
                        IntegrationEvent.available_at <= current_time,
                    ),
                    and_(
                        IntegrationEvent.routing_state == EVENT_ROUTING,
                        or_(IntegrationEvent.claimed_at.is_(None), IntegrationEvent.claimed_at < stale_cutoff),
                    ),
                )
            )
            .order_by(IntegrationEvent.available_at.asc(), IntegrationEvent.created_at.asc())
            .limit(batch_size)
        ).all()
    )


def _routed_event_result(db: Session, event: IntegrationEvent) -> RoutedIntegrationEvent:
    generic = db.scalars(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.delivery_kind == "live",
        )
    ).all()
    generic_ids = [delivery.id for delivery in generic]
    webhook_ids = list(
        db.scalars(
            select(NotificationWebhookDelivery.id).where(
                NotificationWebhookDelivery.integration_delivery_id.in_(generic_ids)
            )
        ).all()
    ) if generic_ids else []
    return RoutedIntegrationEvent(event.id, EVENT_ROUTED, webhook_ids, generic_ids)


def _routing_backoff_seconds(attempts: int) -> int:
    base = max(1, int(settings.integration_event_routing_backoff_seconds))
    return min(base * (2 ** max(0, attempts - 1)), 3600)
