import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.notification import NotificationWebhookField
from app.services.integration_compat import WebhookConfigurationCompatibilityError
from app.services.integration_delivery import (
    claim_integration_delivery,
    claim_webhook_delivery,
    defer_unclaimed_integration_delivery,
    ensure_webhook_delivery,
    finalize_integration_delivery,
    finalize_webhook_delivery,
    list_recoverable_integration_delivery_ids,
    list_recoverable_webhook_delivery_ids,
    replay_dead_letter_delivery,
    renew_integration_delivery_lease,
    reserve_recoverable_integration_deliveries,
)
from app.services.notification_delivery_processing import (
    process_reserved_notification_deliveries,
)
from app.services.notification_webhook_history import (
    claim_notification_webhook_delivery,
    create_pending_notification_webhook_delivery,
)
from app.services.notification_webhook_requests import (
    RenderedNotificationRequest,
    rendered_request_from_delivery,
)
from app.services.notification_webhook_compatibility import (
    WebhookExternalIOFenceError,
    defer_claimed_notification_webhook_for_preflight_error,
)


def test_webhook_delivery_state_and_attempts_are_owned_by_generic_engine(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    generic = ensure_webhook_delivery(
        db_session, webhook=webhook, legacy_delivery=legacy
    )
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
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == generic.id)
    )
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
    first = claim_webhook_delivery(
        db_session, webhook=webhook, legacy_delivery=legacy, now=started_at
    )
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


def test_webhook_claim_reconciles_terminal_delivery_from_legacy_worker(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    generic = ensure_webhook_delivery(
        db_session, webhook=webhook, legacy_delivery=legacy
    )
    completed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    legacy.delivery_state = "succeeded"
    legacy.success = True
    legacy.attempt_count = 1
    legacy.status_code = 204
    legacy.duration_ms = 18
    legacy.attempted_at = completed_at
    db_session.add(legacy)
    db_session.commit()

    claimed = claim_webhook_delivery(
        db_session,
        webhook=webhook,
        legacy_delivery=legacy,
        now=completed_at + timedelta(seconds=1),
    )

    db_session.refresh(generic)
    assert claimed is None
    assert generic.state == "succeeded"
    assert generic.attempt_count == 1
    assert generic.completed_at == completed_at
    assert generic.last_status_code == 204
    assert (
        db_session.scalar(
            select(IntegrationAttempt).where(
                IntegrationAttempt.delivery_id == generic.id
            )
        )
        is None
    )


def test_terminal_generic_webhook_projection_is_recoverable_after_commit_gap(
    db_session,
):
    webhook, legacy = _persist_legacy_delivery(db_session)
    generic = ensure_webhook_delivery(
        db_session, webhook=webhook, legacy_delivery=legacy
    )
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    generic.state = "retry_wait"
    generic.attempt_count = 1
    generic.max_attempts = 1
    generic.not_before = now - timedelta(seconds=1)
    generic.last_status_code = 503
    generic.last_duration_ms = 27
    db_session.add(generic)
    db_session.commit()

    terminal = claim_integration_delivery(
        db_session,
        delivery_id=generic.id,
        now=now,
    )

    db_session.refresh(legacy)
    assert terminal.status == "terminal"
    assert generic.state == "dead_letter"
    assert legacy.delivery_state == "pending"
    assert legacy.attempt_count == 0
    assert legacy.id in list_recoverable_webhook_delivery_ids(
        db_session,
        now=now,
    )

    instance = db_session.get(IntegrationInstance, generic.integration_id)
    assert instance is not None
    instance.schema_version = 2
    db_session.add(instance)
    db_session.commit()

    def _old_worker(*_args, **_kwargs):
        raise WebhookConfigurationCompatibilityError(
            "Older worker cannot read connector schema version 2"
        )

    old_worker_result = process_reserved_notification_deliveries(
        db_session,
        [legacy.id],
        process_delivery=_old_worker,
        reserve_retryable_delivery=lambda *_args, **_kwargs: None,
        reserve_failed_delivery_notifications=None,
        logger=logging.getLogger(__name__),
    )
    db_session.refresh(legacy)
    assert old_worker_result.failed == 1
    assert legacy.delivery_state == "failed"
    assert legacy.attempt_count == 0
    assert "Older worker cannot read connector schema version 2" in (
        legacy.error or ""
    )
    assert legacy.id in list_recoverable_webhook_delivery_ids(
        db_session,
        now=now + timedelta(seconds=1),
    )

    claimed = claim_notification_webhook_delivery(
        db_session,
        delivery_id=legacy.id,
        now=now + timedelta(seconds=1),
    )

    assert claimed is None
    assert legacy.delivery_state == "failed"
    assert legacy.attempt_count == 1
    assert legacy.status_code == 503
    assert legacy.duration_ms == 27
    assert legacy.error == "Delivery exhausted its configured attempts."
    assert legacy.attempted_at == generic.dead_lettered_at


def test_webhook_preflight_failure_preserves_prior_external_io_marker(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    claimed = claim_webhook_delivery(
        db_session,
        webhook=webhook,
        legacy_delivery=legacy,
    )
    assert claimed is not None
    generic = db_session.get(IntegrationDelivery, claimed.integration_delivery_id)
    assert generic is not None
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == generic.id,
            IntegrationAttempt.attempt_number == 1,
        )
    )
    assert attempt is not None
    attempt.response_json = {
        "delivery_outcome": "unknown",
        "external_side_effect_possible": True,
    }
    db_session.add(attempt)
    db_session.commit()

    deferred = defer_claimed_notification_webhook_for_preflight_error(
        db_session,
        delivery=claimed,
        expected_attempt_number=1,
        error=WebhookExternalIOFenceError("Database marker unavailable"),
        commit_outcome=True,
    )

    db_session.refresh(generic)
    db_session.refresh(attempt)
    assert deferred.claimed is False
    assert generic.state == "failed"
    assert attempt.response_json["delivery_outcome"] == "unknown"
    assert attempt.response_json["external_side_effect_possible"] is True
    assert attempt.response_json["automatic_retry_suppressed"] is True
    assert "retry_budget_consumed" not in attempt.response_json


def test_webhook_claim_reconciles_inflight_delivery_from_legacy_worker(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    generic = ensure_webhook_delivery(
        db_session, webhook=webhook, legacy_delivery=legacy
    )
    claimed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    legacy.delivery_state = "sending"
    legacy.attempt_count = 1
    legacy.claimed_at = claimed_at
    legacy.attempted_at = claimed_at
    db_session.add(legacy)
    db_session.commit()

    claimed = claim_webhook_delivery(
        db_session,
        webhook=webhook,
        legacy_delivery=legacy,
        now=claimed_at + timedelta(seconds=1),
    )

    db_session.refresh(generic)
    assert claimed is None
    assert generic.state == "sending"
    assert generic.attempt_count == 1
    assert generic.claimed_at == claimed_at
    assert (
        db_session.scalar(
            select(IntegrationAttempt).where(
                IntegrationAttempt.delivery_id == generic.id
            )
        )
        is None
    )


def test_generic_delivery_retries_with_backoff_and_rejects_stale_results(
    db_session, monkeypatch
):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.integration_delivery_retry_backoff_seconds",
        30,
    )
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.integration_delivery_retry_max_backoff_seconds",
        300,
    )
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    claim = claim_integration_delivery(
        db_session, delivery_id=delivery.id, now=started_at
    )
    outcome = finalize_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        expected_attempt_number=claim.attempt_number or 0,
        success=False,
        duration_ms=50,
        error_code="timeout",
        error_message="SMTP password=hunter2 timed out.",
        retryable=True,
        finished_at=started_at + timedelta(seconds=1),
    )
    db_session.commit()

    assert claim.status == "claimed"
    assert outcome.recorded is True
    assert outcome.state == "retry_wait"
    assert outcome.retry_at is not None
    assert outcome.retry_at >= started_at + timedelta(seconds=31)
    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert attempt is not None
    assert delivery.last_error_message == "SMTP password=[REDACTED] timed out."
    assert attempt.error_message == delivery.last_error_message
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


def test_active_operation_lease_prevents_stale_reclaim(db_session, monkeypatch):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.notification_delivery_sending_stale_after_seconds",
        30,
    )
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    claim = claim_integration_delivery(
        db_session, delivery_id=delivery.id, now=started_at
    )

    renewed = renew_integration_delivery_lease(
        db_session,
        delivery_id=delivery.id,
        expected_attempt_number=claim.attempt_number or 0,
        lease_seconds=120,
        now=started_at + timedelta(seconds=10),
    )
    db_session.commit()

    assert renewed is True
    assert delivery.id not in list_recoverable_integration_delivery_ids(
        db_session,
        now=started_at + timedelta(seconds=60),
    )
    active = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at + timedelta(seconds=60),
    )
    assert active.status == "deferred"
    assert active.reason == "active_lease"

    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert attempt is not None
    attempt.response_json = {
        "delivery_outcome": "unknown",
        "external_side_effect_possible": True,
    }
    db_session.add(attempt)
    db_session.commit()

    recovered = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at + timedelta(seconds=131),
    )
    assert recovered.status == "terminal"
    assert recovered.reason == "unknown_delivery_outcome"
    db_session.refresh(delivery)
    assert delivery.state == "dead_letter"
    assert delivery.last_error_retryable is False
    db_session.refresh(attempt)
    assert attempt.status == "interrupted"
    assert attempt.response_json["delivery_outcome"] == "unknown"


def test_fresh_claim_is_preserved_when_integration_is_disabled(db_session, monkeypatch):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.notification_delivery_sending_stale_after_seconds",
        30,
    )
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    claimed = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at,
    )
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert claimed.attempt_number == 1
    assert instance is not None
    instance.enabled = False
    db_session.add(instance)
    db_session.commit()

    duplicate = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at + timedelta(seconds=1),
    )

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert duplicate.status == "deferred"
    assert duplicate.reason == "already_claimed"
    assert delivery.state == "sending"
    assert delivery.attempt_count == 1
    assert attempt is not None and attempt.status == "running"


@pytest.mark.parametrize(
    ("marker", "expected_reason"),
    [
        (False, "integration_disabled"),
        (True, "unknown_delivery_outcome"),
        (None, "unknown_delivery_outcome"),
    ],
)
def test_stale_smtp_attempt_is_classified_before_disabled_integration(
    db_session,
    monkeypatch,
    marker,
    expected_reason,
):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.notification_delivery_sending_stale_after_seconds",
        30,
    )
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    claimed = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at,
    )
    assert claimed.attempt_number == 1
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert instance is not None
    assert attempt is not None
    attempt.response_json = (
        {
            "delivery_outcome": "unknown",
            "external_side_effect_possible": True,
        }
        if marker is True
        else {
            "delivery_outcome": "not_attempted",
            "external_side_effect_possible": False,
        }
        if marker is False
        else {}
    )
    instance.enabled = False
    db_session.add_all([attempt, instance])
    db_session.commit()

    recovered = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at + timedelta(seconds=31),
    )

    db_session.refresh(delivery)
    db_session.refresh(attempt)
    assert recovered.status == "terminal"
    assert recovered.reason == expected_reason
    assert delivery.state == "dead_letter"
    assert delivery.last_error_code == expected_reason
    assert attempt.status == "interrupted"
    assert attempt.response_json["external_side_effect_possible"] is (marker is not False)


def test_stale_smtp_attempt_before_external_side_effect_is_reclaimed(
    db_session, monkeypatch
):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.notification_delivery_sending_stale_after_seconds",
        30,
    )
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    first = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at,
    )
    first_attempt = db_session.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == delivery.id,
            IntegrationAttempt.attempt_number == 1,
        )
    )

    assert first.status == "claimed"
    assert first_attempt is not None
    assert first_attempt.response_json == {
        "delivery_outcome": "not_attempted",
        "external_side_effect_possible": False,
    }

    recovered = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=started_at + timedelta(seconds=31),
    )

    assert recovered.status == "claimed"
    assert recovered.attempt_number == 2
    attempts = db_session.scalars(
        select(IntegrationAttempt)
        .where(IntegrationAttempt.delivery_id == delivery.id)
        .order_by(IntegrationAttempt.attempt_number)
    ).all()
    assert [(attempt.attempt_number, attempt.status) for attempt in attempts] == [
        (1, "interrupted"),
        (2, "running"),
    ]
    assert attempts[0].retryable is True
    assert attempts[0].response_json["delivery_outcome"] == "not_attempted"
    assert attempts[0].response_json["external_side_effect_possible"] is False


def test_recovery_publication_reservation_suppresses_duplicate_sweeps_without_blocking_claim(
    db_session,
    monkeypatch,
):
    delivery = _persist_generic_delivery(db_session)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.notification_delivery_sending_stale_after_seconds",
        30,
    )
    reserved_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    first = reserve_recoverable_integration_deliveries(db_session, now=reserved_at)
    db_session.commit()
    second = reserve_recoverable_integration_deliveries(
        db_session,
        now=reserved_at + timedelta(seconds=10),
    )

    assert first.delivery_ids == (delivery.id,)
    assert second.delivery_ids == ()
    claim = claim_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        now=reserved_at + timedelta(seconds=11),
    )
    assert claim.status == "claimed"


def test_active_operation_lease_still_counts_toward_instance_concurrency(
    db_session, monkeypatch
):
    first = _persist_generic_delivery(db_session, max_concurrency=1)
    second = _persist_generic_delivery(db_session, instance_id=first.integration_id)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.notification_delivery_sending_stale_after_seconds",
        30,
    )
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    claim = claim_integration_delivery(db_session, delivery_id=first.id, now=started_at)
    renew_integration_delivery_lease(
        db_session,
        delivery_id=first.id,
        expected_attempt_number=claim.attempt_number or 0,
        lease_seconds=120,
        now=started_at + timedelta(seconds=10),
    )
    db_session.commit()

    second_claim = claim_integration_delivery(
        db_session,
        delivery_id=second.id,
        now=started_at + timedelta(seconds=60),
    )

    assert second_claim.status == "deferred"
    assert second_claim.reason == "concurrency_limited"


def test_generic_claim_enforces_per_instance_concurrency(db_session):
    first = _persist_generic_delivery(db_session, max_concurrency=1)
    second = _persist_generic_delivery(db_session, instance_id=first.integration_id)
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    first_claim = claim_integration_delivery(db_session, delivery_id=first.id, now=now)
    second_claim = claim_integration_delivery(
        db_session, delivery_id=second.id, now=now
    )

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

    second_claim = claim_integration_delivery(
        db_session, delivery_id=second.id, now=now + timedelta(seconds=2)
    )

    assert second_claim.status == "deferred"
    assert second_claim.reason == "rate_limited"


def test_retryable_failure_opens_circuit_and_dead_letter_can_be_replayed(
    db_session, monkeypatch
):
    delivery = _persist_generic_delivery(db_session, max_attempts=1)
    monkeypatch.setattr(
        "app.services.integration_delivery.settings.integration_delivery_circuit_failure_threshold",
        1,
    )
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
    assert "smtp_recipient_override" not in replay.payload_json


def test_smtp_partial_acceptance_replay_targets_only_refused_recipients(db_session):
    delivery = _persist_generic_delivery(db_session)
    delivery.state = "dead_letter"
    delivery.attempt_count = 1
    delivery.dead_lettered_at = datetime.now(timezone.utc)
    db_session.add(
        IntegrationAttempt(
            delivery_id=delivery.id,
            integration_id=delivery.integration_id,
            attempt_number=1,
            status="failed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            error_code="recipient_rejected",
            retryable=False,
            response_json={
                "delivery_outcome": "partial",
                "accepted_recipients": ["accepted@example.com"],
                "refused_recipients": ["refused@example.com"],
            },
        )
    )
    db_session.commit()

    replay = replay_dead_letter_delivery(db_session, delivery_id=delivery.id)

    assert replay.payload_json["smtp_recipient_override"] == ["refused@example.com"]


def test_webhook_dead_letter_replay_creates_legacy_history_projection(db_session):
    webhook, legacy = _persist_legacy_delivery(db_session)
    generic = ensure_webhook_delivery(
        db_session, webhook=webhook, legacy_delivery=legacy
    )
    generic.state = "dead_letter"
    generic.dead_lettered_at = datetime.now(timezone.utc)
    legacy.delivery_state = "failed"
    legacy.error = "Connection refused"
    db_session.add_all([generic, legacy])
    db_session.commit()

    replay = replay_dead_letter_delivery(db_session, delivery_id=generic.id)
    db_session.flush()
    projection = db_session.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.integration_delivery_id == replay.id
        )
    )

    assert replay.connector_type == "webhook"
    assert replay.delivery_kind == "replay"
    assert replay.source_delivery_id == generic.id
    assert replay.payload_json["legacy_webhook_delivery_id"] == str(replay.id)
    assert projection is not None
    assert projection.id == replay.id
    assert projection.delivery_kind == "retry"
    assert projection.delivery_state == "pending"
    assert projection.source_delivery_id == legacy.id
    assert projection.rendered_url == legacy.rendered_url


def test_webhook_replay_preserves_encrypted_request_snapshot_for_processing(
    db_session,
):
    webhook, _legacy = _persist_legacy_delivery(db_session)
    source_id = uuid.uuid4()
    rendered_source = RenderedNotificationRequest(
        method="POST",
        url="https://example.com/hook",
        headers=[
            NotificationWebhookField(
                key="Authorization",
                value="Bearer replay-secret",
            )
        ],
        query_params=[
            NotificationWebhookField(key="token", value="query-secret")
        ],
        body='{"message":"encrypted replay"}',
        headers_dict={"Authorization": "Bearer replay-secret"},
        query_param_pairs=[("token", "query-secret")],
        json_body={"message": "encrypted replay"},
        form_body=None,
        raw_body=None,
        timeout_seconds=10,
    )
    source = create_pending_notification_webhook_delivery(
        db_session,
        delivery_id=source_id,
        webhook=webhook,
        event_type="rss_item_new",
        rendered=rendered_source,
        delivery_kind="live",
        item_id=None,
        feed_id=None,
        item_title=None,
        feed_name=None,
        source_delivery_id=None,
        scope_key=None,
        attempted_at=datetime.now(timezone.utc),
        not_before=None,
    )
    generic = db_session.get(IntegrationDelivery, source.integration_delivery_id)
    assert generic is not None
    generic.state = "dead_letter"
    generic.dead_lettered_at = datetime.now(timezone.utc)
    source.delivery_state = "failed"
    source.error = "Connection refused"
    db_session.add_all([generic, source])
    db_session.commit()

    assert isinstance(source.rendered_headers_json, dict)
    assert isinstance(source.rendered_query_params_json, dict)
    replay = replay_dead_letter_delivery(db_session, delivery_id=generic.id)
    db_session.flush()
    projection = db_session.scalar(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.integration_delivery_id == replay.id
        )
    )

    assert projection is not None
    assert projection.rendered_headers_json == source.rendered_headers_json
    assert projection.rendered_query_params_json == source.rendered_query_params_json
    rendered_replay = rendered_request_from_delivery(projection)
    assert rendered_replay.headers_dict["Authorization"] == "Bearer replay-secret"
    assert rendered_replay.query_param_pairs == [("token", "query-secret")]
    assert rendered_replay.body == '{"message":"encrypted replay"}'


def test_webhook_dead_letter_replay_rejects_missing_history_projection(db_session):
    delivery = _persist_generic_delivery(db_session)
    delivery.connector_type = "webhook"
    delivery.state = "dead_letter"
    delivery.payload_json = []
    db_session.add(delivery)
    db_session.commit()

    try:
        replay_dead_letter_delivery(db_session, delivery_id=delivery.id)
    except ValueError as exc:
        assert (
            str(exc) == "Webhook delivery history is unavailable and cannot be replayed"
        )
    else:
        raise AssertionError("expected replay without webhook history to fail")

    assert (
        db_session.scalar(
            select(IntegrationDelivery).where(
                IntegrationDelivery.source_delivery_id == delivery.id
            )
        )
        is None
    )


def test_unknown_connector_delivery_is_deferred_for_rolling_upgrade(db_session):
    delivery = _persist_generic_delivery(db_session)
    delivery.connector_type = "future-connector"
    started_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    deferred = defer_unclaimed_integration_delivery(
        db_session,
        delivery_id=delivery.id,
        error_code="unsupported_connector",
        error_message="Connector is not installed on this worker.",
        delay_seconds=90,
        now=started_at,
    )

    assert deferred is True
    assert delivery.state == "retry_wait"
    assert delivery.not_before == started_at + timedelta(seconds=90)
    assert delivery.last_error_retryable is True
    assert delivery.dead_lettered_at is None


def _persist_legacy_delivery(
    db_session,
) -> tuple[NotificationWebhook, NotificationWebhookDelivery]:
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
