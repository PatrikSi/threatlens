from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import NotificationWebhookWrite
from app.services.notification_webhook_storage import (
    decrypt_notification_text,
    upgrade_notification_webhook_delivery_secret_storage,
)
from app.services.notification_webhook_templates import (
    find_unknown_template_variables,
)
from app.services.url_utils import is_fetchable_url


settings = get_settings()


def validate_notification_webhook_payload(
    payload: NotificationWebhookWrite,
    available_feed_ids: set[uuid.UUID],
) -> None:
    if payload.feed_scope == "selected":
        if any(feed_id not in available_feed_ids for feed_id in payload.feed_ids):
            # Do not disclose which submitted identifiers exist but are outside
            # the actor's current handling-label grants.
            raise ValueError("One or more selected feeds are unavailable")

    validate_notification_target_url(payload.url_template)

    unknown_variables = sorted(find_unknown_template_variables(payload))
    if unknown_variables:
        raise ValueError(
            f"Unknown template variable(s): {', '.join(unknown_variables)}"
        )


def validate_notification_target_url(url_template: str) -> None:
    try:
        split = urlsplit(url_template)
    except ValueError as exc:
        raise ValueError("url_template must be a valid URL") from exc

    if "{{" in split.scheme or "{{" in split.netloc:
        raise ValueError(
            "url_template must not contain templates in the scheme or host"
        )
    if split.scheme.lower() not in {"http", "https"}:
        raise ValueError("url_template must use http or https")
    if split.scheme.lower() != "https" and not settings.allow_private_network_webhooks:
        raise ValueError(
            "url_template must use https unless ALLOW_PRIVATE_NETWORK_WEBHOOKS is enabled"
        )
    if (
        split.scheme.lower() == "http"
        and settings.allow_private_network_webhooks
        and is_fetchable_url(url_template, allow_private_network=False)
    ):
        raise ValueError(
            "url_template must use https for publicly routable hosts; plain http is only allowed for private-network webhook endpoints"
        )
    if split.username or split.password:
        raise ValueError("url_template must not include embedded credentials")
    if split.fragment:
        raise ValueError("url_template must not include fragments")
    if not is_fetchable_url(
        url_template,
        allow_private_network=settings.allow_private_network_webhooks,
    ):
        raise ValueError("url_template is not allowed for outbound fetch")


def validate_notification_webhook_payload_for_actor(
    payload: NotificationWebhookWrite,
    available_feed_ids: set[uuid.UUID],
    *,
    actor_user: User | SimpleNamespace | None,
) -> None:
    validate_notification_webhook_payload(payload, available_feed_ids)
    validate_notification_actor_for_delivery(actor_user)


def validate_notification_delivery_target_for_actor(
    delivery: NotificationWebhookDelivery,
    *,
    actor_user: User | SimpleNamespace | None,
    require_active: bool = True,
) -> None:
    upgrade_notification_webhook_delivery_secret_storage(delivery)
    rendered_url = decrypt_notification_text(delivery.rendered_url) or ""
    validate_notification_actor_for_delivery(
        actor_user,
        require_active=require_active,
    )
    validate_notification_target_url(rendered_url)


def validate_notification_actor_for_delivery(
    actor_user: User | SimpleNamespace | None,
    *,
    require_active: bool = True,
) -> None:
    if actor_user is None:
        raise ValueError(
            "Webhook owner is no longer active and approved for outbound delivery"
        )
    if require_active and (
        not getattr(actor_user, "is_active", True)
        or not getattr(actor_user, "is_approved", True)
    ):
        raise ValueError(
            "Webhook owner is no longer active and approved for outbound delivery"
        )
    if getattr(actor_user, "role", None) not in {ROLE_ADMIN, ROLE_ANALYST}:
        raise ValueError(
            "Webhook owner is no longer authorized to manage outbound deliveries"
        )
