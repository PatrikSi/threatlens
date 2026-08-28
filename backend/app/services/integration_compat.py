from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
    IntegrationSubscriptionFeed,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.webhook_delivery_locking import (
    WebhookDeliveryBusyError,
    is_webhook_delivery_lock_contention,
)

WEBHOOK_INTEGRATION_TYPE = "webhook"
WEBHOOK_CONFIG_SCHEMA_VERSION = 1
WEBHOOK_SUBSCRIPTION_KEY = "legacy-webhook"
INTEGRATION_DIRECTION_DESTINATION = "destination"


class WebhookConfigurationCompatibilityError(RuntimeError):
    code = "unsupported_connector_config_schema"


def ensure_webhook_config_schema_compatible(instance: IntegrationInstance) -> None:
    schema_version = int(instance.schema_version or 1)
    if schema_version > WEBHOOK_CONFIG_SCHEMA_VERSION:
        raise WebhookConfigurationCompatibilityError(
            f"Webhook integration configuration uses schema version {schema_version}; "
            f"this worker supports through version {WEBHOOK_CONFIG_SCHEMA_VERSION}. "
            "Delivery will retry after the worker is upgraded."
        )


def lock_notification_webhook(
    db: Session,
    webhook_id: uuid.UUID,
    *,
    refresh_existing: bool = False,
) -> NotificationWebhook | None:
    """Lock the webhook parent before any dependent compatibility rows."""

    query = (
        select(NotificationWebhook)
        .where(NotificationWebhook.id == webhook_id)
        .with_for_update()
        .execution_options(autoflush=False)
    )
    if refresh_existing:
        query = query.execution_options(populate_existing=True)
    return db.scalar(query)


def ensure_webhook_integration(
    db: Session,
    webhook: NotificationWebhook,
) -> tuple[IntegrationInstance, IntegrationSubscription]:
    """Create or repair the generic control-plane records for a legacy webhook."""
    # Clean repair candidates may be stale after waiting; dirty API updates must survive.
    locked_webhook = lock_notification_webhook(
        db,
        webhook.id,
        refresh_existing=not db.is_modified(webhook, include_collections=True),
    )
    if locked_webhook is None:
        raise ValueError("Webhook configuration no longer exists")
    webhook = locked_webhook

    instance = _load_webhook_instance(db, webhook)
    if instance is None:
        candidate_id = webhook.id if db.get(IntegrationInstance, webhook.id) is None else uuid.uuid4()
        instance = IntegrationInstance(
            id=candidate_id,
            owner_user_id=webhook.user_id,
            system_key=None,
            name=webhook.name,
            integration_type=WEBHOOK_INTEGRATION_TYPE,
            direction=INTEGRATION_DIRECTION_DESTINATION,
            enabled=webhook.enabled,
            schema_version=WEBHOOK_CONFIG_SCHEMA_VERSION,
            config_json={"legacy_webhook_id": str(webhook.id)},
            secret_json=None,
            health_status="unknown",
        )
        db.add(instance)
        db.flush()
    else:
        ensure_webhook_config_schema_compatible(instance)

    _sync_webhook_instance(instance, webhook)

    subscription = _load_webhook_subscription(db, webhook, instance)
    if subscription is None:
        candidate_id = webhook.id if db.get(IntegrationSubscription, webhook.id) is None else uuid.uuid4()
        subscription = IntegrationSubscription(
            id=candidate_id,
            integration_id=instance.id,
            subscription_key=WEBHOOK_SUBSCRIPTION_KEY,
            event_type=webhook.event_type,
            enabled=webhook.enabled,
            filter_json={},
            transform_json={"legacy_webhook_id": str(webhook.id)},
        )
        db.add(subscription)

    _sync_webhook_subscription(subscription, webhook, instance)
    db.flush()
    _sync_subscription_feeds(db, subscription=subscription, feed_ids=webhook.feed_ids_json or [])

    webhook.integration_id = instance.id
    webhook.subscription_id = subscription.id
    db.add(webhook)
    db.flush()
    return instance, subscription


def delete_webhook_integration(db: Session, webhook: NotificationWebhook) -> None:
    locked_webhook = lock_notification_webhook(
        db,
        webhook.id,
        refresh_existing=True,
    )
    if locked_webhook is None:
        return
    webhook = locked_webhook
    integration_id = webhook.integration_id
    try:
        list(
            db.scalars(
                select(NotificationWebhookDelivery.id)
                .where(NotificationWebhookDelivery.webhook_id == webhook.id)
                .with_for_update(nowait=True)
            )
        )
        if integration_id is not None:
            list(
                db.scalars(
                    select(IntegrationDelivery.id)
                    .where(IntegrationDelivery.integration_id == integration_id)
                    .with_for_update(nowait=True)
                )
            )
    except OperationalError as exc:
        if not is_webhook_delivery_lock_contention(exc):
            raise
        db.rollback()
        raise WebhookDeliveryBusyError(
            "Webhook delivery processing is busy; retry deletion shortly."
        ) from exc
    db.delete(webhook)
    db.flush()
    if integration_id is None:
        return
    instance = db.get(IntegrationInstance, integration_id)
    if instance is not None and instance.integration_type == WEBHOOK_INTEGRATION_TYPE:
        db.delete(instance)


def repair_legacy_webhook_integrations(db: Session, *, limit: int = 500) -> int:
    webhooks = db.scalars(
        select(NotificationWebhook)
        .where(
            (NotificationWebhook.integration_id.is_(None))
            | (NotificationWebhook.subscription_id.is_(None))
        )
        .order_by(NotificationWebhook.created_at.asc())
        .limit(max(1, int(limit)))
    ).all()
    for webhook in webhooks:
        ensure_webhook_integration(db, webhook)
    return len(webhooks)


def _load_webhook_instance(db: Session, webhook: NotificationWebhook) -> IntegrationInstance | None:
    if webhook.integration_id is None:
        return None
    instance = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.id == webhook.integration_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None or instance.integration_type != WEBHOOK_INTEGRATION_TYPE:
        webhook.integration_id = None
        return None
    return instance


def _load_webhook_subscription(
    db: Session,
    webhook: NotificationWebhook,
    instance: IntegrationInstance,
) -> IntegrationSubscription | None:
    subscription = db.get(IntegrationSubscription, webhook.subscription_id) if webhook.subscription_id else None
    if subscription is not None and subscription.integration_id == instance.id:
        return subscription
    webhook.subscription_id = None
    return db.scalar(
        select(IntegrationSubscription).where(
            IntegrationSubscription.integration_id == instance.id,
            IntegrationSubscription.subscription_key == WEBHOOK_SUBSCRIPTION_KEY,
        )
    )


def _sync_webhook_instance(instance: IntegrationInstance, webhook: NotificationWebhook) -> None:
    config = dict(instance.config_json) if isinstance(instance.config_json, dict) else {}
    config["legacy_webhook_id"] = str(webhook.id)
    instance.owner_user_id = webhook.user_id
    instance.name = webhook.name
    instance.integration_type = WEBHOOK_INTEGRATION_TYPE
    instance.direction = INTEGRATION_DIRECTION_DESTINATION
    instance.enabled = webhook.enabled
    instance.schema_version = WEBHOOK_CONFIG_SCHEMA_VERSION
    instance.config_json = config


def _sync_webhook_subscription(
    subscription: IntegrationSubscription,
    webhook: NotificationWebhook,
    instance: IntegrationInstance,
) -> None:
    subscription.integration_id = instance.id
    subscription.subscription_key = WEBHOOK_SUBSCRIPTION_KEY
    subscription.event_type = webhook.event_type
    subscription.enabled = webhook.enabled
    subscription.feed_scope = webhook.feed_scope
    subscription.filter_json = {
        "feed_scope": webhook.feed_scope,
        "feed_ids": list(webhook.feed_ids_json or []),
    }
    subscription.transform_json = {"legacy_webhook_id": str(webhook.id)}


def _sync_subscription_feeds(
    db: Session,
    *,
    subscription: IntegrationSubscription,
    feed_ids: list[str],
) -> None:
    desired_ids: set[uuid.UUID] = set()
    for value in feed_ids:
        try:
            desired_ids.add(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    if desired_ids:
        desired_ids = set(db.scalars(select(Feed.id).where(Feed.id.in_(desired_ids))).all())
    existing = {
        row.feed_id: row
        for row in db.scalars(
            select(IntegrationSubscriptionFeed).where(
                IntegrationSubscriptionFeed.subscription_id == subscription.id
            )
        ).all()
    }
    for feed_id, row in existing.items():
        if feed_id not in desired_ids:
            db.delete(row)
    for feed_id in desired_ids - set(existing):
        db.add(IntegrationSubscriptionFeed(subscription_id=subscription.id, feed_id=feed_id))
    db.flush()
