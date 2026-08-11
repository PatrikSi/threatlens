import uuid
from contextlib import contextmanager

from sqlalchemy import select

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.services.integration_connectors.base import ConnectorDeliveryResult
from app.services.integration_delivery import (
    claim_integration_delivery,
    finalize_integration_delivery,
)
from app.tasks.integration_tasks import (
    dispatch_pending_integration_deliveries,
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
