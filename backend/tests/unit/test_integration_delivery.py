import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.integration import IntegrationAttempt, IntegrationDelivery
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_delivery import (
    claim_webhook_delivery,
    ensure_webhook_delivery,
    finalize_webhook_delivery,
)


def test_webhook_delivery_state_and_attempts_are_owned_by_generic_engine(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    generic = ensure_webhook_delivery(db_session, webhook=webhook, legacy_delivery=legacy)
    assert generic.state == "pending"
    assert legacy.integration_delivery_id == generic.id

    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    claimed = claim_webhook_delivery(
        db_session,
        webhook=webhook,
        legacy_delivery=legacy,
        now=started_at,
    )
    assert claimed is not None
    assert claimed.delivery_state == "sending"
    assert claimed.attempt_count == 1

    generic = db_session.get(IntegrationDelivery, generic.id)
    attempt = db_session.scalar(select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == generic.id))
    assert generic is not None
    assert generic.state == "sending"
    assert generic.attempt_count == 1
    assert attempt is not None
    assert attempt.status == "running"

    recorded = finalize_webhook_delivery(
        db_session,
        legacy_delivery=claimed,
        success=True,
        status_code=204,
        duration_ms=25,
        error=None,
        retryable=False,
        expected_attempt_number=1,
        finished_at=started_at + timedelta(milliseconds=25),
    )
    db_session.commit()

    assert recorded is True
    db_session.refresh(generic)
    db_session.refresh(attempt)
    assert generic.state == "succeeded"
    assert generic.last_status_code == 204
    assert attempt.status == "succeeded"
    assert attempt.duration_ms == 25


def test_stale_webhook_attempt_cannot_overwrite_newer_recovery_attempt(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    first = claim_webhook_delivery(db_session, webhook=webhook, legacy_delivery=legacy, now=started_at)
    assert first is not None

    second = claim_webhook_delivery(
        db_session,
        webhook=webhook,
        legacy_delivery=first,
        now=started_at + timedelta(minutes=3),
    )
    assert second is not None
    assert second.attempt_count == 2

    stale_recorded = finalize_webhook_delivery(
        db_session,
        legacy_delivery=second,
        success=False,
        status_code=500,
        duration_ms=180_000,
        error="HTTP 500",
        retryable=True,
        expected_attempt_number=1,
    )
    assert stale_recorded is False

    generic = db_session.get(IntegrationDelivery, second.integration_delivery_id)
    attempts = db_session.scalars(
        select(IntegrationAttempt)
        .where(IntegrationAttempt.delivery_id == generic.id)
        .order_by(IntegrationAttempt.attempt_number)
    ).all()
    assert generic is not None
    assert generic.state == "sending"
    assert generic.attempt_count == 2
    assert [(attempt.attempt_number, attempt.status) for attempt in attempts] == [
        (1, "interrupted"),
        (2, "running"),
    ]


def _persist_legacy_delivery(db_session) -> tuple[NotificationWebhook, NotificationWebhookDelivery]:
    user = User(
        id=uuid.uuid4(),
        email=f"delivery-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    db_session.add(user)
    db_session.flush()
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Delivery webhook",
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
    db_session.add(webhook)
    db_session.flush()
    legacy = NotificationWebhookDelivery(
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
        attempted_at=datetime.now(timezone.utc),
    )
    db_session.add(legacy)
    db_session.flush()
    return webhook, legacy
