"""A rendering failure is not an accepted request that can be replayed."""

from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.notification_webhook_storage import RENDER_FAILURE_ERROR_PREFIX

REQUEST_RENDERED_KEY = "webhook_request_rendered"
RENDER_FAILURE_RETRY_MESSAGE = (
    "This delivery has no successfully rendered request and cannot be retried or replayed. "
    "Reduce or correct the webhook template, preview a stored event, then wait for a new matching event."
)


class WebhookRequestNotRendered(ValueError):
    pass


def request_failed_rendering(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    generic: IntegrationDelivery | None = None,
) -> bool:
    if (delivery.error or "").startswith(RENDER_FAILURE_ERROR_PREFIX):
        return True
    if generic is None and delivery.integration_delivery_id is not None:
        generic = db.get(IntegrationDelivery, delivery.integration_delivery_id)
    if generic is None:
        return False
    payload = generic.payload_json if isinstance(generic.payload_json, dict) else {}
    return (
        payload.get(REQUEST_RENDERED_KEY) is False
        or generic.last_error_code == "render_error"
    )


def require_rendered_request(
    db: Session,
    *,
    delivery: NotificationWebhookDelivery,
    generic: IntegrationDelivery | None = None,
) -> None:
    if request_failed_rendering(db, delivery=delivery, generic=generic):
        raise WebhookRequestNotRendered(RENDER_FAILURE_RETRY_MESSAGE)
