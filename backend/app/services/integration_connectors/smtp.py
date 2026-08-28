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
from app.models.user import User
from app.services.integration_connectors.base import (
    ConnectorDeliveryResult,
    ConnectorRoutingResult,
    IntegrationConnectorDefinition,
    IntegrationEventCompatibilityError,
    IntegrationEventContextError,
)
from app.services.integration_processors import process_smtp_integration_delivery
from app.services.integration_registry_constants import SMTP_CONFIG_SCHEMA_VERSION
from app.services.smtp_delivery_eligibility import (
    SMTP_SOURCE_OWNER_IDS_KEY,
    SMTPDeliverySourceCompatibilityError,
    SMTPDeliverySourceContextError,
    smtp_alert_event_source_owner_ids,
    smtp_legacy_alert_event_snapshot,
)
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
                IntegrationInstance.integration_type
                == self.definition.integration_type,
                IntegrationInstance.enabled.is_(True),
            )
        ).all()
        for instance in instances:
            sync_smtp_subscriptions(db, instance)

    def route_event(
        self, db: Session, *, event: IntegrationEvent
    ) -> ConnectorRoutingResult:
        eligible_owner_ids: frozenset[uuid.UUID] | None = None
        if event.event_type == "alert_match":
            try:
                owner_ids = smtp_alert_event_source_owner_ids(db, event=event)
            except SMTPDeliverySourceCompatibilityError as exc:
                raise IntegrationEventCompatibilityError(f"{exc.code}: {exc}") from exc
            except SMTPDeliverySourceContextError as exc:
                raise IntegrationEventContextError(f"{exc.code}: {exc}") from exc
            eligible_owner_ids = frozenset(
                db.scalars(
                    select(User.id).where(
                        User.id.in_(owner_ids),
                        User.is_active.is_(True),
                        User.is_approved.is_(True),
                    )
                ).all()
            )
            if not eligible_owner_ids:
                return ConnectorRoutingResult()

        feed_id = _payload_uuid(event, "feed_id", required=False)
        query = (
            select(IntegrationSubscription, IntegrationInstance)
            .join(
                IntegrationInstance,
                IntegrationInstance.id == IntegrationSubscription.integration_id,
            )
            .where(
                IntegrationInstance.integration_type
                == self.definition.integration_type,
                IntegrationInstance.enabled.is_(True),
                IntegrationSubscription.enabled.is_(True),
                IntegrationSubscription.event_type == event.event_type,
            )
        )
        if event.event_type == "alert_match":
            assert eligible_owner_ids is not None
            query = query.outerjoin(
                User, User.id == IntegrationInstance.owner_user_id
            ).where(
                or_(
                    IntegrationInstance.owner_user_id.is_(None),
                    and_(
                        IntegrationInstance.owner_user_id.in_(eligible_owner_ids),
                        User.is_active.is_(True),
                        User.is_approved.is_(True),
                    ),
                )
            )
        if feed_id is None and event.event_type not in {"daily_digest", "report_ready"}:
            query = query.where(IntegrationSubscription.feed_scope == "all")
        elif feed_id is not None:
            query = query.outerjoin(
                IntegrationSubscriptionFeed,
                and_(
                    IntegrationSubscriptionFeed.subscription_id
                    == IntegrationSubscription.id,
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
            delivery_owner_id = instance.owner_user_id
            payload_owner_id = delivery_owner_id
            if (
                event.event_type == "alert_match"
                and payload_owner_id is None
                and eligible_owner_ids
                and len(eligible_owner_ids) == 1
            ):
                payload_owner_id = next(iter(eligible_owner_ids))
            delivery_payload = _smtp_delivery_payload_for_owner(
                db,
                event=event,
                owner_user_id=payload_owner_id,
                eligible_owner_ids=eligible_owner_ids,
            )
            if delivery_payload is None:
                continue
            if (
                event.event_type == "alert_match"
                and SMTP_SOURCE_OWNER_IDS_KEY not in delivery_payload
            ):
                source_owner_ids = (
                    frozenset({payload_owner_id})
                    if payload_owner_id is not None
                    else eligible_owner_ids
                )
                if source_owner_ids:
                    delivery_payload[SMTP_SOURCE_OWNER_IDS_KEY] = [
                        str(owner_id) for owner_id in sorted(source_owner_ids, key=str)
                    ]
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
                owner_user_id=delivery_owner_id,
                connector_type=self.definition.integration_type,
                event_type=event.event_type,
                delivery_kind="live",
                state="pending",
                idempotency_key=f"event:{event.id}:subscription:{subscription.id}:live",
                payload_json=delivery_payload,
                max_attempts=max(
                    1, int(settings.integration_delivery_retry_max_attempts)
                ),
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


def _payload_uuid(
    event: IntegrationEvent, key: str, *, required: bool = True
) -> uuid.UUID | None:
    value = (
        event.payload_json.get(key) if isinstance(event.payload_json, dict) else None
    )
    if value is None or value == "":
        if required:
            raise IntegrationEventContextError(
                f"{event.event_type} event is missing {key}"
            )
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise IntegrationEventContextError(
            f"{event.event_type} event has invalid {key}"
        ) from exc


def _smtp_delivery_payload_for_owner(
    db: Session,
    *,
    event: IntegrationEvent,
    owner_user_id: uuid.UUID | None,
    eligible_owner_ids: frozenset[uuid.UUID] | None = None,
) -> dict | None:
    from app.services.integration_events import (
        build_alert_match_snapshot_payload,
        delivery_payload_for_global_alert,
        delivery_payload_for_owner,
    )

    if event.event_type != "alert_match":
        return delivery_payload_for_owner(event, owner_user_id=owner_user_id)
    if int(event.schema_version or 1) >= 2:
        if owner_user_id is None:
            return delivery_payload_for_global_alert(
                event, owner_user_ids=eligible_owner_ids
            )
        return delivery_payload_for_owner(event, owner_user_id=owner_user_id)
    owner_ids = (
        frozenset({owner_user_id})
        if owner_user_id is not None
        else eligible_owner_ids or frozenset()
    )
    legacy_snapshot = smtp_legacy_alert_event_snapshot(
        db,
        event=event,
        owner_ids=owner_ids,
    )
    contexts_by_owner = legacy_snapshot.contexts_by_owner
    selected_contexts = (
        {owner_user_id: contexts_by_owner[owner_user_id]}
        if owner_user_id is not None
        else contexts_by_owner
    )
    occurrence_count = sum(context.count for context in selected_contexts.values())
    payload = build_alert_match_snapshot_payload(
        item=legacy_snapshot.item,
        feed=legacy_snapshot.feed,
        contexts_by_owner=selected_contexts,
        occurrence_ids=[],
        occurrence_count=occurrence_count,
        occurrence_ids_truncated=occurrence_count > 0,
        evaluation_request_id=None,
        owner_user_id=owner_user_id,
    )
    if owner_user_id is None:
        payload.pop("alert_matches", None)
        payload.pop("occurrence_ids_by_owner", None)
    payload[SMTP_SOURCE_OWNER_IDS_KEY] = [
        str(source_owner_id) for source_owner_id in sorted(selected_contexts, key=str)
    ]
    return payload
