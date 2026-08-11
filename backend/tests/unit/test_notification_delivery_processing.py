import logging
import uuid
from types import SimpleNamespace

from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.notification_delivery_processing import (
    process_reserved_notification_deliveries,
)


def test_poison_webhook_delivery_does_not_abort_later_delivery(db_session):
    first, second = _persist_deliveries(db_session)

    def _process(_db, *, delivery_id):
        if delivery_id == first.id:
            raise RuntimeError("poison delivery")
        return SimpleNamespace(
            claimed=True,
            delivery=second,
            result=SimpleNamespace(success=True),
        )

    result = process_reserved_notification_deliveries(
        db_session,
        [first.id, second.id],
        process_delivery=_process,
        reserve_retryable_delivery=lambda *_args, **_kwargs: None,
        reserve_failed_delivery_notifications=None,
        logger=logging.getLogger(__name__),
    )

    db_session.refresh(first)
    assert result.delivered == 1
    assert result.failed == 1
    assert first.delivery_state == "pending"
    assert first.claimed_at is None
    assert first.not_before is not None
    assert first.error == "RuntimeError: poison delivery"


def test_poison_webhook_retry_reservation_does_not_abort_later_delivery(db_session):
    first, second = _persist_deliveries(db_session)

    def _process(_db, *, delivery_id):
        delivery = first if delivery_id == first.id else second
        return SimpleNamespace(
            claimed=True,
            delivery=delivery,
            result=SimpleNamespace(
                success=delivery_id == second.id,
                status_code=503,
                error="HTTP 503",
            ),
        )

    def _reserve_retry(*_args, **_kwargs):
        raise RuntimeError("retry reservation failed")

    result = process_reserved_notification_deliveries(
        db_session,
        [first.id, second.id],
        process_delivery=_process,
        reserve_retryable_delivery=_reserve_retry,
        reserve_failed_delivery_notifications=None,
        logger=logging.getLogger(__name__),
    )

    db_session.refresh(first)
    assert result.delivered == 1
    assert result.failed == 1
    assert first.delivery_state == "pending"
    assert first.error == "RuntimeError: retry reservation failed"


def _persist_deliveries(
    db_session,
) -> tuple[NotificationWebhookDelivery, NotificationWebhookDelivery]:
    user = User(
        id=uuid.uuid4(),
        email=f"delivery-worker-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Delivery isolation",
        enabled=True,
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(webhook)
    db_session.flush()
    deliveries = tuple(
        NotificationWebhookDelivery(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            user_id=user.id,
            event_type_snapshot="rss_item_new",
            delivery_kind="live",
            delivery_state="pending",
            attempt_count=0,
            success=False,
            timeout_seconds=10,
            rendered_url="https://example.com/hook",
            rendered_method="POST",
            rendered_headers_json=[],
            rendered_query_params_json=[],
        )
        for _ in range(2)
    )
    db_session.add_all(deliveries)
    db_session.commit()
    return deliveries
