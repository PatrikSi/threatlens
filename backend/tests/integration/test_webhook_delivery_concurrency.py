from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationInstance,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_compat import (
    delete_webhook_integration,
    ensure_webhook_integration,
)
from app.services.integration_delivery import (
    lock_webhook_delivery_external_io_eligibility,
)
from app.services.notification_delivery_processing import (
    process_reserved_notification_deliveries,
)
from app.services.notification_webhook_history import (
    claim_notification_webhook_delivery,
)
from app.services.notification_webhooks import process_notification_webhook_delivery


@pytest.mark.parametrize(
    ("revocation", "expected_error"),
    [
        (
            "owner",
            "Webhook owner is no longer active and approved for outbound delivery.",
        ),
        ("integration", "Webhook integration is disabled."),
    ],
)
def test_webhook_delivery_rechecks_eligibility_after_concurrent_revocation(
    database_engine,
    monkeypatch,
    revocation: str,
    expected_error: str,
):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    integration_id: uuid.UUID
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-race-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Concurrent revocation webhook",
            enabled=True,
            event_type="rss_item_new",
            url_template="https://hooks.example.com/events",
            method="POST",
            feed_scope="all",
            feed_ids_json=[],
            query_params_json=[],
            headers_json=[],
            body_mode="none",
            body_fields_json=[],
            timeout_seconds=10,
        )
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(webhook)
        setup_db.flush()
        instance, _subscription = ensure_webhook_integration(setup_db, webhook)
        integration_id = instance.id
        setup_db.add(
            NotificationWebhookDelivery(
                id=delivery_id,
                webhook_id=webhook_id,
                user_id=owner_id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="pending",
                attempt_count=0,
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/events",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                attempted_at=datetime.now(timezone.utc),
            )
        )
        setup_db.commit()

    initial_precheck_completed = Event()
    revocation_committed = Event()
    send_calls: list[bool] = []
    import app.services.notification_webhooks as webhook_service

    original_validate = webhook_service.validate_notification_delivery_target_for_actor

    def _pause_after_initial_precheck(*args, **kwargs):
        original_validate(*args, **kwargs)
        initial_precheck_completed.set()
        assert revocation_committed.wait(timeout=5)

    def _unexpected_send(*_args, **_kwargs):
        send_calls.append(True)
        raise AssertionError("Webhook HTTP request started after revocation committed")

    monkeypatch.setattr(
        webhook_service,
        "validate_notification_delivery_target_for_actor",
        _pause_after_initial_precheck,
    )
    monkeypatch.setattr(
        webhook_service,
        "_send_rendered_notification_request",
        _unexpected_send,
    )

    def _process_delivery():
        with Session(database_engine) as worker_db:
            return process_notification_webhook_delivery(
                worker_db,
                delivery_id=delivery_id,
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(_process_delivery)
            assert initial_precheck_completed.wait(timeout=5)
            with Session(database_engine) as revocation_db:
                if revocation == "owner":
                    owner = revocation_db.scalar(
                        select(User).where(User.id == owner_id).with_for_update()
                    )
                    assert owner is not None
                    owner.is_active = False
                    revocation_db.add(owner)
                else:
                    instance = revocation_db.scalar(
                        select(IntegrationInstance)
                        .where(IntegrationInstance.id == integration_id)
                        .with_for_update()
                    )
                    assert instance is not None
                    instance.enabled = False
                    revocation_db.add(instance)
                revocation_db.commit()
            revocation_committed.set()
            attempt = worker.result(timeout=5)

        assert send_calls == []
        assert attempt.claimed is True
        assert attempt.result.success is False
        assert attempt.result.error == expected_error
        assert attempt.delivery.delivery_state == "failed"
        assert attempt.delivery.error == f"policy_error:{expected_error}"
    finally:
        revocation_committed.set()
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


@pytest.mark.parametrize("disable_after_takeover", [False, True])
def test_stale_webhook_worker_cannot_mutate_replacement_attempt(
    database_engine,
    monkeypatch,
    disable_after_takeover: bool,
):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-takeover-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Lease takeover webhook",
            enabled=True,
            event_type="rss_item_new",
            url_template="https://hooks.example.com/events",
            method="POST",
            feed_scope="all",
            feed_ids_json=[],
            query_params_json=[],
            headers_json=[],
            body_mode="none",
            body_fields_json=[],
            timeout_seconds=10,
        )
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(webhook)
        setup_db.flush()
        ensure_webhook_integration(setup_db, webhook)
        setup_db.add(
            NotificationWebhookDelivery(
                id=delivery_id,
                webhook_id=webhook_id,
                user_id=owner_id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="pending",
                attempt_count=0,
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/events",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                attempted_at=datetime.now(timezone.utc),
            )
        )
        setup_db.commit()

    first_claim_paused = Event()
    allow_first_worker_to_continue = Event()
    send_calls: list[bool] = []
    import app.services.notification_webhooks as webhook_service

    original_validate = webhook_service.validate_notification_delivery_target_for_actor

    def _pause_after_first_claim(*args, **kwargs):
        original_validate(*args, **kwargs)
        first_claim_paused.set()
        assert allow_first_worker_to_continue.wait(timeout=5)

    def _unexpected_send(*_args, **_kwargs):
        send_calls.append(True)
        raise AssertionError("A stale webhook worker must not begin HTTP delivery")

    monkeypatch.setattr(
        webhook_service,
        "validate_notification_delivery_target_for_actor",
        _pause_after_first_claim,
    )
    monkeypatch.setattr(
        webhook_service,
        "_send_rendered_notification_request",
        _unexpected_send,
    )

    def _run_first_worker():
        with Session(database_engine) as worker_db:
            return process_reserved_notification_deliveries(
                worker_db,
                [delivery_id],
                process_delivery=lambda session, *, delivery_id: (
                    process_notification_webhook_delivery(
                        session,
                        delivery_id=delivery_id,
                    )
                ),
                reserve_retryable_delivery=lambda *_args, **_kwargs: None,
                reserve_failed_delivery_notifications=None,
                logger=logging.getLogger(__name__),
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first_worker = executor.submit(_run_first_worker)
            assert first_claim_paused.wait(timeout=5)

            stale_at = datetime.now(timezone.utc) - timedelta(days=1)
            with Session(database_engine) as expire_db:
                legacy = expire_db.get(NotificationWebhookDelivery, delivery_id)
                assert legacy is not None
                generic = expire_db.get(
                    IntegrationDelivery, legacy.integration_delivery_id
                )
                assert generic is not None
                legacy.claimed_at = stale_at
                legacy.not_before = None
                generic.claimed_at = stale_at
                generic.not_before = None
                expire_db.add_all([legacy, generic])
                expire_db.commit()

            with Session(database_engine) as takeover_db:
                replacement = claim_notification_webhook_delivery(
                    takeover_db,
                    delivery_id=delivery_id,
                    now=datetime.now(timezone.utc),
                )
                assert replacement is not None
                assert replacement.attempt_count == 2

            if disable_after_takeover:
                with Session(database_engine) as disable_db:
                    webhook = disable_db.scalar(
                        select(NotificationWebhook)
                        .where(NotificationWebhook.id == webhook_id)
                        .with_for_update()
                    )
                    assert webhook is not None
                    webhook.enabled = False
                    disable_db.add(webhook)
                    disable_db.commit()

            allow_first_worker_to_continue.set()
            result = first_worker.result(timeout=5)

        assert result.failed == 1
        assert send_calls == []
        with Session(database_engine) as verify_db:
            legacy = verify_db.get(NotificationWebhookDelivery, delivery_id)
            assert legacy is not None
            generic = verify_db.get(IntegrationDelivery, legacy.integration_delivery_id)
            assert generic is not None
            attempts = list(
                verify_db.scalars(
                    select(IntegrationAttempt)
                    .where(IntegrationAttempt.delivery_id == generic.id)
                    .order_by(IntegrationAttempt.attempt_number.asc())
                ).all()
            )
            assert legacy.delivery_state == "sending"
            assert legacy.attempt_count == 2
            assert generic.state == "sending"
            assert generic.attempt_count == 2
            assert [
                (attempt.attempt_number, attempt.status) for attempt in attempts
            ] == [
                (1, "interrupted"),
                (2, "running"),
            ]
    finally:
        allow_first_worker_to_continue.set()
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_webhook_delete_and_delivery_fence_use_parent_first_lock_order(
    database_engine,
):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    integration_delivery_id: uuid.UUID
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-delete-race-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Concurrent deletion webhook",
            enabled=True,
            event_type="rss_item_new",
            url_template="https://hooks.example.com/events",
            method="POST",
            feed_scope="all",
            feed_ids_json=[],
            query_params_json=[],
            headers_json=[],
            body_mode="none",
            body_fields_json=[],
            timeout_seconds=10,
        )
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(webhook)
        setup_db.flush()
        ensure_webhook_integration(setup_db, webhook)
        setup_db.add(
            NotificationWebhookDelivery(
                id=delivery_id,
                webhook_id=webhook_id,
                user_id=owner_id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="pending",
                attempt_count=0,
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/events",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                attempted_at=datetime.now(timezone.utc),
            )
        )
        setup_db.commit()

    with Session(database_engine) as claim_db:
        claimed = claim_notification_webhook_delivery(
            claim_db,
            delivery_id=delivery_id,
        )
        assert claimed is not None
        assert claimed.integration_delivery_id is not None
        integration_delivery_id = claimed.integration_delivery_id

    worker_thread_id: list[int] = []
    deleter_thread_id: list[int] = []
    worker_locked_delivery = Event()
    allow_worker_to_commit = Event()
    delete_lock_started = Event()

    def _pause_worker_after_delivery_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            worker_thread_id
            and threading.get_ident() == worker_thread_id[0]
            and "notification_webhook_deliveries" in statement.lower()
            and "for update" in statement.lower()
            and not worker_locked_delivery.is_set()
        ):
            worker_locked_delivery.set()
            assert allow_worker_to_commit.wait(timeout=10)

    def _observe_delete_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if not deleter_thread_id or threading.get_ident() != deleter_thread_id[0]:
            return
        normalized = statement.lower()
        if (
            "notification_webhooks" in normalized and "for update" in normalized
        ) or statement.lstrip().lower().startswith(
            "delete from notification_webhooks"
        ):
            delete_lock_started.set()

    def _fence_delivery() -> None:
        worker_thread_id.append(threading.get_ident())
        with Session(database_engine) as worker_db:
            lock_webhook_delivery_external_io_eligibility(
                worker_db,
                webhook_id=webhook_id,
                legacy_delivery_id=delivery_id,
                integration_delivery_id=integration_delivery_id,
                expected_attempt_number=1,
            )
            worker_db.commit()

    def _delete_webhook() -> None:
        deleter_thread_id.append(threading.get_ident())
        with Session(database_engine) as delete_db:
            webhook = delete_db.get(NotificationWebhook, webhook_id)
            assert webhook is not None
            delete_webhook_integration(delete_db, webhook)
            delete_db.commit()

    event.listen(database_engine, "after_cursor_execute", _pause_worker_after_delivery_lock)
    event.listen(database_engine, "before_cursor_execute", _observe_delete_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            worker = executor.submit(_fence_delivery)
            assert worker_locked_delivery.wait(timeout=5)
            deleter = executor.submit(_delete_webhook)
            assert delete_lock_started.wait(timeout=5)
            time.sleep(0.1)
            assert not deleter.done()
            allow_worker_to_commit.set()
            worker.result(timeout=10)
            deleter.result(timeout=10)

        with Session(database_engine) as verify_db:
            assert verify_db.get(NotificationWebhook, webhook_id) is None
            assert verify_db.get(NotificationWebhookDelivery, delivery_id) is None
            assert verify_db.get(IntegrationDelivery, integration_delivery_id) is None
    finally:
        allow_worker_to_commit.set()
        event.remove(
            database_engine,
            "after_cursor_execute",
            _pause_worker_after_delivery_lock,
        )
        event.remove(
            database_engine,
            "before_cursor_execute",
            _observe_delete_lock,
        )
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_webhook_claim_and_delete_use_parent_first_lock_order(database_engine):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-claim-delete-race-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Concurrent claim and deletion webhook",
            enabled=True,
            event_type="rss_item_new",
            url_template="https://hooks.example.com/events",
            method="POST",
            feed_scope="all",
            feed_ids_json=[],
            query_params_json=[],
            headers_json=[],
            body_mode="none",
            body_fields_json=[],
            timeout_seconds=10,
        )
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(webhook)
        setup_db.flush()
        ensure_webhook_integration(setup_db, webhook)
        setup_db.add(
            NotificationWebhookDelivery(
                id=delivery_id,
                webhook_id=webhook_id,
                user_id=owner_id,
                event_type_snapshot="rss_item_new",
                delivery_kind="live",
                delivery_state="pending",
                attempt_count=0,
                success=False,
                timeout_seconds=10,
                rendered_url="https://hooks.example.com/events",
                rendered_method="POST",
                rendered_headers_json=[],
                rendered_query_params_json=[],
                rendered_body=None,
                attempted_at=datetime.now(timezone.utc),
            )
        )
        setup_db.commit()

    worker_thread_id: list[int] = []
    deleter_thread_id: list[int] = []
    first_worker_lock: list[str] = []
    worker_lock_acquired = Event()
    allow_worker_to_continue = Event()
    delete_lock_started = Event()
    claim_finished = Event()

    def _is_parent_lock(statement: str) -> bool:
        normalized = statement.lower()
        return "notification_webhooks" in normalized and "for update" in normalized

    def _is_delivery_lock(statement: str) -> bool:
        normalized = statement.lower()
        return (
            "notification_webhook_deliveries" in normalized
            and "for update" in normalized
        )

    def _pause_worker_after_first_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            worker_thread_id
            and threading.get_ident() == worker_thread_id[0]
            and not worker_lock_acquired.is_set()
        ):
            lock_name = (
                "parent"
                if _is_parent_lock(statement)
                else "delivery"
                if _is_delivery_lock(statement)
                else None
            )
            if lock_name is not None:
                first_worker_lock.append(lock_name)
                worker_lock_acquired.set()
                assert allow_worker_to_continue.wait(timeout=10)

    def _observe_delete_lock_start(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if not deleter_thread_id or threading.get_ident() != deleter_thread_id[0]:
            return
        if _is_parent_lock(statement) or statement.lstrip().lower().startswith(
            "delete from notification_webhooks"
        ):
            delete_lock_started.set()

    def _hold_deleter_after_parent_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            deleter_thread_id
            and threading.get_ident() == deleter_thread_id[0]
            and _is_parent_lock(statement)
        ):
            assert claim_finished.wait(timeout=10)

    def _claim_delivery() -> int:
        worker_thread_id.append(threading.get_ident())
        try:
            with Session(database_engine) as worker_db:
                claimed = claim_notification_webhook_delivery(
                    worker_db,
                    delivery_id=delivery_id,
                )
                assert claimed is not None
                return claimed.attempt_count
        finally:
            claim_finished.set()

    def _delete_webhook() -> None:
        deleter_thread_id.append(threading.get_ident())
        with Session(database_engine) as delete_db:
            webhook = delete_db.get(NotificationWebhook, webhook_id)
            assert webhook is not None
            delete_webhook_integration(delete_db, webhook)
            delete_db.commit()

    event.listen(database_engine, "after_cursor_execute", _pause_worker_after_first_lock)
    event.listen(database_engine, "before_cursor_execute", _observe_delete_lock_start)
    event.listen(database_engine, "after_cursor_execute", _hold_deleter_after_parent_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            worker = executor.submit(_claim_delivery)
            assert worker_lock_acquired.wait(timeout=5)
            deleter = executor.submit(_delete_webhook)
            assert delete_lock_started.wait(timeout=5)
            time.sleep(0.1)
            assert not deleter.done()
            allow_worker_to_continue.set()
            assert worker.result(timeout=10) == 1
            deleter.result(timeout=10)

        assert first_worker_lock == ["parent"]
        with Session(database_engine) as verify_db:
            assert verify_db.get(NotificationWebhook, webhook_id) is None
            assert verify_db.get(NotificationWebhookDelivery, delivery_id) is None
    finally:
        allow_worker_to_continue.set()
        claim_finished.set()
        event.remove(
            database_engine,
            "after_cursor_execute",
            _pause_worker_after_first_lock,
        )
        event.remove(
            database_engine,
            "before_cursor_execute",
            _observe_delete_lock_start,
        )
        event.remove(
            database_engine,
            "after_cursor_execute",
            _hold_deleter_after_parent_lock,
        )
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()
