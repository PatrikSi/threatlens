from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.user import User
from app.schemas.integration import SMTPSettingsUpdate
from app.services.integration_connectors.base import IntegrationEventCompatibilityError
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_delivery import CLAIMED, claim_integration_delivery
from app.services.integration_processors import process_smtp_integration_delivery
from app.services.integration_storage import (
    SMTP_SYSTEM_KEY,
    acquire_smtp_configuration_read_lock,
    acquire_smtp_configuration_write_lock,
    apply_smtp_settings_update,
    build_active_smtp_settings,
)
from app.services.smtp_integration import (
    SMTPNotificationResult,
    dispatch_smtp_notification,
)
from app.services.smtp_delivery_eligibility import (
    SMTP_SOURCE_OWNER_IDS_KEY,
    SMTPDeliveryIneligibleError,
    lock_smtp_delivery_external_io_eligibility,
)
from app.services.notification_webhook_history import (
    try_acquire_notification_delivery_lock,
)


def test_smtp_routing_waits_for_config_writer_and_refreshes_schema(
    database_engine,
    monkeypatch,
):
    instance_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        setup_db.add(
            IntegrationInstance(
                id=instance_id,
                name="SMTP routing compatibility",
                integration_type="smtp",
                direction="destination",
                enabled=True,
                schema_version=3,
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
        )
        setup_db.add(
            IntegrationSubscription(
                id=subscription_id,
                integration_id=instance_id,
                subscription_key="event:rss_item_new",
                event_type="rss_item_new",
                enabled=False,
            )
        )
        setup_db.commit()

    cached = Event()
    begin_routing = Event()
    read_lock_started = Event()

    def _observed_read_lock(db: Session) -> None:
        read_lock_started.set()
        acquire_smtp_configuration_read_lock(db)

    monkeypatch.setattr(
        "app.services.integration_connectors.smtp.acquire_smtp_configuration_read_lock",
        _observed_read_lock,
    )

    def _route_with_cached_instance() -> str:
        with Session(database_engine) as worker_db:
            instance = worker_db.get(IntegrationInstance, instance_id)
            assert instance is not None and instance.schema_version == 3
            cached.set()
            assert begin_routing.wait(timeout=5)
            try:
                SMTPIntegrationConnector().prepare_routing(
                    worker_db,
                    event=IntegrationEvent(
                        event_type="rss_item_new",
                        source_type="test",
                        source_id=str(uuid.uuid4()),
                        idempotency_key=f"smtp-routing-race:{uuid.uuid4()}",
                        payload_json={"schema_version": 1},
                    ),
                )
            except IntegrationEventCompatibilityError as exc:
                worker_db.rollback()
                return str(exc)
            raise AssertionError("Older SMTP worker routed a future configuration")

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_route_with_cached_instance)
            assert cached.wait(timeout=5)
            with Session(database_engine) as writer_db:
                acquire_smtp_configuration_write_lock(writer_db)
                instance = writer_db.scalar(
                    select(IntegrationInstance)
                    .where(IntegrationInstance.id == instance_id)
                    .with_for_update()
                )
                assert instance is not None
                instance.schema_version = 4
                instance.config_json = {
                    **instance.config_json,
                    "event_types": ["report_ready"],
                }
                writer_db.add(instance)
                writer_db.flush()
                begin_routing.set()
                assert read_lock_started.wait(timeout=5)
                time.sleep(0.1)
                assert not future.done()
                writer_db.commit()
            error_message = future.result(timeout=5)

        assert "newer configuration schema version 4" in error_message
        with Session(database_engine) as verify_db:
            instance = verify_db.get(IntegrationInstance, instance_id)
            subscription = verify_db.get(IntegrationSubscription, subscription_id)
            subscriptions = verify_db.scalars(
                select(IntegrationSubscription).where(
                    IntegrationSubscription.integration_id == instance_id
                )
            ).all()
            assert instance is not None and instance.schema_version == 4
            assert instance.config_json["event_types"] == ["report_ready"]
            assert subscription is not None and subscription.enabled is False
            assert [row.id for row in subscriptions] == [subscription_id]
    finally:
        begin_routing.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(IntegrationInstance).where(IntegrationInstance.id == instance_id)
            )
            cleanup_db.commit()


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
                    "schema_version": 3,
                    "owner_user_id": str(source_owner_id),
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


def test_ownerless_alert_deactivation_before_external_io_skips_smtp_send(
    database_engine,
    monkeypatch,
):
    source_owner_id, instance_id, event_id, delivery_id = (
        _persist_generic_alert_delivery(database_engine)
    )
    smtp_opened: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _settings: smtp_opened.append(True),
    )
    try:
        with Session(database_engine) as writer_db:
            owner = writer_db.get(User, source_owner_id)
            assert owner is not None
            owner.is_active = False
            writer_db.add(owner)
            writer_db.commit()

        with Session(database_engine) as worker_db:
            result = process_smtp_integration_delivery(
                worker_db, delivery_id=delivery_id
            )

        assert result.status == "succeeded"
        assert result.reason == "smtp_source_owner_not_eligible"
        assert smtp_opened == []
        with Session(database_engine) as verify_db:
            delivery = verify_db.get(IntegrationDelivery, delivery_id)
            assert delivery is not None
            assert delivery.state == "succeeded"
            assert delivery.last_error_code is None
    finally:
        _cleanup_generic_alert_delivery(
            database_engine,
            source_owner_id=source_owner_id,
            instance_id=instance_id,
            event_id=event_id,
        )


def test_owner_deactivation_waits_for_end_to_end_blocking_smtp_send(
    database_engine,
    monkeypatch,
):
    source_owner_id, instance_id, event_id, delivery_id = (
        _persist_generic_alert_delivery(database_engine, timeout_seconds=60)
    )
    send_started = Event()
    allow_send_to_finish = Event()
    writer_started = Event()
    effective_timeouts: list[float] = []

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

    def _open_smtp(settings):
        effective_timeouts.append(float(settings.timeout_seconds))
        return _BlockingSMTP()

    monkeypatch.setattr("app.services.smtp_integration._open_smtp", _open_smtp)

    def _process_delivery():
        with Session(database_engine) as worker_db:
            return process_smtp_integration_delivery(worker_db, delivery_id=delivery_id)

    def _deactivate_source_owner() -> None:
        with Session(database_engine) as writer_db:
            writer_db.execute(text("SET LOCAL statement_timeout = '2s'"))
            writer_started.set()
            owner = writer_db.scalar(
                select(User).where(User.id == source_owner_id).with_for_update()
            )
            assert owner is not None
            owner.is_active = False
            writer_db.add(owner)
            writer_db.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            worker = executor.submit(_process_delivery)
            assert send_started.wait(timeout=5)
            with Session(database_engine) as marker_db:
                attempt = marker_db.scalar(
                    select(IntegrationAttempt).where(
                        IntegrationAttempt.delivery_id == delivery_id,
                        IntegrationAttempt.attempt_number == 1,
                    )
                )
                assert attempt is not None
                assert attempt.response_json["external_side_effect_possible"] is True
            writer = executor.submit(_deactivate_source_owner)
            assert writer_started.wait(timeout=5)
            time.sleep(0.1)
            assert not writer.done()
            allow_send_to_finish.set()
            result = worker.result(timeout=10)
            writer.result(timeout=10)

        assert result.status == "succeeded"
        assert len(effective_timeouts) == 1
        assert 0 < effective_timeouts[0] <= 15
        with Session(database_engine) as verify_db:
            delivery = verify_db.get(IntegrationDelivery, delivery_id)
            owner = verify_db.get(User, source_owner_id)
            assert delivery is not None and delivery.state == "succeeded"
            assert owner is not None and owner.is_active is False
    finally:
        allow_send_to_finish.set()
        _cleanup_generic_alert_delivery(
            database_engine,
            source_owner_id=source_owner_id,
            instance_id=instance_id,
            event_id=event_id,
        )


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
                timeout_seconds=60,
            ),
        )
        setup_db.add(instance)
        setup_db.commit()

    send_started = Event()
    allow_send_to_finish = Event()
    writer_started = Event()
    effective_timeouts: list[float] = []

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

    def _open_smtp(settings):
        effective_timeouts.append(float(settings.timeout_seconds))
        return _BlockingSMTP()

    monkeypatch.setattr("app.services.smtp_integration._open_smtp", _open_smtp)

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
        assert len(effective_timeouts) == 1
        assert 0 < effective_timeouts[0] <= 15
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


def test_generic_smtp_waits_for_legacy_delivery_lock_and_recovers(
    database_engine,
    monkeypatch,
):
    source_owner_id, instance_id, event_id, delivery_id = (
        _persist_generic_alert_delivery(
            database_engine,
            system_key=SMTP_SYSTEM_KEY,
        )
    )
    sends: list[uuid.UUID] = []

    def _send(_active, **kwargs):
        sends.append(kwargs["delivery_id"])
        return SMTPNotificationResult(
            success=True,
            duration_ms=8,
            recipient_count=1,
            accepted_count=1,
            error_code=None,
            error=None,
            server_message="250 accepted",
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
            delivery_outcome="accepted",
            accepted_recipients=("soc@example.com",),
        )

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)
    try:
        with Session(database_engine) as lock_db:
            event = lock_db.get(IntegrationEvent, event_id)
            assert event is not None
            payload = event.payload_json
            assert try_acquire_notification_delivery_lock(
                lock_db,
                webhook_id=instance_id,
                event_type="alert_match",
                item_id=uuid.UUID(payload["item_id"]),
                feed_id=uuid.UUID(payload["feed_id"]),
            )
            with Session(database_engine) as worker_db:
                deferred = process_smtp_integration_delivery(
                    worker_db,
                    delivery_id=delivery_id,
                )

            assert deferred.status == "retry_wait"
            assert deferred.reason == "smtp_preflight_database_unavailable"
            assert sends == []
            lock_db.rollback()

        with Session(database_engine) as retry_db:
            delivery = retry_db.get(IntegrationDelivery, delivery_id)
            assert delivery is not None
            delivery.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            retry_db.add(delivery)
            retry_db.commit()
            recovered = process_smtp_integration_delivery(
                retry_db,
                delivery_id=delivery_id,
            )

        assert recovered.status == "succeeded"
        assert sends == [delivery_id]
    finally:
        _cleanup_generic_alert_delivery(
            database_engine,
            source_owner_id=source_owner_id,
            instance_id=instance_id,
            event_id=event_id,
        )


def _persist_generic_alert_delivery(
    database_engine,
    *,
    timeout_seconds: int = 10,
    system_key: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    source_owner_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    event_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    item_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    alert = {
        "count": 1,
        "primary_name": "Concurrent SMTP alert",
        "names": ["Concurrent SMTP alert"],
        "categories": ["threat"],
        "matched_keywords": ["concurrent"],
    }
    payload = {
        "schema_version": 3,
        "owner_user_id": str(source_owner_id),
        "evaluation_request_id": str(uuid.uuid4()),
        "item_id": str(item_id),
        "feed_id": str(feed_id),
        "occurrence_ids": [],
        "occurrence_count": 1,
        "occurrence_ids_truncated": True,
        "occurrence_ids_by_owner": [
            {"owner_user_id": str(source_owner_id), "occurrence_ids": []}
        ],
        "item": {
            "id": str(item_id),
            "feed_id": str(feed_id),
            "title": "Concurrent SMTP item",
            "url": "https://example.com/concurrent-smtp-item",
            "canonical_url": "https://example.com/concurrent-smtp-item",
            "summary": "A persisted SMTP concurrency snapshot",
            "published_at": None,
            "first_seen_at": None,
            "status": "new",
        },
        "feed": {
            "id": str(feed_id),
            "name": "Concurrent SMTP feed",
            "url": "https://example.com/concurrent-smtp-feed.xml",
            "site_url": "https://example.com",
            "error_count": 0,
            "last_error": None,
            "last_fetch_at": None,
            "last_success_at": None,
        },
        "alert": alert,
        "alert_matches": [
            {"owner_user_id": str(source_owner_id), **alert},
        ],
    }
    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=source_owner_id,
                email=f"smtp-e2e-source-{uuid.uuid4().hex}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        instance = IntegrationInstance(
            id=instance_id,
            system_key=system_key,
            name="End-to-end alert SMTP",
            integration_type="smtp",
            direction="destination",
            enabled=True,
            config_json={
                "host": "smtp.example.com",
                "port": 25,
                "security": "none",
                "username": None,
                "from_email": "threatlens@example.com",
                "from_name": "ThreatLens",
                "to_emails": ["soc@example.com"],
                "timeout_seconds": timeout_seconds,
                "event_types": ["alert_match"],
                "feed_scope": "all",
                "feed_ids": [],
                "subject_template": "{{ alert.primary_name }}",
                "html_template": "<p>{{ alert.primary_name }}</p>",
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
        event = IntegrationEvent(
            id=event_id,
            event_type="alert_match",
            schema_version=3,
            source_type="item",
            source_id=str(item_id),
            idempotency_key=f"smtp-e2e-alert:{event_id}",
            payload_json=payload,
        )
        setup_db.add_all([subscription, event])
        setup_db.flush()
        setup_db.add(
            IntegrationDelivery(
                id=delivery_id,
                integration_id=instance.id,
                subscription_id=subscription.id,
                event_id=event.id,
                owner_user_id=None,
                connector_type="smtp",
                event_type="alert_match",
                state="pending",
                idempotency_key=f"smtp-e2e-delivery:{delivery_id}",
                payload_json={
                    **payload,
                    SMTP_SOURCE_OWNER_IDS_KEY: [str(source_owner_id)],
                },
                max_attempts=3,
            )
        )
        setup_db.commit()
    return source_owner_id, instance_id, event_id, delivery_id


def _cleanup_generic_alert_delivery(
    database_engine,
    *,
    source_owner_id: uuid.UUID,
    instance_id: uuid.UUID,
    event_id: uuid.UUID,
) -> None:
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
        cleanup_db.execute(
            delete(IntegrationEvent).where(IntegrationEvent.id == event_id)
        )
        cleanup_db.execute(delete(User).where(User.id == source_owner_id))
        cleanup_db.commit()
