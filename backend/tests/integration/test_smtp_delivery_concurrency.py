from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.services.integration_delivery import CLAIMED, claim_integration_delivery
from app.services.integration_storage import (
    acquire_smtp_configuration_write_lock,
    build_active_smtp_settings,
)
from app.services.smtp_delivery_eligibility import (
    SMTPDeliveryIneligibleError,
    lock_smtp_delivery_external_io_eligibility,
)


def test_smtp_configuration_write_waits_for_external_io_fence(database_engine):
    instance_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        instance = IntegrationInstance(
            id=instance_id,
            name="Concurrent SMTP",
            integration_type="smtp",
            direction="destination",
            enabled=True,
            config_json={
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": None,
                "from_email": "threatlens@example.com",
                "from_name": "ThreatLens",
                "to_emails": ["soc@example.com"],
                "timeout_seconds": 10,
                "event_types": ["rss_item_new"],
                "feed_scope": "all",
                "feed_ids": [],
                "subject_template": "ThreatLens event",
                "html_template": "<p>ThreatLens event</p>",
            },
        )
        setup_db.add(instance)
        setup_db.flush()
        subscription = IntegrationSubscription(
            integration_id=instance.id,
            subscription_key="event:rss_item_new",
            event_type="rss_item_new",
            enabled=True,
        )
        setup_db.add(subscription)
        setup_db.flush()
        setup_db.add(
            IntegrationDelivery(
                id=delivery_id,
                integration_id=instance.id,
                subscription_id=subscription.id,
                connector_type="smtp",
                event_type="rss_item_new",
                state="pending",
                idempotency_key=f"smtp-concurrency:{delivery_id}",
                payload_json={},
                max_attempts=3,
            )
        )
        setup_db.commit()

    writer_started = Event()
    worker_db = Session(database_engine)

    def _disable_integration() -> None:
        with Session(database_engine) as writer_db:
            writer_started.set()
            acquire_smtp_configuration_write_lock(writer_db)
            instance = writer_db.scalar(
                select(IntegrationInstance)
                .where(IntegrationInstance.id == instance_id)
                .with_for_update()
            )
            assert instance is not None
            instance.enabled = False
            writer_db.add(instance)
            writer_db.commit()

    try:
        claim = claim_integration_delivery(worker_db, delivery_id=delivery_id)
        assert claim.status == CLAIMED
        assert claim.attempt_number == 1
        instance = worker_db.get(IntegrationInstance, instance_id)
        assert instance is not None
        expected_settings = build_active_smtp_settings(instance)
        lock_smtp_delivery_external_io_eligibility(
            worker_db,
            delivery_id=delivery_id,
            expected_attempt_number=claim.attempt_number,
            expected_settings=expected_settings,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            writer = executor.submit(_disable_integration)
            assert writer_started.wait(timeout=2)
            time.sleep(0.1)
            assert not writer.done()
            worker_db.commit()
            writer.result(timeout=5)

        with Session(database_engine) as verify_db:
            instance = verify_db.get(IntegrationInstance, instance_id)
            assert instance is not None and instance.enabled is False
            try:
                lock_smtp_delivery_external_io_eligibility(
                    verify_db,
                    delivery_id=delivery_id,
                    expected_attempt_number=1,
                    expected_settings=expected_settings,
                )
            except SMTPDeliveryIneligibleError as exc:
                assert exc.code == "smtp_integration_disabled"
            else:
                raise AssertionError("Disabled SMTP integration remained eligible")
            verify_db.rollback()
    finally:
        worker_db.rollback()
        worker_db.close()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(IntegrationInstance).where(IntegrationInstance.id == instance_id)
            )
            cleanup_db.commit()
