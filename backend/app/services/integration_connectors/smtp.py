from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
    IntegrationSubscriptionFeed,
)
from app.services.integration_connectors.base import (
    ConnectorDeliveryResult,
    ConnectorRoutingResult,
    IntegrationConnectorDefinition,
    IntegrationEventContextError,
)
from app.services.integration_processors import process_smtp_integration_delivery
from app.services.integration_registry_constants import SMTP_CONFIG_SCHEMA_VERSION
from app.services.integration_storage import sync_smtp_subscriptions

settings = get_settings()


class SMTPIntegrationConnector:
    SUPPORTED_EVENT_TYPES = (
        "rss_item_new",
        "alert_match",
        "feed_failing",
        "webhook_failed",
        "daily_digest",
        "report_ready",
    )
    definition = IntegrationConnectorDefinition(
        integration_type="smtp",
        direction="destination",
        display_name="SMTP",
        description="Send event notifications and AI Daily Briefs through an SMTP server.",
        config_schema_version=SMTP_CONFIG_SCHEMA_VERSION,
        supports_test=True,
        supported_event_types=SUPPORTED_EVENT_TYPES,
        capabilities=("destination", "email", "test_connection", "test_delivery"),
    )

    def supports_event_type(self, event_type: str) -> bool:
        return event_type in self.definition.supported_event_types

    def prepare_routing(self, db: Session, *, event: IntegrationEvent) -> None:
        instances = db.scalars(
            select(IntegrationInstance).where(
                IntegrationInstance.integration_type == self.definition.integration_type,
                IntegrationInstance.enabled.is_(True),
            )
        ).all()
        for instance in instances:
            sync_smtp_subscriptions(db, instance)

    def route_event(self, db: Session, *, event: IntegrationEvent) -> ConnectorRoutingResult:
        from app.services.integration_events import delivery_payload_for_owner

        feed_id = _payload_uuid(event, "feed_id", required=False)
        query = (
            select(IntegrationSubscription, IntegrationInstance)
            .join(IntegrationInstance, IntegrationInstance.id == IntegrationSubscription.integration_id)
            .where(
                IntegrationInstance.integration_type == self.definition.integration_type,
                IntegrationInstance.enabled.is_(True),
                IntegrationSubscription.enabled.is_(True),
                IntegrationSubscription.event_type == event.event_type,
            )
        )
        if feed_id is None and event.event_type not in {"daily_digest", "report_ready"}:
            query = query.where(IntegrationSubscription.feed_scope == "all")
        elif feed_id is not None:
            query = query.outerjoin(
                IntegrationSubscriptionFeed,
                and_(
                    IntegrationSubscriptionFeed.subscription_id == IntegrationSubscription.id,
                    IntegrationSubscriptionFeed.feed_id == feed_id,
                ),
            ).where(
                or_(
                    IntegrationSubscription.feed_scope == "all",
                    IntegrationSubscriptionFeed.feed_id == feed_id,
                )
            )

        delivery_ids: list[uuid.UUID] = []
        for subscription, instance in db.execute(query).unique().all():
            existing = db.scalar(
                select(IntegrationDelivery).where(
                    IntegrationDelivery.event_id == event.id,
                    IntegrationDelivery.subscription_id == subscription.id,
                    IntegrationDelivery.delivery_kind == "live",
                )
            )
            if existing is not None:
                delivery_ids.append(existing.id)
                continue
            delivery = IntegrationDelivery(
                integration_id=instance.id,
                subscription_id=subscription.id,
                event_id=event.id,
                owner_user_id=instance.owner_user_id,
                connector_type=self.definition.integration_type,
                event_type=event.event_type,
                delivery_kind="live",
                state="pending",
                idempotency_key=f"event:{event.id}:subscription:{subscription.id}:live",
                payload_json=delivery_payload_for_owner(
                    event,
                    owner_user_id=instance.owner_user_id,
                ),
                max_attempts=max(1, int(settings.integration_delivery_retry_max_attempts)),
            )
            db.add(delivery)
            db.flush()
            delivery_ids.append(delivery.id)
        return ConnectorRoutingResult(delivery_ids=tuple(delivery_ids))

    def process_delivery(
        self,
        db: Session,
        *,
        delivery: IntegrationDelivery,
    ) -> ConnectorDeliveryResult:
        result = process_smtp_integration_delivery(db, delivery_id=delivery.id)
        return ConnectorDeliveryResult(
            delivery_id=result.delivery_id,
            status=result.status,
            reason=result.reason,
            retry_at=result.retry_at,
        )


def _payload_uuid(event: IntegrationEvent, key: str, *, required: bool = True) -> uuid.UUID | None:
    value = event.payload_json.get(key) if isinstance(event.payload_json, dict) else None
    if value is None or value == "":
        if required:
            raise IntegrationEventContextError(f"{event.event_type} event is missing {key}")
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise IntegrationEventContextError(f"{event.event_type} event has invalid {key}") from exc
