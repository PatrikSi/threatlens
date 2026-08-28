from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.user import User
from app.schemas.integration import SMTPSettingsUpdate
from app.services.integration_delivery import CLAIMED, claim_integration_delivery
from app.services.integration_storage import (
    SMTP_SYSTEM_KEY,
    acquire_smtp_configuration_write_lock,
    apply_smtp_settings_update,
    build_active_smtp_settings,
)
from app.services.smtp_integration import dispatch_smtp_notification
from app.services.smtp_delivery_eligibility import (
    SMTP_SOURCE_OWNER_IDS_KEY,
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


def test_global_alert_source_owner_change_waits_for_external_io_fence(
    database_engine,
):
    source_owner_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=source_owner_id,
                email=f"smtp-source-owner-{uuid.uuid4().hex}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        instance = IntegrationInstance(
            id=instance_id,
            name="Global alert SMTP",
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
                "event_types": ["alert_match"],
                "feed_scope": "all",
                "feed_ids": [],
                "subject_template": "ThreatLens alert",
                "html_template": "<p>ThreatLens alert</p>",
            },
        )
        setup_db.add(instance)
        setup_db.flush()
        subscription = IntegrationSubscription(
            integration_id=instance.id,
            subscription_key="event:alert_match",
            event_type="alert_match",
            enabled=True,
        )
        setup_db.add(subscription)
        setup_db.flush()
        setup_db.add(
            IntegrationDelivery(
                id=delivery_id,
                integration_id=instance.id,
                subscription_id=subscription.id,
                owner_user_id=None,
                connector_type="smtp",
                event_type="alert_match",
                state="pending",
                idempotency_key=f"smtp-source-owner:{delivery_id}",
                payload_json={
                    SMTP_SOURCE_OWNER_IDS_KEY: [str(source_owner_id)],
                },
                max_attempts=3,
            )
        )
        setup_db.commit()

    writer_started = Event()
    worker_db = Session(database_engine)

    def _deactivate_source_owner() -> None:
        with Session(database_engine) as writer_db:
            writer_started.set()
            owner = writer_db.scalar(
                select(User).where(User.id == source_owner_id).with_for_update()
            )
            assert owner is not None
            owner.is_active = False
            writer_db.add(owner)
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
            writer = executor.submit(_deactivate_source_owner)
            assert writer_started.wait(timeout=2)
            time.sleep(0.1)
            assert not writer.done()
            worker_db.commit()
            writer.result(timeout=5)

        with Session(database_engine) as verify_db:
            try:
                lock_smtp_delivery_external_io_eligibility(
                    verify_db,
                    delivery_id=delivery_id,
                    expected_attempt_number=1,
                    expected_settings=expected_settings,
                )
            except SMTPDeliveryIneligibleError as exc:
                assert exc.code == "smtp_source_owner_not_eligible"
            else:
                raise AssertionError("Inactive alert source owner remained eligible")
            verify_db.rollback()
    finally:
        worker_db.rollback()
        worker_db.close()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(IntegrationInstance).where(IntegrationInstance.id == instance_id)
            )
            cleanup_db.execute(delete(User).where(User.id == source_owner_id))
            cleanup_db.commit()


def test_legacy_smtp_dispatch_holds_configuration_fence_through_send(
    database_engine,
    monkeypatch,
):
    instance_id = uuid.uuid4()
    scope_key = f"legacy-smtp-concurrency:{uuid.uuid4()}"
    with Session(database_engine) as setup_db:
        instance = IntegrationInstance(
            id=instance_id,
            system_key=SMTP_SYSTEM_KEY,
            name="Legacy SMTP",
            integration_type="smtp",
            direction="destination",
            enabled=False,
            config_json={},
        )
        apply_smtp_settings_update(
            instance,
            SMTPSettingsUpdate(
                enabled=True,
                host="smtp.example.com",
                port=25,
                security="none",
                from_email="threatlens@example.com",
                to_emails=["soc@example.com"],
                event_types=["rss_item_new"],
                subject_template="ThreatLens event",
                html_template="<p>ThreatLens event</p>",
            ),
        )
        setup_db.add(instance)
        setup_db.commit()

    send_started = Event()
    allow_send_to_finish = Event()
    writer_started = Event()

    class _BlockingSMTP:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            return 250, b"OK"

        def send_message(self, _message):
            send_started.set()
            assert allow_send_to_finish.wait(timeout=10)
            return {}

    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _settings: _BlockingSMTP(),
    )

    def _send_legacy_notification():
        with Session(database_engine) as worker_db:
            result = dispatch_smtp_notification(
                worker_db,
                event_type="rss_item_new",
                scope_key=scope_key,
            )
            worker_db.commit()
            return result

    def _disable_smtp() -> None:
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
        with ThreadPoolExecutor(max_workers=2) as executor:
            worker = executor.submit(_send_legacy_notification)
            assert send_started.wait(timeout=5)
            writer = executor.submit(_disable_smtp)
            assert writer_started.wait(timeout=5)
            time.sleep(0.1)
            assert not writer.done()
            allow_send_to_finish.set()
            result = worker.result(timeout=10)
            writer.result(timeout=10)

        assert result.sent is True
        with Session(database_engine) as verify_db:
            instance = verify_db.get(IntegrationInstance, instance_id)
            assert instance is not None
            assert instance.enabled is False
    finally:
        allow_send_to_finish.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(AuditLog).where(
                    AuditLog.resource_type == "integration_instance",
                    AuditLog.resource_id == str(instance_id),
                )
            )
            cleanup_db.execute(
                delete(IntegrationInstance).where(IntegrationInstance.id == instance_id)
            )
            cleanup_db.commit()
