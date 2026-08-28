import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models.feed import Feed
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.item import Item
from app.services.integration_connectors.base import ConnectorDeliveryResult
from app.services.integration_delivery import (
    claim_integration_delivery,
    finalize_integration_delivery,
)
from app.services.integration_processors import (
    SMTPDeliveryPreflightError,
    process_smtp_integration_delivery,
)
from app.tasks.integration_tasks import (
    dispatch_pending_integration_deliveries,
    dispatch_pending_integration_events,
    process_integration_deliveries,
)


def test_poison_integration_delivery_does_not_abort_batch(db_session, monkeypatch):
    first, second = _persist_deliveries(db_session)

    class Connector:
        def process_delivery(self, db, *, delivery):
            claim = claim_integration_delivery(db, delivery_id=delivery.id)
            assert claim.attempt_number is not None
            if delivery.id == first.id:
                raise RuntimeError("poison integration delivery")
            outcome = finalize_integration_delivery(
                db,
                delivery_id=delivery.id,
                expected_attempt_number=claim.attempt_number,
                success=True,
                duration_ms=1,
                error_code=None,
                error_message=None,
                retryable=False,
            )
            db.commit()
            return ConnectorDeliveryResult(delivery.id, outcome.state or "succeeded")

    @contextmanager
    def _db_session():
        yield db_session

    monkeypatch.setattr("app.tasks.integration_tasks.db_session", _db_session)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.get_integration_connector",
        lambda _connector_type: Connector(),
    )

    result = process_integration_deliveries.run([str(first.id), str(second.id)])

    db_session.refresh(first)
    db_session.refresh(second)
    first_attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == first.id)
    )
    assert result["failed"] == 1
    assert result["delivered"] == 1
    assert first.state == "retry_wait"
    assert second.state == "succeeded"
    assert first_attempt is not None
    assert first_attempt.response_json["delivery_outcome"] == "unknown"


@pytest.mark.parametrize(
    ("marker", "expected_state", "expected_outcome", "expected_error_code"),
    [
        (False, "retry_wait", "not_attempted", "worker_preflight_error"),
        (True, "dead_letter", "unknown", "worker_error"),
        (None, "dead_letter", "unknown", "worker_error"),
    ],
)
def test_smtp_task_fallback_honors_durable_side_effect_marker(
    db_session,
    monkeypatch,
    marker,
    expected_state,
    expected_outcome,
    expected_error_code,
):
    delivery, _unused = _persist_deliveries(db_session)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.integration_type = "smtp"
    instance.circuit_state = "half_open"
    instance.circuit_failure_count = 2
    delivery.connector_type = "smtp"
    db_session.add_all([instance, delivery])
    db_session.commit()

    class Connector:
        def process_delivery(self, db, *, delivery):
            claim = claim_integration_delivery(db, delivery_id=delivery.id)
            assert claim.attempt_number == 1
            attempt = db.scalar(
                select(IntegrationAttempt).where(
                    IntegrationAttempt.delivery_id == delivery.id,
                    IntegrationAttempt.attempt_number == 1,
                )
            )
            assert attempt is not None
            if marker is None:
                attempt.response_json = {}
            elif marker is True:
                attempt.response_json = {
                    "delivery_outcome": "unknown",
                    "external_side_effect_possible": True,
                }
            db.add(attempt)
            db.commit()
            raise RuntimeError("connector worker failed")

    @contextmanager
    def _db_session():
        yield db_session

    monkeypatch.setattr("app.tasks.integration_tasks.db_session", _db_session)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.get_integration_connector",
        lambda _connector_type: Connector(),
    )

    result = process_integration_deliveries.run([str(delivery.id)])

    db_session.refresh(delivery)
    db_session.refresh(instance)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result["failed"] == 1
    assert delivery.state == expected_state
    assert delivery.last_error_code == expected_error_code
    assert attempt is not None
    assert attempt.retryable is (marker is False)
    assert attempt.response_json["delivery_outcome"] == expected_outcome
    assert attempt.response_json["external_side_effect_possible"] is (marker is not False)
    assert instance.circuit_state == "half_open"
    assert instance.circuit_failure_count == 2


def test_smtp_task_fallback_recovers_escaped_preflight_finalization(
    db_session,
    monkeypatch,
):
    delivery = _persist_smtp_task_delivery(db_session)

    class _FailFinalizationCommitsSession:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.commit_count = 0

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def commit(self):
            self.commit_count += 1
            if self.commit_count in {2, 3}:
                raise OperationalError(
                    "COMMIT smtp preflight finalization",
                    {},
                    OSError("database connection dropped"),
                )
            return self.wrapped.commit()

    wrapped_db = _FailFinalizationCommitsSession(db_session)

    class Connector:
        def process_delivery(self, db, *, delivery):
            result = process_smtp_integration_delivery(db, delivery_id=delivery.id)
            return ConnectorDeliveryResult(
                result.delivery_id,
                result.status,
                reason=result.reason,
                retry_at=result.retry_at,
            )

    @contextmanager
    def _db_session():
        yield wrapped_db

    monkeypatch.setattr("app.tasks.integration_tasks.db_session", _db_session)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.get_integration_connector",
        lambda _connector_type: Connector(),
    )
    monkeypatch.setattr(
        "app.services.integration_processors.attempt_smtp_integration_delivery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SMTPDeliveryPreflightError(
                "SMTP preflight failed before any external operation."
            )
        ),
    )

    result = process_integration_deliveries.run([str(delivery.id)])

    db_session.refresh(delivery)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert wrapped_db.commit_count == 4
    assert result["failed"] == 1
    assert delivery.state == "retry_wait"
    assert delivery.last_error_code == "worker_preflight_error"
    assert delivery.last_error_retryable is True
    assert attempt is not None and attempt.retryable is True
    assert attempt.response_json["delivery_outcome"] == "not_attempted"
    assert attempt.response_json["external_side_effect_possible"] is False
    assert instance is not None and instance.circuit_failure_count == 0


def test_unknown_connector_worker_does_not_steal_active_claim(
    db_session,
    monkeypatch,
):
    delivery, _unused = _persist_deliveries(db_session)
    delivery.connector_type = "future-connector"
    db_session.add(delivery)
    db_session.commit()
    claim = claim_integration_delivery(db_session, delivery_id=delivery.id)
    assert claim.attempt_number == 1
    claimed_at = delivery.claimed_at

    @contextmanager
    def _db_session():
        yield db_session

    monkeypatch.setattr("app.tasks.integration_tasks.db_session", _db_session)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.get_integration_connector",
        lambda _connector_type: None,
    )

    result = process_integration_deliveries.run([str(delivery.id)])

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result["deferred"] == 1
    assert delivery.state == "sending"
    assert delivery.claimed_at == claimed_at
    assert attempt is not None and attempt.status == "running"
    recorded = finalize_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        expected_attempt_number=1,
        success=True,
        duration_ms=1,
        error_code=None,
        error_message=None,
        retryable=False,
    )
    assert recorded.recorded is True


def test_failed_recovery_enqueue_releases_publication_reservation(
    db_session, monkeypatch
):
    first, second = _persist_deliveries(db_session)

    @contextmanager
    def _db_session():
        yield db_session

    monkeypatch.setattr("app.tasks.integration_tasks.db_session", _db_session)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.process_integration_deliveries.delay",
        lambda _ids: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    result = dispatch_pending_integration_deliveries.run()

    db_session.refresh(first)
    db_session.refresh(second)
    assert result == {
        "status": "ok",
        "scanned": 2,
        "queued": 0,
        "enqueue_failed": True,
    }
    assert first.claimed_at is None
    assert second.claimed_at is None


def test_failed_event_enqueue_releases_publication_reservation(
    db_session, monkeypatch
):
    events = tuple(
        IntegrationEvent(
            event_type="rss_item_new",
            source_type="test",
            idempotency_key=f"event-enqueue:{uuid.uuid4()}",
            payload_json={},
        )
        for _ in range(2)
    )
    db_session.add_all(events)
    db_session.commit()

    @contextmanager
    def _db_session():
        yield db_session

    monkeypatch.setattr("app.tasks.integration_tasks.db_session", _db_session)
    monkeypatch.setattr(
        "app.tasks.integration_tasks.route_integration_event.delay",
        lambda _event_id: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    result = dispatch_pending_integration_events.run()

    for event in events:
        db_session.refresh(event)
        assert event.claimed_at is None
    assert result == {
        "status": "ok",
        "scanned": 2,
        "queued": 0,
        "enqueue_failed": 2,
    }


def _persist_deliveries(db_session) -> tuple[IntegrationDelivery, IntegrationDelivery]:
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        name="Poison isolation",
        integration_type="test",
        direction="destination",
        enabled=True,
        config_json={},
        max_concurrency=2,
        rate_limit_per_minute=60,
    )
    subscription = IntegrationSubscription(
        id=uuid.uuid4(),
        integration_id=instance.id,
        subscription_key="event:rss_item_new",
        event_type="rss_item_new",
    )
    db_session.add(instance)
    db_session.flush()
    db_session.add(subscription)
    db_session.flush()
    deliveries = tuple(
        IntegrationDelivery(
            integration_id=instance.id,
            subscription_id=subscription.id,
            connector_type="test",
            event_type="rss_item_new",
            idempotency_key=f"poison-test:{uuid.uuid4()}",
            payload_json={},
            max_attempts=3,
        )
        for _ in range(2)
    )
    db_session.add_all(deliveries)
    db_session.commit()
    return deliveries


def _persist_smtp_task_delivery(db_session) -> IntegrationDelivery:
    feed = Feed(
        id=uuid.uuid4(),
        name="SMTP task fallback feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/smtp-task-fallback",
        canonical_url="https://example.com/smtp-task-fallback",
        title="SMTP task fallback item",
        summary="Durable preflight marker",
        dedupe_key=f"smtp-task-item:{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex,
    )
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        name="SMTP task fallback",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={
            "host": "smtp.example.com",
            "port": 25,
            "security": "none",
            "from_email": "threatlens@example.com",
            "to_emails": ["soc@example.com"],
            "timeout_seconds": 10,
            "event_types": ["rss_item_new"],
            "feed_scope": "all",
            "feed_ids": [],
            "subject_template": "ThreatLens event",
            "html_template": "<p>ThreatLens event</p>",
        },
    )
    subscription = IntegrationSubscription(
        id=uuid.uuid4(),
        integration_id=instance.id,
        subscription_key="event:rss_item_new",
        event_type="rss_item_new",
    )
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        subscription_id=subscription.id,
        connector_type="smtp",
        event_type="rss_item_new",
        idempotency_key=f"smtp-task-fallback:{uuid.uuid4()}",
        payload_json={"item_id": str(item.id), "feed_id": str(feed.id)},
        max_attempts=3,
    )
    db_session.add_all([feed, instance])
    db_session.flush()
    db_session.add_all([item, subscription])
    db_session.flush()
    db_session.add(delivery)
    db_session.commit()
    return delivery
