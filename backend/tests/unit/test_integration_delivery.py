import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.integration import IntegrationAttempt, IntegrationDelivery, IntegrationInstance, IntegrationSubscription
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_delivery import (
    claim_integration_delivery,
    claim_webhook_delivery,
    ensure_webhook_delivery,
    finalize_integration_delivery,
    finalize_webhook_delivery,
    replay_dead_letter_delivery,
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


def test_generic_delivery_retries_with_backoff_and_rejects_stale_results(db_session, monkeypatch):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr("app.services.integration_delivery.settings.integration_delivery_retry_backoff_seconds", 30)
    monkeypatch.setattr("app.services.integration_delivery.settings.integration_delivery_retry_max_backoff_seconds", 300)
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    claim = claim_integration_delivery(db_session, delivery_id=delivery.id, now=started_at)
    outcome = finalize_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        expected_attempt_number=claim.attempt_number or 0,
        success=False,
        duration_ms=50,
        error_code="timeout",
        error_message="SMTP timed out.",
        retryable=True,
        finished_at=started_at + timedelta(seconds=1),
    )
    db_session.commit()

    assert claim.status == "claimed"
    assert outcome.recorded is True
    assert outcome.state == "retry_wait"
    assert outcome.retry_at is not None
    assert outcome.retry_at >= started_at + timedelta(seconds=31)
    stale = finalize_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        expected_attempt_number=claim.attempt_number or 0,
        success=True,
        duration_ms=1,
        error_code=None,
        error_message=None,
        retryable=False,
    )
    assert stale.recorded is False


def test_generic_claim_enforces_per_instance_concurrency(db_session):
    first = _persist_generic_delivery(db_session, max_concurrency=1)
    second = _persist_generic_delivery(db_session, instance_id=first.integration_id)
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    first_claim = claim_integration_delivery(db_session, delivery_id=first.id, now=now)
    second_claim = claim_integration_delivery(db_session, delivery_id=second.id, now=now)

    assert first_claim.status == "claimed"
    assert second_claim.status == "deferred"
    assert second_claim.reason == "concurrency_limited"


def test_generic_claim_enforces_rate_limit(db_session):
    first = _persist_generic_delivery(db_session, max_concurrency=5, rate_limit=1)
    second = _persist_generic_delivery(db_session, instance_id=first.integration_id)
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    first_claim = claim_integration_delivery(db_session, delivery_id=first.id, now=now)
    finalize_integration_delivery(
        db_session,
        delivery_id=first.id,
        expected_attempt_number=first_claim.attempt_number or 0,
        success=True,
        duration_ms=5,
        error_code=None,
        error_message=None,
        retryable=False,
        finished_at=now + timedelta(seconds=1),
    )
    db_session.commit()

    second_claim = claim_integration_delivery(db_session, delivery_id=second.id, now=now + timedelta(seconds=2))

    assert second_claim.status == "deferred"
    assert second_claim.reason == "rate_limited"


def test_retryable_failure_opens_circuit_and_dead_letter_can_be_replayed(db_session, monkeypatch):
    delivery = _persist_generic_delivery(db_session, max_attempts=1)
    monkeypatch.setattr("app.services.integration_delivery.settings.integration_delivery_circuit_failure_threshold", 1)
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    claim = claim_integration_delivery(db_session, delivery_id=delivery.id, now=now)
    outcome = finalize_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        expected_attempt_number=claim.attempt_number or 0,
        success=False,
        duration_ms=10,
        error_code="connection_error",
        error_message="Connection refused.",
        retryable=True,
        finished_at=now + timedelta(seconds=1),
    )
    db_session.commit()

    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert outcome.state == "dead_letter"
    assert instance is not None
    assert instance.circuit_state == "open"
    replay = replay_dead_letter_delivery(db_session, delivery_id=delivery.id)
    assert replay.state == "pending"
    assert replay.delivery_kind == "replay"
    assert replay.source_delivery_id == delivery.id


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


def _persist_generic_delivery(
    db_session,
    *,
    instance_id: uuid.UUID | None = None,
    max_concurrency: int = 2,
    rate_limit: int = 60,
    max_attempts: int = 3,
) -> IntegrationDelivery:
    instance = db_session.get(IntegrationInstance, instance_id) if instance_id else None
    if instance is None:
        instance = IntegrationInstance(
            id=instance_id or uuid.uuid4(),
            name="SMTP",
            integration_type="smtp",
            direction="destination",
            enabled=True,
            config_json={},
            max_concurrency=max_concurrency,
            rate_limit_per_minute=rate_limit,
        )
        db_session.add(instance)
        db_session.flush()
    subscription = db_session.scalar(
        select(IntegrationSubscription).where(
            IntegrationSubscription.integration_id == instance.id,
            IntegrationSubscription.event_type == "rss_item_new",
        )
    )
    if subscription is None:
        subscription = IntegrationSubscription(
            integration_id=instance.id,
            subscription_key="event:rss_item_new",
            event_type="rss_item_new",
        )
        db_session.add(subscription)
        db_session.flush()
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        subscription_id=subscription.id,
        connector_type="smtp",
        event_type="rss_item_new",
        idempotency_key=f"test:{uuid.uuid4()}",
        payload_json={},
        max_attempts=max_attempts,
    )
    db_session.add(delivery)
    db_session.flush()
    return delivery
