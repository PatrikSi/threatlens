from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.api.routes.notifications import delete_notification_webhook
from app.models.audit_log import AuditLog
from app.models.data_policy import (
    DataPolicyState,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationWebhookTestResponse,
    NotificationWebhookWrite,
)
from app.services import notification_webhook_http
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import DataAccessContext
from app.services.integration_compat import (
    delete_webhook_integration,
    ensure_webhook_integration,
)
from app.services.integration_delivery import (
    ensure_webhook_delivery,
    list_recoverable_webhook_delivery_ids,
    lock_webhook_delivery_external_io_eligibility,
    replay_dead_letter_delivery,
)
from app.services.integration_delivery_compatibility import (
    defer_integration_delivery_for_compatibility,
)
from app.services.integration_events import (
    emit_integration_event,
    list_recoverable_integration_event_ids,
    route_integration_event,
)
from app.services.integration_connectors.webhook import WebhookIntegrationConnector
from app.services.notification_delivery_processing import (
    process_reserved_notification_deliveries,
)
from app.services.notification_webhook_history import (
    claim_notification_webhook_delivery,
)
from app.services.webhook_delivery_locking import WebhookDeliveryBusyError
from app.services.notification_webhooks import (
    process_notification_webhook_delivery,
    reserve_retryable_notification_webhook_delivery,
    test_notification_webhook as execute_notification_webhook_test,
)
from app.services.notification_webhook_test_policy import (
    NotificationWebhookTestReplayUnsafe,
)


def test_webhook_test_concurrent_replay_never_sends_twice(
    database_engine,
    monkeypatch,
):
    owner_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    operation_id = f"concurrent-notification-test-{uuid.uuid4()}"
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"notification-test-race-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="admin",
            is_active=True,
            is_approved=True,
        )
        feed = Feed(
            id=feed_id,
            name="Concurrent webhook test feed",
            url=f"https://example.com/concurrent-test-{feed_id}.xml",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        )
        item = Item(
            id=item_id,
            feed_id=feed_id,
            source_guid=str(item_id),
            url=f"https://example.com/articles/{item_id}",
            canonical_url=f"https://example.com/articles/{item_id}",
            title="Concurrent webhook test item",
            dedupe_key=f"concurrent-webhook-test:{item_id}",
            content_hash=uuid.uuid4().hex,
            status="content_fetched",
        )
        setup_db.add_all([owner, feed])
        setup_db.flush()
        setup_db.add(item)
        setup_db.flush()
        authorization = authorization_context_for_user(setup_db, owner)
        policy_state = setup_db.get(DataPolicyState, 1)
        assert policy_state is not None
        data_access = DataAccessContext(
            mode=policy_state.mode,  # type: ignore[arg-type]
            policy_revision=policy_state.revision,
            coverage_version=policy_state.coverage_version,
            principal_type="user",
            principal_id=owner_id,
            principal_eligible=True,
            allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
        )
        setup_db.commit()

    payload = NotificationWebhookWrite(
        name="Concurrent synthetic test",
        event_type="rss_item_new",
        url_template="https://hooks.example.com/concurrent-test",
        method="POST",
        feed_scope="all",
        body_mode="none",
        timeout_seconds=10,
    )
    send_started = Event()
    allow_send_to_finish = Event()
    send_calls: list[bool] = []

    def _blocking_send(rendered):
        notification_webhook_http._mark_notification_external_io_started()
        send_calls.append(True)
        send_started.set()
        assert allow_send_to_finish.wait(timeout=5)
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=1,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview="",
            error=None,
        )

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _blocking_send,
    )

    def _execute():
        with Session(database_engine) as worker_db:
            owner = worker_db.get(User, owner_id)
            assert owner is not None
            return execute_notification_webhook_test(
                worker_db,
                user=owner,
                payload=payload,
                sample_item_id=item_id,
                data_access=data_access,
                authorization=authorization,
                operation_id=operation_id,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(_execute)
            assert send_started.wait(timeout=5)
            concurrent_replay = executor.submit(_execute)
            try:
                with pytest.raises(NotificationWebhookTestReplayUnsafe):
                    concurrent_replay.result(timeout=5)
            finally:
                allow_send_to_finish.set()
            first_result = first.result(timeout=5)

        assert first_result.success is True
        assert first_result.status_code == 204
        assert send_calls == [True]
    finally:
        allow_send_to_finish.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(AuditLog).where(AuditLog.actor_user_id == owner_id)
            )
            cleanup_db.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup_db.execute(delete(User).where(User.id == owner_id))
            cleanup_db.commit()


@pytest.mark.parametrize(
    ("revocation", "expected_error"),
    [
        (
            "owner",
            "Webhook owner is no longer active and approved for outbound delivery.",
        ),
        ("integration", "Webhook integration is disabled."),
        (
            "schema",
            "Webhook integration configuration uses schema version 2; this worker "
            "supports through version 1. Delivery will retry after the worker is upgraded.",
        ),
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
        "app.services.notification_webhook_http.send_rendered_notification_request",
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
                    if revocation == "integration":
                        instance.enabled = False
                    else:
                        instance.schema_version = 2
                    revocation_db.add(instance)
                revocation_db.commit()
            revocation_committed.set()
            attempt = worker.result(timeout=5)

        assert send_calls == []
        assert attempt.result.success is False
        assert attempt.result.error == expected_error
        if revocation == "schema":
            assert attempt.claimed is False
            assert attempt.delivery.delivery_state == "pending"
            assert attempt.delivery.error == expected_error
            with Session(database_engine) as verify_db:
                generic = verify_db.scalar(
                    select(IntegrationDelivery).where(
                        IntegrationDelivery.id
                        == attempt.delivery.integration_delivery_id
                    )
                )
                assert generic is not None and generic.state == "retry_wait"
                generic_attempt = verify_db.scalar(
                    select(IntegrationAttempt).where(
                        IntegrationAttempt.delivery_id == generic.id
                    )
                )
                assert generic.last_error_code == "unsupported_connector_config_schema"
                assert generic_attempt is not None
                assert generic_attempt.response_json["retry_budget_consumed"] is False
        else:
            assert attempt.claimed is True
            assert attempt.delivery.delivery_state == "failed"
            assert attempt.delivery.error == f"policy_error:{expected_error}"
    finally:
        revocation_committed.set()
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_first_webhook_heartbeat_schema_race_preserves_retry_budget(
    database_engine,
    monkeypatch,
):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-first-heartbeat-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="First heartbeat schema race",
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
        legacy = NotificationWebhookDelivery(
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
        setup_db.add(legacy)
        setup_db.flush()
        generic = ensure_webhook_delivery(
            setup_db,
            webhook=webhook,
            legacy_delivery=legacy,
        )
        generic.max_attempts = 1
        setup_db.add(generic)
        integration_id = instance.id
        setup_db.commit()

    lease_committed = Event()
    schema_updated = Event()
    import app.services.notification_delivery_processing as processing_service
    import app.services.notification_webhook_http as webhook_http

    original_lock = processing_service.lock_webhook_delivery_external_io_eligibility

    def _pause_before_first_heartbeat_fence(*args, **kwargs):
        lease_committed.set()
        assert schema_updated.wait(timeout=5)
        return original_lock(*args, **kwargs)

    def _unexpected_client(*_args, **_kwargs):
        raise AssertionError("HTTP client opened after an incompatible schema committed")

    monkeypatch.setattr(
        processing_service,
        "lock_webhook_delivery_external_io_eligibility",
        _pause_before_first_heartbeat_fence,
    )
    monkeypatch.setattr(webhook_http, "build_safe_http_client", _unexpected_client)

    def _run_worker():
        with Session(database_engine) as worker_db:
            return process_reserved_notification_deliveries(
                worker_db,
                [delivery_id],
                process_delivery=lambda session, *, delivery_id: (
                    process_notification_webhook_delivery(
                        session,
                        delivery_id=delivery_id,
                        commit_outcome=False,
                    )
                ),
                reserve_retryable_delivery=lambda *_args, **_kwargs: None,
                reserve_failed_delivery_notifications=None,
                logger=logging.getLogger(__name__),
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(_run_worker)
            assert lease_committed.wait(timeout=5)
            with Session(database_engine) as update_db:
                instance = update_db.scalar(
                    select(IntegrationInstance)
                    .where(IntegrationInstance.id == integration_id)
                    .with_for_update()
                )
                assert instance is not None
                instance.schema_version = 2
                update_db.add(instance)
                update_db.commit()
            schema_updated.set()
            worker.result(timeout=5)

        with Session(database_engine) as verify_db:
            legacy = verify_db.get(NotificationWebhookDelivery, delivery_id)
            assert legacy is not None
            generic = verify_db.get(IntegrationDelivery, legacy.integration_delivery_id)
            assert generic is not None
            attempt = verify_db.scalar(
                select(IntegrationAttempt).where(
                    IntegrationAttempt.delivery_id == generic.id,
                    IntegrationAttempt.attempt_number == 1,
                )
            )
            assert legacy.delivery_state == "pending"
            assert generic.state == "retry_wait"
            assert generic.attempt_count == 1
            assert attempt is not None
            assert attempt.response_json["external_side_effect_possible"] is False
            assert attempt.response_json["retry_budget_consumed"] is False

            instance = verify_db.get(IntegrationInstance, integration_id)
            assert instance is not None
            instance.schema_version = 1
            generic.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            legacy.not_before = generic.not_before
            verify_db.add_all([instance, generic, legacy])
            verify_db.commit()
            replacement = claim_notification_webhook_delivery(
                verify_db,
                delivery_id=delivery_id,
            )
            assert replacement is not None
            assert replacement.attempt_count == 2
    finally:
        schema_updated.set()
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


@pytest.mark.parametrize(
    ("configuration_change", "expected_generic_state", "expected_send_count"),
    [
        ("schema", "dead_letter", 1),
        ("disabled", "failed", 1),
        ("redirect", "failed", 1),
        ("redirect_validation", "failed", 1),
        ("redirect_malformed", "failed", 1),
        ("redirect_malformed_httpx", "failed", 1),
        ("redirect_close", "failed", 1),
        ("redirect_503", "failed", 2),
    ],
)
def test_webhook_configuration_race_after_first_request_records_unknown_outcome(
    database_engine,
    monkeypatch,
    configuration_change: str,
    expected_generic_state: str,
    expected_send_count: int,
):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-post-send-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Post-send schema race",
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
        legacy = NotificationWebhookDelivery(
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
        setup_db.add(legacy)
        setup_db.flush()
        generic = ensure_webhook_delivery(
            setup_db,
            webhook=webhook,
            legacy_delivery=legacy,
        )
        generic.max_attempts = 2 if configuration_change.startswith("redirect") else 1
        setup_db.add(generic)
        integration_id = instance.id
        setup_db.commit()

    post_send_heartbeat_committed = Event()
    schema_updated = Event()
    send_calls: list[str] = []
    import app.services.notification_delivery_processing as processing_service
    import app.services.notification_webhook_http as webhook_http

    original_lock = processing_service.lock_webhook_delivery_external_io_eligibility
    heartbeat_fences = 0

    def _pause_before_post_send_heartbeat_fence(*args, **kwargs):
        nonlocal heartbeat_fences
        heartbeat_fences += 1
        if heartbeat_fences == 3:
            post_send_heartbeat_committed.set()
            assert schema_updated.wait(timeout=5)
        return original_lock(*args, **kwargs)

    class _FailingCloseStream(webhook_http.httpx.SyncByteStream):
        def __iter__(self):
            yield b""

        def close(self):
            raise webhook_http.httpx.ReadError("Redirect response close failed")

    class _RedirectClient:
        timeout = type("Timeout", (), {"read": 10})()

        def build_request(self, method, url, **kwargs):
            return webhook_http.httpx.Request(method, url, **kwargs)

        def send(self, request, **_kwargs):
            send_calls.append(str(request.url))
            if configuration_change == "redirect_503" and request.url.path == "/next":
                return webhook_http.httpx.Response(503, request=request)
            location = "/next"
            if configuration_change == "redirect":
                location = "https://other.example.com/next"
            elif configuration_change == "redirect_malformed":
                location = "https://[::1"
            return webhook_http.httpx.Response(
                302,
                headers={"location": location},
                request=request,
                stream=(
                    _FailingCloseStream()
                    if configuration_change == "redirect_close"
                    else None
                ),
            )

    @contextmanager
    def _fake_client(*_args, **_kwargs):
        if configuration_change == "redirect_malformed_httpx":
            def _malformed_redirect(request):
                send_calls.append(str(request.url))
                return webhook_http.httpx.Response(
                    302,
                    headers={"location": "https://[::1"},
                    request=request,
                )

            with webhook_http.httpx.Client(
                transport=webhook_http.httpx.MockTransport(_malformed_redirect)
            ) as client:
                yield client
            return
        yield _RedirectClient()

    monkeypatch.setattr(
        processing_service,
        "lock_webhook_delivery_external_io_eligibility",
        _pause_before_post_send_heartbeat_fence,
    )
    monkeypatch.setattr(webhook_http, "build_safe_http_client", _fake_client)

    def _validate_request_target(url, **_kwargs):
        if configuration_change == "redirect_validation" and url.endswith("/next"):
            raise ValueError("Redirect target DNS resolution failed")

    monkeypatch.setattr(
        webhook_http,
        "ensure_runtime_fetchable_url",
        _validate_request_target,
    )

    def _run_worker():
        with Session(database_engine) as worker_db:
            return process_reserved_notification_deliveries(
                worker_db,
                [delivery_id],
                process_delivery=lambda session, *, delivery_id: (
                    process_notification_webhook_delivery(
                        session,
                        delivery_id=delivery_id,
                        commit_outcome=False,
                    )
                ),
                reserve_retryable_delivery=(
                    reserve_retryable_notification_webhook_delivery
                ),
                reserve_failed_delivery_notifications=None,
                logger=logging.getLogger(__name__),
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(_run_worker)
            if configuration_change in {
                "redirect_validation",
                "redirect_503",
            }:
                assert post_send_heartbeat_committed.wait(timeout=5)
                schema_updated.set()
            elif configuration_change not in {
                "redirect",
                "redirect_malformed",
                "redirect_malformed_httpx",
                "redirect_close",
            }:
                assert post_send_heartbeat_committed.wait(timeout=5)
                with Session(database_engine) as update_db:
                    instance = update_db.scalar(
                        select(IntegrationInstance)
                        .where(IntegrationInstance.id == integration_id)
                        .with_for_update()
                    )
                    assert instance is not None
                    if configuration_change == "schema":
                        instance.schema_version = 2
                    else:
                        instance.enabled = False
                    update_db.add(instance)
                    update_db.commit()
                schema_updated.set()
            worker_result = worker.result(timeout=5)

        expected_send_urls = ["https://hooks.example.com/events"]
        if expected_send_count == 2:
            expected_send_urls.append("https://hooks.example.com/next")
        assert send_calls == expected_send_urls
        assert worker_result.followup_deliveries == ()
        with Session(database_engine) as verify_db:
            legacy = verify_db.get(NotificationWebhookDelivery, delivery_id)
            assert legacy is not None
            generic = verify_db.get(IntegrationDelivery, legacy.integration_delivery_id)
            assert generic is not None
            attempt = verify_db.scalar(
                select(IntegrationAttempt).where(
                    IntegrationAttempt.delivery_id == generic.id,
                    IntegrationAttempt.attempt_number == 1,
                )
            )
            assert legacy.delivery_state == "failed"
            assert legacy.not_before is None
            assert generic.state == expected_generic_state
            assert attempt is not None
            assert attempt.response_json["delivery_outcome"] == "unknown"
            assert attempt.response_json["external_side_effect_possible"] is True
            assert "retry_budget_consumed" not in attempt.response_json
            if configuration_change == "disabled" or configuration_change.startswith(
                "redirect"
            ):
                assert (legacy.error or "").startswith("policy_error:")
                assert attempt.retryable is False
            if configuration_change.startswith("redirect"):
                expected_error_code = {
                    "redirect_malformed_httpx": "ambiguous_webhook_response",
                    "redirect_503": "ambiguous_webhook_response",
                }.get(configuration_change, "redirect_policy_error")
                assert attempt.error_code == expected_error_code
                assert generic.not_before is None
                deliveries = verify_db.scalars(
                    select(IntegrationDelivery).where(
                        IntegrationDelivery.owner_user_id == owner_id
                    )
                ).all()
                assert [row.id for row in deliveries] == [generic.id]
            _assert_post_send_response_metadata(
                configuration_change=configuration_change,
                legacy=legacy,
                generic=generic,
                attempt=attempt,
            )
    finally:
        schema_updated.set()
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def _assert_post_send_response_metadata(
    *,
    configuration_change: str,
    legacy: NotificationWebhookDelivery,
    generic: IntegrationDelivery,
    attempt: IntegrationAttempt,
) -> None:
    if configuration_change != "redirect_503":
        return
    assert legacy.status_code == 503
    assert generic.last_status_code == 503
    assert attempt.status_code == 503
    assert attempt.response_json["final_response_status_code"] == 503


def test_webhook_created_between_prepare_and_route_waits_for_compatible_worker(
    database_engine,
    monkeypatch,
):
    owner_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-route-race-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        feed = Feed(
            id=feed_id,
            name="Webhook route race feed",
            url=f"https://example.com/{feed_id}.xml",
            enabled=True,
            fetch_interval_seconds=1800,
        )
        item = Item(
            id=item_id,
            feed_id=feed_id,
            source_guid=str(item_id),
            url=f"https://example.com/articles/{item_id}",
            canonical_url=f"https://example.com/articles/{item_id}",
            title="Webhook route race article",
            dedupe_key=f"route-race:{item_id}",
            content_hash=uuid.uuid4().hex,
            status="content_fetched",
        )
        setup_db.add_all([owner, feed])
        setup_db.flush()
        setup_db.add(item)
        setup_db.flush()
        event = emit_integration_event(
            setup_db,
            event_type="rss_item_new",
            source_type="item",
            source_id=item.id,
            idempotency_key=f"webhook-route-race:{item.id}",
            payload={"item_id": str(item.id), "feed_id": str(feed.id)},
        )
        event_id = event.id
        setup_db.commit()

    preparation_completed = Event()
    future_webhook_committed = Event()
    original_prepare = WebhookIntegrationConnector.prepare_routing

    def _pause_after_prepare(self, db, *, event):
        original_prepare(self, db, event=event)
        if event.id == event_id:
            preparation_completed.set()
            assert future_webhook_committed.wait(timeout=5)

    monkeypatch.setattr(
        WebhookIntegrationConnector,
        "prepare_routing",
        _pause_after_prepare,
    )
    monkeypatch.setattr(
        "app.services.integration_events.settings.integration_event_routing_max_attempts",
        1,
    )

    def _route_event():
        with Session(database_engine) as worker_db:
            result = route_integration_event(worker_db, event_id=event_id)
            worker_db.commit()
            return result

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(_route_event)
            assert preparation_completed.wait(timeout=5)
            with Session(database_engine) as writer_db:
                webhook = NotificationWebhook(
                    id=uuid.uuid4(),
                    user_id=owner_id,
                    name="Future webhook inserted during routing",
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
                writer_db.add(webhook)
                writer_db.flush()
                instance, _subscription = ensure_webhook_integration(
                    writer_db, webhook
                )
                instance.schema_version = 2
                instance.config_json = {
                    **instance.config_json,
                    "future_option": True,
                }
                writer_db.add(instance)
                writer_db.commit()
            future_webhook_committed.set()
            first = worker.result(timeout=5)

        with Session(database_engine) as verify_db:
            event = verify_db.get(IntegrationEvent, event_id)
            assert event is not None
            assert first.status == "failed"
            assert event.routing_attempt_count == 0
            assert all(error.compatibility_wait for error in first.routing_errors)
            assert "schema version 2" in (event.last_error or "")
            assert event.id in list_recoverable_integration_event_ids(
                verify_db,
                now=event.available_at + timedelta(seconds=1),
            )
    finally:
        future_webhook_committed.set()
        with Session(database_engine) as cleanup_db:
            event = cleanup_db.get(IntegrationEvent, event_id)
            if event is not None:
                cleanup_db.delete(event)
            item = cleanup_db.get(Item, item_id)
            if item is not None:
                cleanup_db.delete(item)
            feed = cleanup_db.get(Feed, feed_id)
            if feed is not None:
                cleanup_db.delete(feed)
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_direct_webhook_worker_exit_after_send_prevents_automatic_duplicate(
    database_engine,
    monkeypatch,
):
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"webhook-direct-crash-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Direct webhook crash boundary",
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
        legacy = NotificationWebhookDelivery(
            id=delivery_id,
            webhook_id=webhook_id,
            user_id=owner_id,
            event_type_snapshot="rss_item_new",
            delivery_kind="retry",
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
        setup_db.add(legacy)
        setup_db.flush()
        generic = ensure_webhook_delivery(
            setup_db,
            webhook=webhook,
            legacy_delivery=legacy,
        )
        generic.max_attempts = 1
        setup_db.add(generic)
        setup_db.commit()

    send_calls: list[str] = []
    import app.services.notification_webhook_http as webhook_http

    class _SimulatedWorkerExit(BaseException):
        pass

    class _CrashAfterAcceptanceClient:
        timeout = type("Timeout", (), {"read": 10})()

        def build_request(self, method, url, **kwargs):
            return webhook_http.httpx.Request(method, url, **kwargs)

        def send(self, request, **_kwargs):
            send_calls.append(str(request.url))
            raise _SimulatedWorkerExit()

    @contextmanager
    def _fake_client(*_args, **_kwargs):
        yield _CrashAfterAcceptanceClient()

    monkeypatch.setattr(webhook_http, "build_safe_http_client", _fake_client)
    monkeypatch.setattr(
        webhook_http,
        "ensure_runtime_fetchable_url",
        lambda *_args, **_kwargs: None,
    )

    try:
        with Session(database_engine) as worker_db:
            with pytest.raises(_SimulatedWorkerExit):
                process_notification_webhook_delivery(
                    worker_db,
                    delivery_id=delivery_id,
                )
            worker_db.rollback()

        assert send_calls == ["https://hooks.example.com/events"]
        stale_at = datetime.now(timezone.utc) - timedelta(days=1)
        with Session(database_engine) as recovery_db:
            legacy = recovery_db.get(NotificationWebhookDelivery, delivery_id)
            assert legacy is not None
            generic = recovery_db.get(
                IntegrationDelivery, legacy.integration_delivery_id
            )
            assert generic is not None
            attempt = recovery_db.scalar(
                select(IntegrationAttempt).where(
                    IntegrationAttempt.delivery_id == generic.id,
                    IntegrationAttempt.attempt_number == 1,
                )
            )
            assert attempt is not None
            assert attempt.response_json["external_side_effect_possible"] is True
            legacy.claimed_at = stale_at
            legacy.not_before = None
            generic.claimed_at = stale_at
            generic.not_before = None
            recovery_db.add_all([legacy, generic])
            recovery_db.commit()

            deferred = defer_integration_delivery_for_compatibility(
                recovery_db,
                delivery_id=generic.id,
                error_code="unsupported_connector_config_schema",
                error_message="Future webhook schema",
                now=datetime.now(timezone.utc),
            )
            recovery_db.commit()
            assert deferred.status == "retry_wait"
            recovery_db.refresh(attempt)
            assert attempt.response_json["delivery_outcome"] == "unknown"
            assert "retry_budget_consumed" not in attempt.response_json

            generic.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            legacy.not_before = generic.not_before
            recovery_db.add_all([generic, legacy])
            recovery_db.commit()
            replacement = claim_notification_webhook_delivery(
                recovery_db,
                delivery_id=delivery_id,
            )
            assert replacement is None
            recovery_db.refresh(generic)
            recovery_db.refresh(legacy)
            assert generic.state == "dead_letter"
            assert legacy.delivery_state == "failed"
    finally:
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
        "app.services.notification_webhook_http.send_rendered_notification_request",
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


def _persist_webhook_delivery(
    database_engine,
    *,
    email_prefix: str,
    dead_letter: bool,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    owner_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"{email_prefix}-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        webhook = NotificationWebhook(
            id=webhook_id,
            user_id=owner_id,
            name="Replay lock ordering webhook",
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
        legacy = NotificationWebhookDelivery(
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
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(webhook)
        setup_db.flush()
        setup_db.add(legacy)
        setup_db.flush()
        generic = ensure_webhook_delivery(
            setup_db,
            webhook=webhook,
            legacy_delivery=legacy,
        )
        if dead_letter:
            generic.state = "dead_letter"
            generic.attempt_count = 1
            generic.dead_lettered_at = datetime.now(timezone.utc)
            generic.last_error_message = "Connection refused"
        setup_db.add(generic)
        setup_db.commit()
        return owner_id, delivery_id, generic.id


def _persist_dead_letter_webhook_delivery(
    database_engine,
    *,
    email_prefix: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return _persist_webhook_delivery(
        database_engine,
        email_prefix=email_prefix,
        dead_letter=True,
    )


@pytest.mark.parametrize(
    ("failure_stage", "redirected"),
    [
        ("read", False),
        ("close", False),
        ("read", True),
        ("close", True),
    ],
)
def test_successful_webhook_response_preview_failure_does_not_retry(
    database_engine,
    monkeypatch,
    failure_stage: str,
    redirected: bool,
):
    owner_id, delivery_id, generic_delivery_id = _persist_webhook_delivery(
        database_engine,
        email_prefix=f"webhook-response-{failure_stage}-{redirected}",
        dead_letter=False,
    )
    send_calls: list[str] = []
    import app.services.notification_webhook_http as webhook_http

    class _FailingReadStream(webhook_http.httpx.SyncByteStream):
        def __iter__(self):
            raise webhook_http.httpx.ReadError("Response body read failed")

    class _FailingCloseStream(webhook_http.httpx.SyncByteStream):
        def __iter__(self):
            yield b"accepted"

        def close(self):
            raise webhook_http.httpx.ReadError("Response close failed")

    class _ResponseClient:
        timeout = type("Timeout", (), {"read": 10})()

        def build_request(self, method, url, **kwargs):
            return webhook_http.httpx.Request(method, url, **kwargs)

        def send(self, request, **_kwargs):
            send_calls.append(str(request.url))
            if redirected and request.url.path == "/events":
                return webhook_http.httpx.Response(
                    302,
                    headers={"location": "/accepted"},
                    request=request,
                )
            stream = (
                _FailingReadStream()
                if failure_stage == "read"
                else _FailingCloseStream()
            )
            return webhook_http.httpx.Response(
                200,
                request=request,
                stream=stream,
            )

    @contextmanager
    def _fake_client(*_args, **_kwargs):
        yield _ResponseClient()

    monkeypatch.setattr(webhook_http, "build_safe_http_client", _fake_client)
    monkeypatch.setattr(
        webhook_http,
        "ensure_runtime_fetchable_url",
        lambda *_args, **_kwargs: None,
    )

    try:
        with Session(database_engine) as worker_db:
            result = process_reserved_notification_deliveries(
                worker_db,
                [delivery_id],
                process_delivery=lambda session, *, delivery_id: (
                    process_notification_webhook_delivery(
                        session,
                        delivery_id=delivery_id,
                    )
                ),
                reserve_retryable_delivery=(
                    reserve_retryable_notification_webhook_delivery
                ),
                reserve_failed_delivery_notifications=None,
                logger=logging.getLogger(__name__),
            )

        assert result.delivered == 1
        assert result.failed == 0
        assert result.followup_deliveries == ()
        expected_send_calls = ["https://hooks.example.com/events"]
        if redirected:
            expected_send_calls.append("https://hooks.example.com/accepted")
        assert send_calls == expected_send_calls
        with Session(database_engine) as verify_db:
            legacy = verify_db.get(NotificationWebhookDelivery, delivery_id)
            generic = verify_db.get(IntegrationDelivery, generic_delivery_id)
            attempt = verify_db.scalar(
                select(IntegrationAttempt).where(
                    IntegrationAttempt.delivery_id == generic_delivery_id,
                    IntegrationAttempt.attempt_number == 1,
                )
            )
            assert legacy is not None
            assert legacy.delivery_state == "succeeded"
            assert legacy.status_code == 200
            assert generic is not None
            assert generic.state == "succeeded"
            assert generic.last_status_code == 200
            assert attempt is not None
            assert attempt.status == "succeeded"
            assert attempt.status_code == 200
            deliveries = verify_db.scalars(
                select(IntegrationDelivery).where(
                    IntegrationDelivery.owner_user_id == owner_id
                )
            ).all()
            assert [delivery.id for delivery in deliveries] == [generic_delivery_id]
    finally:
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_webhook_replay_and_recovery_use_parent_first_lock_order(database_engine):
    owner_id, delivery_id, generic_delivery_id = (
        _persist_dead_letter_webhook_delivery(
            database_engine,
            email_prefix="webhook-replay-order",
        )
    )

    recovery_thread_id: list[int] = []
    replay_thread_id: list[int] = []
    recovery_locked_parent = Event()
    allow_recovery_to_continue = Event()
    replay_parent_lock_started = Event()

    def _pause_recovery_after_parent_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            recovery_thread_id
            and threading.get_ident() == recovery_thread_id[0]
            and "notification_webhooks" in statement.lower()
            and "for update" in statement.lower()
            and not recovery_locked_parent.is_set()
        ):
            recovery_locked_parent.set()
            assert allow_recovery_to_continue.wait(timeout=10)

    def _observe_replay_parent_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            replay_thread_id
            and threading.get_ident() == replay_thread_id[0]
            and "notification_webhooks" in statement.lower()
            and "for update" in statement.lower()
        ):
            replay_parent_lock_started.set()

    def _recover_delivery():
        recovery_thread_id.append(threading.get_ident())
        with Session(database_engine) as recovery_db:
            return claim_notification_webhook_delivery(
                recovery_db,
                delivery_id=delivery_id,
            )

    def _replay_delivery() -> uuid.UUID:
        replay_thread_id.append(threading.get_ident())
        with Session(database_engine) as replay_db:
            replay = replay_dead_letter_delivery(
                replay_db,
                delivery_id=generic_delivery_id,
            )
            replay_id = replay.id
            replay_db.commit()
            return replay_id

    event.listen(
        database_engine,
        "after_cursor_execute",
        _pause_recovery_after_parent_lock,
    )
    event.listen(
        database_engine,
        "before_cursor_execute",
        _observe_replay_parent_lock,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            recovery = executor.submit(_recover_delivery)
            assert recovery_locked_parent.wait(timeout=5)
            replay = executor.submit(_replay_delivery)
            assert replay_parent_lock_started.wait(timeout=5)
            assert not replay.done()
            allow_recovery_to_continue.set()
            assert recovery.result(timeout=10) is None
            replay_id = replay.result(timeout=10)

        with Session(database_engine) as verify_db:
            source = verify_db.get(NotificationWebhookDelivery, delivery_id)
            replay_projection = verify_db.get(NotificationWebhookDelivery, replay_id)
            replay_delivery = verify_db.get(IntegrationDelivery, replay_id)
            assert source is not None
            assert source.delivery_state == "failed"
            assert replay_projection is not None
            assert replay_projection.source_delivery_id == source.id
            assert replay_delivery is not None
            assert replay_delivery.source_delivery_id == generic_delivery_id
    finally:
        allow_recovery_to_continue.set()
        event.remove(
            database_engine,
            "after_cursor_execute",
            _pause_recovery_after_parent_lock,
        )
        event.remove(
            database_engine,
            "before_cursor_execute",
            _observe_replay_parent_lock,
        )
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


@pytest.mark.parametrize("current_operation", ["replay", "recovery"])
def test_current_webhook_workers_yield_to_legacy_replay_lock_order(
    database_engine,
    current_operation: str,
):
    owner_id, delivery_id, generic_delivery_id = (
        _persist_dead_letter_webhook_delivery(
            database_engine,
            email_prefix=f"webhook-mixed-order-{current_operation}",
        )
    )
    legacy_thread_id: list[int] = []
    current_thread_id: list[int] = []
    legacy_generic_locked = Event()
    allow_legacy_child_lock = Event()
    legacy_child_lock_started = Event()
    current_child_locked = Event()

    def _observe_legacy_child_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            legacy_thread_id
            and threading.get_ident() == legacy_thread_id[0]
            and "notification_webhook_deliveries" in statement.lower()
            and "for update" in statement.lower()
        ):
            legacy_child_lock_started.set()

    def _pause_current_after_child_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            current_thread_id
            and threading.get_ident() == current_thread_id[0]
            and "notification_webhook_deliveries" in statement.lower()
            and "for update" in statement.lower()
            and not current_child_locked.is_set()
        ):
            current_child_locked.set()
            allow_legacy_child_lock.set()
            assert legacy_child_lock_started.wait(timeout=5)

    def _run_legacy_replay_lock_order() -> None:
        legacy_thread_id.append(threading.get_ident())
        with Session(database_engine) as legacy_db:
            generic = legacy_db.scalar(
                select(IntegrationDelivery)
                .where(IntegrationDelivery.id == generic_delivery_id)
                .with_for_update()
            )
            assert generic is not None
            legacy_generic_locked.set()
            assert allow_legacy_child_lock.wait(timeout=10)
            legacy = legacy_db.scalar(
                select(NotificationWebhookDelivery)
                .where(NotificationWebhookDelivery.id == delivery_id)
                .with_for_update()
            )
            assert legacy is not None
            legacy_db.commit()

    def _run_current_operation():
        current_thread_id.append(threading.get_ident())
        with Session(database_engine) as current_db:
            if current_operation == "recovery":
                return claim_notification_webhook_delivery(
                    current_db,
                    delivery_id=delivery_id,
                )
            try:
                replay_dead_letter_delivery(
                    current_db,
                    delivery_id=generic_delivery_id,
                )
            except Exception as exc:  # asserted in the parent thread
                return exc
            raise AssertionError("mixed-version replay unexpectedly acquired all locks")

    event.listen(
        database_engine,
        "before_cursor_execute",
        _observe_legacy_child_lock,
    )
    event.listen(
        database_engine,
        "after_cursor_execute",
        _pause_current_after_child_lock,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            legacy_worker = executor.submit(_run_legacy_replay_lock_order)
            assert legacy_generic_locked.wait(timeout=5)
            current_worker = executor.submit(_run_current_operation)
            assert current_child_locked.wait(timeout=5)
            outcome = current_worker.result(timeout=10)
            legacy_worker.result(timeout=10)

        if current_operation == "replay":
            assert isinstance(outcome, WebhookDeliveryBusyError)
            assert str(outcome) == (
                "Webhook delivery replay is busy; retry the request shortly."
            )
        else:
            assert outcome is None

        with Session(database_engine) as verify_db:
            source = verify_db.get(NotificationWebhookDelivery, delivery_id)
            assert source is not None
            assert source.delivery_state == "pending"
            assert delivery_id in list_recoverable_webhook_delivery_ids(verify_db)
            assert (
                verify_db.scalar(
                    select(IntegrationDelivery).where(
                        IntegrationDelivery.source_delivery_id
                        == generic_delivery_id
                    )
                )
                is None
            )
    finally:
        allow_legacy_child_lock.set()
        event.remove(
            database_engine,
            "before_cursor_execute",
            _observe_legacy_child_lock,
        )
        event.remove(
            database_engine,
            "after_cursor_execute",
            _pause_current_after_child_lock,
        )
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_webhook_delete_returns_conflict_for_legacy_replay_lock_order(
    database_engine,
):
    owner_id, delivery_id, generic_delivery_id = (
        _persist_dead_letter_webhook_delivery(
            database_engine,
            email_prefix="webhook-mixed-order-delete",
        )
    )
    with Session(database_engine) as lookup_db:
        legacy = lookup_db.get(NotificationWebhookDelivery, delivery_id)
        assert legacy is not None
        webhook_id = legacy.webhook_id

    legacy_thread_id: list[int] = []
    delete_thread_id: list[int] = []
    legacy_generic_locked = Event()
    allow_legacy_child_lock = Event()
    legacy_child_locked = Event()
    allow_legacy_commit = Event()
    delete_generic_lock_started = Event()

    def _observe_legacy_child_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            legacy_thread_id
            and threading.get_ident() == legacy_thread_id[0]
            and "notification_webhook_deliveries" in statement.lower()
            and "for update" in statement.lower()
        ):
            legacy_child_locked.set()

    def _pause_delete_before_generic_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            delete_thread_id
            and threading.get_ident() == delete_thread_id[0]
            and "integration_deliveries" in statement.lower()
            and "for update" in statement.lower()
            and not delete_generic_lock_started.is_set()
        ):
            delete_generic_lock_started.set()
            allow_legacy_child_lock.set()

    def _run_legacy_replay_lock_order() -> None:
        legacy_thread_id.append(threading.get_ident())
        with Session(database_engine) as legacy_db:
            generic = legacy_db.scalar(
                select(IntegrationDelivery)
                .where(IntegrationDelivery.id == generic_delivery_id)
                .with_for_update()
            )
            assert generic is not None
            legacy_generic_locked.set()
            assert allow_legacy_child_lock.wait(timeout=10)
            legacy = legacy_db.scalar(
                select(NotificationWebhookDelivery)
                .where(NotificationWebhookDelivery.id == delivery_id)
                .with_for_update()
            )
            assert legacy is not None
            assert allow_legacy_commit.wait(timeout=10)
            legacy_db.commit()

    def _delete_through_api() -> HTTPException:
        delete_thread_id.append(threading.get_ident())
        with Session(database_engine) as delete_db:
            owner = delete_db.get(User, owner_id)
            assert owner is not None
            try:
                delete_notification_webhook(
                    webhook_id=webhook_id,
                    db=delete_db,
                    user=owner,
                    _scope_user=owner,
                )
            except HTTPException as exc:
                return exc
            raise AssertionError("mixed-version webhook deletion unexpectedly succeeded")

    event.listen(
        database_engine,
        "after_cursor_execute",
        _observe_legacy_child_lock,
    )
    event.listen(
        database_engine,
        "before_cursor_execute",
        _pause_delete_before_generic_lock,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            legacy_worker = executor.submit(_run_legacy_replay_lock_order)
            assert legacy_generic_locked.wait(timeout=5)
            delete_worker = executor.submit(_delete_through_api)
            assert delete_generic_lock_started.wait(timeout=5)
            error = delete_worker.result(timeout=10)
            assert legacy_child_locked.wait(timeout=5)
            allow_legacy_commit.set()
            legacy_worker.result(timeout=10)

        assert error.status_code == 409
        assert error.detail == (
            "Webhook delivery processing is busy; retry deletion shortly."
        )
        with Session(database_engine) as verify_db:
            assert verify_db.get(NotificationWebhook, webhook_id) is not None
            assert (
                verify_db.get(NotificationWebhookDelivery, delivery_id) is not None
            )
            assert verify_db.get(IntegrationDelivery, generic_delivery_id) is not None
    finally:
        allow_legacy_child_lock.set()
        allow_legacy_commit.set()
        event.remove(
            database_engine,
            "after_cursor_execute",
            _observe_legacy_child_lock,
        )
        event.remove(
            database_engine,
            "before_cursor_execute",
            _pause_delete_before_generic_lock,
        )
        with Session(database_engine) as cleanup_db:
            owner = cleanup_db.get(User, owner_id)
            if owner is not None:
                cleanup_db.delete(owner)
            cleanup_db.commit()


def test_webhook_delete_yields_to_legacy_claim_lock_order_and_can_retry(
    database_engine,
):
    owner_id, delivery_id, generic_delivery_id = _persist_webhook_delivery(
        database_engine,
        email_prefix="webhook-mixed-order-claim-delete",
        dead_letter=False,
    )
    with Session(database_engine) as lookup_db:
        legacy = lookup_db.get(NotificationWebhookDelivery, delivery_id)
        assert legacy is not None
        webhook_id = legacy.webhook_id

    legacy_thread_id: list[int] = []
    delete_thread_id: list[int] = []
    legacy_locked = Event()
    allow_legacy_parent_lock = Event()
    legacy_parent_lock_started = Event()
    delete_legacy_lock_started = Event()

    def _observe_legacy_parent_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            legacy_thread_id
            and threading.get_ident() == legacy_thread_id[0]
            and "notification_webhooks" in statement.lower()
            and "for update" in statement.lower()
        ):
            legacy_parent_lock_started.set()

    def _pause_delete_before_legacy_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            delete_thread_id
            and threading.get_ident() == delete_thread_id[0]
            and "notification_webhook_deliveries" in statement.lower()
            and "for update" in statement.lower()
            and not delete_legacy_lock_started.is_set()
        ):
            delete_legacy_lock_started.set()
            allow_legacy_parent_lock.set()
            assert legacy_parent_lock_started.wait(timeout=5)

    def _run_legacy_claim_lock_order() -> None:
        legacy_thread_id.append(threading.get_ident())
        with Session(database_engine) as legacy_db:
            legacy = legacy_db.scalar(
                select(NotificationWebhookDelivery)
                .where(NotificationWebhookDelivery.id == delivery_id)
                .with_for_update()
            )
            assert legacy is not None
            legacy_locked.set()
            assert allow_legacy_parent_lock.wait(timeout=10)
            webhook = legacy_db.scalar(
                select(NotificationWebhook)
                .where(NotificationWebhook.id == webhook_id)
                .with_for_update()
            )
            assert webhook is not None
            legacy_db.commit()

    def _delete_through_api() -> HTTPException:
        delete_thread_id.append(threading.get_ident())
        with Session(database_engine) as delete_db:
            owner = delete_db.get(User, owner_id)
            assert owner is not None
            try:
                delete_notification_webhook(
                    webhook_id=webhook_id,
                    db=delete_db,
                    user=owner,
                    _scope_user=owner,
                )
            except HTTPException as exc:
                return exc
            raise AssertionError("mixed-version webhook deletion unexpectedly succeeded")

    event.listen(
        database_engine,
        "before_cursor_execute",
        _observe_legacy_parent_lock,
    )
    event.listen(
        database_engine,
        "before_cursor_execute",
        _pause_delete_before_legacy_lock,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            legacy_worker = executor.submit(_run_legacy_claim_lock_order)
            assert legacy_locked.wait(timeout=5)
            delete_worker = executor.submit(_delete_through_api)
            assert delete_legacy_lock_started.wait(timeout=5)
            error = delete_worker.result(timeout=10)
            legacy_worker.result(timeout=10)

        assert error.status_code == 409
        assert error.detail == (
            "Webhook delivery processing is busy; retry deletion shortly."
        )
        with Session(database_engine) as retry_db:
            owner = retry_db.get(User, owner_id)
            assert owner is not None
            delete_notification_webhook(
                webhook_id=webhook_id,
                db=retry_db,
                user=owner,
                _scope_user=owner,
            )

        with Session(database_engine) as verify_db:
            assert verify_db.get(NotificationWebhook, webhook_id) is None
            assert verify_db.get(NotificationWebhookDelivery, delivery_id) is None
            assert verify_db.get(IntegrationDelivery, generic_delivery_id) is None
    finally:
        allow_legacy_parent_lock.set()
        event.remove(
            database_engine,
            "before_cursor_execute",
            _observe_legacy_parent_lock,
        )
        event.remove(
            database_engine,
            "before_cursor_execute",
            _pause_delete_before_legacy_lock,
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
