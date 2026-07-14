from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationInstance, IntegrationSubscription
from app.models.notification_webhook import NotificationWebhook

WEBHOOK_INTEGRATION_TYPE = "webhook"
WEBHOOK_CONFIG_SCHEMA_VERSION = 1
WEBHOOK_SUBSCRIPTION_KEY = "legacy-webhook"
INTEGRATION_DIRECTION_DESTINATION = "destination"


def ensure_webhook_integration(
    db: Session,
    webhook: NotificationWebhook,
) -> tuple[IntegrationInstance, IntegrationSubscription]:
    """Create or repair the generic control-plane records for a legacy webhook."""
    locked_webhook = db.scalar(
        select(NotificationWebhook).where(NotificationWebhook.id == webhook.id).with_for_update()
    )
    if locked_webhook is not None:
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

    webhook.integration_id = instance.id
    webhook.subscription_id = subscription.id
    db.add(webhook)
    db.flush()
    return instance, subscription


def delete_webhook_integration(db: Session, webhook: NotificationWebhook) -> None:
    integration_id = webhook.integration_id
    db.delete(webhook)
    db.flush()
    if integration_id is None:
        return
    instance = db.get(IntegrationInstance, integration_id)
    if instance is not None and instance.integration_type == WEBHOOK_INTEGRATION_TYPE:
        db.delete(instance)


def _load_webhook_instance(db: Session, webhook: NotificationWebhook) -> IntegrationInstance | None:
    if webhook.integration_id is None:
        return None
    instance = db.get(IntegrationInstance, webhook.integration_id)
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
    subscription.filter_json = {
        "feed_scope": webhook.feed_scope,
        "feed_ids": list(webhook.feed_ids_json or []),
    }
    subscription.transform_json = {"legacy_webhook_id": str(webhook.id)}
