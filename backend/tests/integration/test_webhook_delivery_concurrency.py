from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationInstance,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_compat import ensure_webhook_integration
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


def test_stale_webhook_worker_cannot_mutate_replacement_attempt(
    database_engine,
    monkeypatch,
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
