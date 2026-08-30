from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_compat import (
    WEBHOOK_CONFIG_SCHEMA_VERSION,
    WebhookConfigurationCompatibilityError,
    ensure_webhook_integration,
    repair_legacy_webhook_integrations,
)
from app.services.integration_connectors.base import (
    ConnectorDeliveryResult,
    ConnectorFollowupDelivery,
    ConnectorRoutingResult,
    IntegrationConnectorDefinition,
    IntegrationEventCompatibilityError,
    IntegrationEventContextError,
)
from app.services.integration_delivery import (
    ensure_webhook_delivery,
    mark_integration_delivery_dead_letter,
)
from app.services.daily_brief_notifications import (
    DailyBriefNotificationContextError,
    daily_brief_context_from_payload,
)
from app.services.notification_delivery_processing import (
    process_reserved_notification_deliveries,
)
from app.services.notification_webhooks import (
    NotificationDeliveryReservationBatch,
    has_recent_notification_delivery,
    process_notification_webhook_delivery,
    reserve_alert_match_notification_deliveries,
    reserve_feed_failing_notification_deliveries,
    reserve_new_item_notification_deliveries,
    reserve_notification_webhook_delivery,
    reserve_retryable_notification_webhook_delivery,
    reserve_webhook_failed_notification_deliveries,
    try_acquire_notification_delivery_lock,
)
from app.services.report_event_compatibility import report_ready_event_owner_id

logger = logging.getLogger(__name__)
LEGACY_DELIVERY_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


class WebhookIntegrationConnector:
    SUPPORTED_EVENT_TYPES = (
        "rss_item_new",
        "alert_match",
        "feed_failing",
        "webhook_failed",
        "daily_digest",
        "report_ready",
    )
    definition = IntegrationConnectorDefinition(
        integration_type="webhook",
        direction="destination",
        display_name="Webhook",
        description="Deliver ThreatLens events to configurable HTTP endpoints.",
        config_schema_version=WEBHOOK_CONFIG_SCHEMA_VERSION,
        supports_test=True,
        supported_event_types=SUPPORTED_EVENT_TYPES,
        capabilities=(
            "destination",
            "http",
            "test_delivery",
            "retries",
            "delivery_history",
        ),
    )

    def supports_event_type(self, event_type: str) -> bool:
        return event_type in self.definition.supported_event_types

    def prepare_routing(self, db: Session, *, event: IntegrationEvent) -> None:
        try:
            repair_legacy_webhook_integrations(db)
            webhooks = db.scalars(
                select(NotificationWebhook).where(
                    NotificationWebhook.event_type == event.event_type
                )
            ).all()
            for webhook in webhooks:
                ensure_webhook_integration(db, webhook)
        except WebhookConfigurationCompatibilityError as exc:
            raise IntegrationEventCompatibilityError(str(exc)) from exc

    def route_event(
        self, db: Session, *, event: IntegrationEvent
    ) -> ConnectorRoutingResult:
        try:
            reservation = self._reserve_event_deliveries(db, event=event)
            delivery_ids = self._attach_event_to_deliveries(
                db,
                event=event,
                compatibility_delivery_ids=reservation.delivery_ids,
            )
        except WebhookConfigurationCompatibilityError as exc:
            raise IntegrationEventCompatibilityError(str(exc)) from exc
        return ConnectorRoutingResult(
            delivery_ids=tuple(delivery_ids),
            compatibility_delivery_ids=tuple(reservation.delivery_ids),
        )

    def process_delivery(
        self,
        db: Session,
        *,
        delivery: IntegrationDelivery,
    ) -> ConnectorDeliveryResult:
        legacy_delivery = db.scalar(
            select(NotificationWebhookDelivery).where(
                NotificationWebhookDelivery.integration_delivery_id == delivery.id
            )
        )
        if legacy_delivery is None:
            mark_integration_delivery_dead_letter(
                db,
                delivery_id=delivery.id,
                error_code="legacy_projection_missing",
                error_message="Webhook compatibility delivery no longer exists.",
            )
            db.commit()
            return ConnectorDeliveryResult(
                delivery_id=delivery.id,
                status="dead_letter",
                reason="Webhook compatibility delivery no longer exists.",
            )

        processing = process_reserved_notification_deliveries(
            db,
            [legacy_delivery.id],
            process_delivery=lambda session, *, delivery_id: (
                process_notification_webhook_delivery(
                    session,
                    delivery_id=delivery_id,
                    commit_outcome=False,
                )
            ),
            reserve_retryable_delivery=reserve_retryable_notification_webhook_delivery,
            reserve_failed_delivery_notifications=None,
            logger=logger,
            emit_failed_delivery_event=self._emit_failed_delivery_event,
            mark_dead_letter=self._mark_dead_letter,
        )
        followup_deliveries: list[ConnectorFollowupDelivery] = []
        for followup in processing.followup_deliveries:
            compatibility_delivery = db.get(
                NotificationWebhookDelivery, followup.delivery_id
            )
            if (
                compatibility_delivery is None
                or compatibility_delivery.integration_delivery_id is None
            ):
                continue
            followup_deliveries.append(
                ConnectorFollowupDelivery(
                    delivery_id=compatibility_delivery.integration_delivery_id,
                    countdown_seconds=followup.countdown_seconds,
                )
            )
        current = db.get(IntegrationDelivery, delivery.id)
        if current is None:
            return ConnectorDeliveryResult(
                delivery.id, "missing", "Integration delivery no longer exists."
            )
        return ConnectorDeliveryResult(
            delivery_id=current.id,
            status=current.state,
            reason=current.last_error_message,
            retry_at=current.not_before.isoformat()
            if current.not_before is not None
            else None,
            followup_deliveries=tuple(followup_deliveries),
            followup_event_ids=processing.followup_event_ids,
        )

    def _reserve_event_deliveries(
        self,
        db: Session,
        *,
        event: IntegrationEvent,
    ) -> NotificationDeliveryReservationBatch:
        resources = None
        if event.event_type in {"rss_item_new", "alert_match", "feed_failing"}:
            from app.services.integration_events import (
                hydrate_integration_event_resources,
            )

            resources = hydrate_integration_event_resources(db, event=event)
        feed_id = (
            getattr(resources.feed, "id", None)
            if resources is not None
            else _payload_uuid(event, "feed_id", required=False)
        )
        owner_user_id = (
            report_ready_event_owner_id(db, event=event)
            if event.event_type == "report_ready"
            else _payload_uuid(event, "owner_user_id", required=False)
        )
        webhooks = self._matching_webhooks(
            db,
            event_type=event.event_type,
            feed_id=feed_id,
            owner_user_id=owner_user_id,
        )

        if event.event_type in {"rss_item_new", "alert_match"}:
            item = resources.item if resources is not None else None
            if item is None:
                raise IntegrationEventContextError(
                    f"{event.event_type} event is missing its item context"
                )
            feed = resources.feed if resources is not None else None
            if feed is None:
                raise IntegrationEventContextError(
                    f"{event.event_type} event is missing its feed context"
                )
            if event.event_type == "rss_item_new":
                return reserve_new_item_notification_deliveries(
                    db, item=item, feed=feed, webhooks=webhooks
                )
            if resources is not None and resources.from_snapshot:
                return self._reserve_snapshot_alert_deliveries(
                    db,
                    event=event,
                    item=item,
                    feed=feed,
                    resources=resources,
                    webhooks=webhooks,
                )
            return reserve_alert_match_notification_deliveries(
                db, item=item, feed=feed, webhooks=webhooks
            )

        if event.event_type == "feed_failing":
            feed = resources.feed if resources is not None else None
            if feed is None:
                raise IntegrationEventContextError(
                    "feed_failing event is missing its feed context"
                )
            return reserve_feed_failing_notification_deliveries(
                db, feed=feed, webhooks=webhooks
            )

        if event.event_type == "webhook_failed":
            source_delivery_id = _payload_uuid(event, "source_delivery_id")
            source_delivery = db.get(NotificationWebhookDelivery, source_delivery_id)
            if source_delivery is None:
                raise IntegrationEventContextError(
                    f"Webhook delivery {source_delivery_id} no longer exists"
                )
            return reserve_webhook_failed_notification_deliveries(
                db, failed_delivery=source_delivery
            )

        if event.event_type == "daily_digest":
            return self._reserve_daily_digest(db, event=event, webhooks=webhooks)

        if event.event_type == "report_ready":
            return self._reserve_digest_like(
                db,
                event=event,
                webhooks=webhooks,
                event_type="report_ready",
                item_label="Intelligence report",
            )

        raise IntegrationEventContextError(
            f"Unsupported integration event type: {event.event_type}"
        )

    def _reserve_snapshot_alert_deliveries(
        self,
        db: Session,
        *,
        event: IntegrationEvent,
        item,
        feed,
        resources,
        webhooks: list[NotificationWebhook],
    ) -> NotificationDeliveryReservationBatch:
        scope_key = f"alert_event:{event.id}"
        existing_by_webhook: dict[uuid.UUID, NotificationWebhookDelivery] = {}
        existing_projections = db.scalars(
            select(NotificationWebhookDelivery)
            .join(
                IntegrationDelivery,
                IntegrationDelivery.id
                == NotificationWebhookDelivery.integration_delivery_id,
            )
            .where(
                IntegrationDelivery.event_id == event.id,
                IntegrationDelivery.delivery_kind == "live",
                NotificationWebhookDelivery.event_type_snapshot == "alert_match",
                NotificationWebhookDelivery.delivery_kind == "live",
            )
            .order_by(NotificationWebhookDelivery.attempted_at.asc())
        ).all()
        for existing in existing_projections:
            existing_by_webhook.setdefault(existing.webhook_id, existing)

        delivery_ids: list[uuid.UUID] = []
        skipped = 0
        for webhook in webhooks:
            user = db.get(User, webhook.user_id)
            if user is None or not user.is_active or not user.is_approved:
                skipped += 1
                continue
            alert_context = resources.alert_context_for_owner(webhook.user_id)
            if alert_context is None:
                skipped += 1
                continue
            if not try_acquire_notification_delivery_lock(
                db,
                webhook_id=webhook.id,
                event_type="alert_match",
                item_id=item.id,
                scope_key=scope_key,
            ):
                raise RuntimeError(
                    "Webhook alert routing is waiting for its event delivery lock."
                )
            if not try_acquire_notification_delivery_lock(
                db,
                webhook_id=webhook.id,
                event_type="alert_match",
                item_id=item.id,
            ):
                raise RuntimeError(
                    "Webhook alert routing is waiting for a rolling-upgrade "
                    "compatibility lock."
                )
            existing = existing_by_webhook.get(webhook.id)
            if existing is not None:
                if existing.scope_key is None:
                    # Rows routed before event-scoped deduplication remain the
                    # compatibility history for this exact integration event.
                    existing.scope_key = scope_key
                    db.add(existing)
                skipped += 1
                continue
            legacy = self._adoptable_legacy_alert_delivery(
                db,
                webhook_id=webhook.id,
                user_id=webhook.user_id,
                item_id=item.id,
                feed_id=feed.id,
                created_not_before=(
                    event.created_at - LEGACY_DELIVERY_CLOCK_SKEW_TOLERANCE
                ),
            )
            if legacy is not None:
                legacy.scope_key = scope_key
                db.add(legacy)
                delivery_ids.append(legacy.id)
                continue
            if has_recent_notification_delivery(
                db,
                webhook_id=webhook.id,
                event_type="alert_match",
                item_id=item.id,
                scope_key=scope_key,
            ):
                skipped += 1
                continue
            delivery = reserve_notification_webhook_delivery(
                db,
                webhook=webhook,
                user=user,
                event_type="alert_match",
                item=item,
                feed=feed,
                alert_context=alert_context,
                scope_key=scope_key,
            )
            delivery_ids.append(delivery.id)
        return NotificationDeliveryReservationBatch(
            delivery_ids=delivery_ids,
            matched_webhooks=len(webhooks),
            skipped=skipped,
        )

    @staticmethod
    def _adoptable_legacy_alert_delivery(
        db: Session,
        *,
        webhook_id: uuid.UUID,
        user_id: uuid.UUID,
        item_id: uuid.UUID,
        feed_id: uuid.UUID,
        created_not_before: datetime,
    ) -> NotificationWebhookDelivery | None:
        return db.scalar(
            select(NotificationWebhookDelivery)
            .outerjoin(
                IntegrationDelivery,
                IntegrationDelivery.id
                == NotificationWebhookDelivery.integration_delivery_id,
            )
            .where(
                NotificationWebhookDelivery.webhook_id == webhook_id,
                NotificationWebhookDelivery.user_id == user_id,
                NotificationWebhookDelivery.event_type_snapshot == "alert_match",
                NotificationWebhookDelivery.delivery_kind == "live",
                NotificationWebhookDelivery.item_id == item_id,
                NotificationWebhookDelivery.feed_id == feed_id,
                NotificationWebhookDelivery.attempted_at >= created_not_before,
                NotificationWebhookDelivery.scope_key.is_(None),
                or_(
                    NotificationWebhookDelivery.integration_delivery_id.is_(None),
                    IntegrationDelivery.event_id.is_(None),
                ),
            )
            .order_by(NotificationWebhookDelivery.attempted_at.asc())
            .limit(1)
            .with_for_update(of=NotificationWebhookDelivery)
        )

    def _matching_webhooks(
        self,
        db: Session,
        *,
        event_type: str,
        feed_id: uuid.UUID | None,
        owner_user_id: uuid.UUID | None,
    ) -> list[NotificationWebhook]:
        query = (
            select(NotificationWebhook)
            .join(
                IntegrationSubscription,
                IntegrationSubscription.id == NotificationWebhook.subscription_id,
            )
            .join(
                IntegrationInstance,
                IntegrationInstance.id == NotificationWebhook.integration_id,
            )
            .join(User, User.id == NotificationWebhook.user_id)
            .where(
                IntegrationInstance.integration_type
                == self.definition.integration_type,
                NotificationWebhook.enabled.is_(True),
                NotificationWebhook.event_type == event_type,
                User.is_active.is_(True),
                User.is_approved.is_(True),
            )
        )
        if owner_user_id is not None:
            query = query.where(NotificationWebhook.user_id == owner_user_id)

        candidates = (
            db.scalars(query.order_by(NotificationWebhook.created_at.asc()))
            .unique()
            .all()
        )
        webhooks = [
            webhook
            for webhook in candidates
            if _legacy_webhook_matches_feed(
                webhook, event_type=event_type, feed_id=feed_id
            )
        ]
        for webhook in webhooks:
            # Older nodes only know the legacy row. Repair its generic projection before routing.
            ensure_webhook_integration(db, webhook)
        return webhooks

    def _reserve_daily_digest(
        self,
        db: Session,
        *,
        event: IntegrationEvent,
        webhooks: list[NotificationWebhook],
    ) -> NotificationDeliveryReservationBatch:
        return self._reserve_digest_like(
            db,
            event=event,
            webhooks=webhooks,
            event_type="daily_digest",
            item_label="AI Daily Brief",
        )

    def _reserve_digest_like(
        self,
        db: Session,
        *,
        event: IntegrationEvent,
        webhooks: list[NotificationWebhook],
        event_type: str,
        item_label: str,
    ) -> NotificationDeliveryReservationBatch:
        try:
            digest_context = daily_brief_context_from_payload(event.payload_json)
        except DailyBriefNotificationContextError as exc:
            raise IntegrationEventContextError(str(exc)) from exc
        scope_key = str(
            event.payload_json.get("scope_key")
            or f"ai_daily_brief:{digest_context.brief_date or event.created_at.date().isoformat()}"
        )
        delivery_ids: list[uuid.UUID] = []
        skipped = 0
        for webhook in webhooks:
            user = db.get(User, webhook.user_id)
            if user is None or not user.is_active or not user.is_approved:
                skipped += 1
                continue
            if not try_acquire_notification_delivery_lock(
                db,
                webhook_id=webhook.id,
                event_type=event_type,
                scope_key=scope_key,
            ):
                skipped += 1
                continue
            if has_recent_notification_delivery(
                db,
                webhook_id=webhook.id,
                event_type=event_type,
                scope_key=scope_key,
            ):
                skipped += 1
                continue
            delivery = reserve_notification_webhook_delivery(
                db,
                webhook=webhook,
                user=user,
                event_type=event_type,
                digest_context=digest_context,
                item_title=digest_context.title,
                feed_name=item_label,
                scope_key=scope_key,
            )
            delivery_ids.append(delivery.id)
        return NotificationDeliveryReservationBatch(
            delivery_ids=delivery_ids,
            matched_webhooks=len(webhooks),
            skipped=skipped,
        )

    def _attach_event_to_deliveries(
        self,
        db: Session,
        *,
        event: IntegrationEvent,
        compatibility_delivery_ids: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        from app.services.integration_events import delivery_payload_for_owner

        generic_ids: list[uuid.UUID] = []
        for legacy_delivery in db.scalars(
            select(NotificationWebhookDelivery).where(
                NotificationWebhookDelivery.id.in_(compatibility_delivery_ids)
            )
        ).all():
            webhook = db.get(NotificationWebhook, legacy_delivery.webhook_id)
            if webhook is None:
                continue
            generic = ensure_webhook_delivery(
                db,
                webhook=webhook,
                legacy_delivery=legacy_delivery,
                event_id=event.id,
            )
            generic.idempotency_key = (
                f"event:{event.id}:subscription:{generic.subscription_id}:live"
            )
            generic.payload_json = delivery_payload_for_owner(
                event,
                owner_user_id=legacy_delivery.user_id,
            )
            generic.payload_json["legacy_webhook_delivery_id"] = str(legacy_delivery.id)
            db.add(generic)
            generic_ids.append(generic.id)
        db.flush()
        return generic_ids

    @staticmethod
    def _emit_failed_delivery_event(
        db: Session, failed_delivery: NotificationWebhookDelivery
    ) -> uuid.UUID:
        from app.services.integration_events import emit_integration_event

        event = emit_integration_event(
            db,
            event_type="webhook_failed",
            source_type="notification_webhook_delivery",
            source_id=failed_delivery.id,
            idempotency_key=f"webhook_delivery:{failed_delivery.id}:webhook_failed:v1",
            payload={
                "source_delivery_id": str(failed_delivery.id),
                "feed_id": str(failed_delivery.feed_id)
                if failed_delivery.feed_id
                else None,
                "owner_user_id": str(failed_delivery.user_id),
            },
        )
        return event.id

    @staticmethod
    def _mark_dead_letter(
        db: Session, failed_delivery: NotificationWebhookDelivery
    ) -> None:
        if failed_delivery.integration_delivery_id is None:
            return
        mark_integration_delivery_dead_letter(
            db,
            delivery_id=failed_delivery.integration_delivery_id,
            error_code="attempts_exhausted",
            error_message=failed_delivery.error
            or "Webhook delivery attempts were exhausted.",
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


def _legacy_webhook_matches_feed(
    webhook: NotificationWebhook,
    *,
    event_type: str,
    feed_id: uuid.UUID | None,
) -> bool:
    if event_type in {"daily_digest", "report_ready"}:
        return True
    if webhook.feed_scope == "all":
        return True
    if feed_id is None:
        return False
    return str(feed_id) in {str(value) for value in (webhook.feed_ids_json or [])}
