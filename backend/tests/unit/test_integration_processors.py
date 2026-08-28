import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models.alert_occurrence import AlertOccurrence
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.item import Item
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_delivery import claim_integration_delivery
from app.services.integration_processors import (
    SMTP_OWNER_NOT_ELIGIBLE,
    process_smtp_integration_delivery,
)
from app.services.integration_storage import (
    build_active_smtp_settings,
    get_smtp_credential_source,
)
from app.services.smtp_integration import SMTPNotificationResult
from app.services.smtp_delivery_eligibility import (
    SMTP_SOURCE_OWNER_IDS_KEY,
    SMTPDeliverySourceCompatibilityError,
    ensure_smtp_delivery_schema_compatible,
    lock_smtp_delivery_external_io_eligibility,
)


def test_smtp_delivery_uses_generic_claim_attempt_and_audit_history(
    db_session, monkeypatch
):
    feed, item, delivery = _persist_smtp_delivery(db_session)
    attempted_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **kwargs: SMTPNotificationResult(
            success=True,
            duration_ms=27,
            recipient_count=2,
            accepted_count=2,
            error_code=None,
            error=None,
            server_message="250 accepted",
            attempted_at=attempted_at,
            delivery_id=kwargs["delivery_id"],
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "integrations.smtp.delivery",
            AuditLog.metadata_json["delivery_id"].as_string() == str(delivery.id),
        )
    )
    assert result.status == "succeeded"
    assert delivery.state == "succeeded"
    assert delivery.attempt_count == 1
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert attempt.response_json["accepted_count"] == 2
    assert audit is not None
    assert audit.success is True
    assert audit.metadata_json["item_id"] == str(item.id)
    assert audit.metadata_json["feed_id"] == str(feed.id)


def test_smtp_delivery_with_missing_context_is_dead_lettered_with_clear_error(
    db_session,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.circuit_state = "half_open"
    instance.circuit_failure_count = 2
    delivery.payload_json = {"item_id": str(uuid.uuid4())}
    db_session.add_all([delivery, instance])
    db_session.commit()

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert delivery.last_error_code == "context_error"
    assert "Referenced item" in (delivery.last_error_message or "")
    db_session.refresh(instance)
    assert instance.circuit_state == "half_open"
    assert instance.circuit_failure_count == 2


def test_routed_smtp_delivery_is_skipped_when_owner_is_deactivated_before_send(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    delivery = _persist_routed_smtp_delivery(db_session, owner_user_id=owner.id)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.circuit_state = "half_open"
    instance.circuit_failure_count = 2
    owner.is_active = False
    db_session.add_all([owner, instance])
    db_session.commit()
    send_calls = []

    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "succeeded"
    assert result.reason == SMTP_OWNER_NOT_ELIGIBLE
    assert delivery.state == "succeeded"
    assert delivery.attempt_count == 1
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert attempt.response_json == {
        "skipped": True,
        "reason": SMTP_OWNER_NOT_ELIGIBLE,
    }
    db_session.refresh(instance)
    assert instance.circuit_state == "half_open"
    assert instance.circuit_failure_count == 2
    assert send_calls == []


def test_routed_smtp_delivery_is_skipped_when_owner_approval_is_removed_before_send(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    delivery = _persist_routed_smtp_delivery(db_session, owner_user_id=owner.id)
    owner.is_approved = False
    db_session.add(owner)
    db_session.commit()
    send_calls = []

    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "succeeded"
    assert result.reason == SMTP_OWNER_NOT_ELIGIBLE
    assert delivery.state == "succeeded"
    assert delivery.attempt_count == 1
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert attempt.response_json == {
        "skipped": True,
        "reason": SMTP_OWNER_NOT_ELIGIBLE,
    }
    assert send_calls == []


def test_smtp_delivery_rechecks_owner_after_lease_renewal(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    delivery = _persist_routed_smtp_delivery(db_session, owner_user_id=owner.id)
    send_calls = []

    def _attempt(*_args, lease_heartbeat, **kwargs):
        instance = kwargs["instance"]
        active = build_active_smtp_settings(
            instance,
            credential_source=get_smtp_credential_source(db_session, instance),
        )
        owner.is_approved = False
        db_session.add(owner)
        db_session.commit()
        lease_heartbeat(30, active)
        send_calls.append(True)
        raise AssertionError("SMTP send must not start for an ineligible owner")

    monkeypatch.setattr(
        "app.services.integration_processors.attempt_smtp_integration_delivery",
        _attempt,
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "succeeded"
    assert result.reason == SMTP_OWNER_NOT_ELIGIBLE
    assert delivery.state == "succeeded"
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert attempt.response_json["skipped"] is True
    assert attempt.response_json["reason"] == SMTP_OWNER_NOT_ELIGIBLE
    assert "no longer active and approved" in attempt.response_json["message"]
    assert send_calls == []


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("disable", "smtp_integration_disabled"),
        ("recipients", "smtp_configuration_changed"),
    ],
)
def test_smtp_delivery_rechecks_configuration_before_external_io(
    db_session,
    monkeypatch,
    mutation,
    expected_reason,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    send_calls = []

    def _attempt(*_args, lease_heartbeat, **kwargs):
        instance = kwargs["instance"]
        active = build_active_smtp_settings(
            instance,
            credential_source=get_smtp_credential_source(db_session, instance),
        )
        if mutation == "disable":
            instance.enabled = False
        else:
            config = dict(instance.config_json)
            config["to_emails"] = ["replacement@example.com"]
            instance.config_json = config
        db_session.add(instance)
        db_session.commit()
        lease_heartbeat(30, active)
        send_calls.append(True)
        raise AssertionError("SMTP send must not start with stale configuration")

    monkeypatch.setattr(
        "app.services.integration_processors.attempt_smtp_integration_delivery",
        _attempt,
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "succeeded"
    assert result.reason == expected_reason
    assert delivery.state == "succeeded"
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert attempt.response_json["skipped"] is True
    assert attempt.response_json["reason"] == expected_reason
    assert send_calls == []


def test_smtp_database_fence_failure_retries_without_external_side_effect(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    send_calls: list[bool] = []

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        send_calls.append(True)
        raise AssertionError("SMTP I/O must not start when the database fence fails")

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)
    monkeypatch.setattr(
        "app.services.integration_processors.lock_smtp_delivery_external_io_eligibility",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OperationalError("SELECT pg_advisory_xact_lock_shared(...) ", {}, OSError())
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "retry_wait"
    assert result.reason == "smtp_preflight_database_unavailable"
    assert delivery.last_error_retryable is True
    assert instance is not None and instance.circuit_failure_count == 0
    assert attempt is not None
    assert attempt.response_json == {
        "failure_class": "smtp_preflight_database",
        "delivery_outcome": "not_attempted",
        "external_side_effect_possible": False,
    }
    assert send_calls == []


def test_smtp_database_fence_timeout_retries_without_external_side_effect(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    send_calls: list[bool] = []

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        send_calls.append(True)
        raise AssertionError("SMTP I/O must not start when the database fence times out")

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)
    monkeypatch.setattr(
        "app.services.integration_processors.lock_smtp_delivery_external_io_eligibility",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("database statement timed out")
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "retry_wait"
    assert result.reason == "smtp_preflight_database_unavailable"
    assert delivery.last_error_retryable is True
    assert instance is not None and instance.circuit_failure_count == 0
    assert attempt is not None
    assert attempt.response_json["delivery_outcome"] == "not_attempted"
    assert attempt.response_json["external_side_effect_possible"] is False
    assert send_calls == []


def test_smtp_side_effect_marker_failure_retries_before_data(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    data_calls: list[bool] = []

    def _attempt(*_args, on_external_side_effect_possible, **_kwargs):
        on_external_side_effect_possible()
        data_calls.append(True)
        raise AssertionError("SMTP DATA must not start without a durable marker")

    monkeypatch.setattr(
        "app.services.integration_processors.attempt_smtp_integration_delivery",
        _attempt,
    )
    monkeypatch.setattr(
        "app.services.integration_processors.persist_external_side_effect_marker",
        lambda **_kwargs: (_ for _ in ()).throw(
            OperationalError("UPDATE integration_attempts", {}, OSError())
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "retry_wait"
    assert result.reason == "smtp_preflight_database_unavailable"
    assert delivery.last_error_retryable is True
    assert instance is not None and instance.circuit_failure_count == 0
    assert attempt is not None
    assert attempt.response_json["delivery_outcome"] == "not_attempted"
    assert attempt.response_json["external_side_effect_possible"] is False
    assert data_calls == []


def test_smtp_capability_failure_is_terminal_and_circuit_neutral(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.circuit_state = "half_open"
    instance.circuit_failure_count = 2
    db_session.add(instance)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **kwargs: SMTPNotificationResult(
            success=False,
            duration_ms=5,
            recipient_count=2,
            accepted_count=0,
            error_code="starttls_not_supported",
            error="SMTP server does not support the required STARTTLS capability.",
            server_message="STARTTLS extension not supported by server.",
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
            delivery_outcome="not_attempted",
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    db_session.refresh(instance)
    assert result.status == "dead_letter"
    assert delivery.last_error_code == "starttls_not_supported"
    assert delivery.last_error_retryable is False
    assert instance.circuit_state == "half_open"
    assert instance.circuit_failure_count == 2


@pytest.mark.parametrize(
    ("error_code", "expected_state", "expected_retryable"),
    [
        ("transient_smtp_error", "retry_wait", True),
        ("connect_rejected", "dead_letter", False),
    ],
)
def test_smtp_response_class_controls_delivery_retry_policy(
    db_session,
    monkeypatch,
    error_code,
    expected_state,
    expected_retryable,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **kwargs: SMTPNotificationResult(
            success=False,
            duration_ms=5,
            recipient_count=2,
            accepted_count=0,
            error_code=error_code,
            error="SMTP server rejected the connection request.",
            server_message=(
                "421 temporary failure"
                if expected_retryable
                else "554 permanent rejection"
            ),
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
            delivery_outcome="not_attempted",
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == expected_state
    assert result.reason == error_code
    assert (result.retry_at is not None) is expected_retryable
    assert delivery.last_error_code == error_code
    assert delivery.last_error_retryable is expected_retryable


def test_smtp_credential_lookup_database_failure_retries_before_external_io(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    send_calls: list[bool] = []

    monkeypatch.setattr(
        "app.services.smtp_integration.get_smtp_credential_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OperationalError("SELECT integration_instances", {}, OSError())
        ),
    )
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "retry_wait"
    assert result.reason == "smtp_preflight_database_unavailable"
    assert delivery.last_error_retryable is True
    assert instance is not None and instance.circuit_failure_count == 0
    assert attempt is not None
    assert attempt.response_json == {
        "failure_class": "smtp_preflight_database",
        "delivery_outcome": "not_attempted",
        "external_side_effect_possible": False,
    }
    assert send_calls == []


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("invalid", "smtp_source_owner_context_invalid"),
        ("duplicate", "smtp_source_owner_context_mismatch"),
        ("oversized", "smtp_source_owner_context_too_large"),
        ("schema_mismatch", "smtp_source_owner_context_mismatch"),
        ("invalid_schema", "smtp_source_owner_context_invalid"),
    ],
)
def test_smtp_alert_source_context_failures_are_precise_and_terminal(
    db_session,
    monkeypatch,
    case,
    expected_code,
):
    feed, item, delivery = _persist_smtp_delivery(db_session)
    first_owner_id = uuid.uuid4()
    second_owner_id = uuid.uuid4()
    if case == "invalid":
        payload = {
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            SMTP_SOURCE_OWNER_IDS_KEY: ["not-a-uuid"],
        }
    elif case == "duplicate":
        payload = {
            "schema_version": 2,
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            SMTP_SOURCE_OWNER_IDS_KEY: [str(first_owner_id), str(first_owner_id)],
        }
    elif case == "oversized":
        payload = {
            "schema_version": 2,
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            SMTP_SOURCE_OWNER_IDS_KEY: [str(uuid.uuid4()) for _ in range(101)],
        }
    elif case == "schema_mismatch":
        payload = {
            "schema_version": 3,
            "owner_user_id": str(first_owner_id),
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            SMTP_SOURCE_OWNER_IDS_KEY: [str(second_owner_id)],
        }
    else:
        payload = {
            "schema_version": True,
            "owner_user_id": str(first_owner_id),
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            SMTP_SOURCE_OWNER_IDS_KEY: [str(first_owner_id)],
        }
    _configure_delivery_as_alert(db_session, delivery=delivery, payload=payload)
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "dead_letter"
    assert result.reason == expected_code
    assert delivery.last_error_code == expected_code
    assert delivery.last_error_retryable is False
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.error_code == expected_code
    assert attempt.retryable is False
    assert attempt.response_json == {
        "failure_class": "smtp_source_owner_context",
        "delivery_outcome": "not_attempted",
        "external_side_effect_possible": False,
    }
    assert send_calls == []


def test_future_smtp_alert_schema_waits_without_consuming_an_attempt(
    db_session,
    monkeypatch,
):
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={
            "schema_version": 4,
            "item_id": str(item.id),
            "feed_id": str(feed.id),
        },
    )
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempts = db_session.scalars(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    ).all()
    assert result.status == "retry_wait"
    assert result.reason == "smtp_source_owner_context_unsupported"
    assert result.retry_at is not None
    assert delivery.attempt_count == 0
    assert delivery.last_error_retryable is True
    assert attempts == []
    assert send_calls == []


@pytest.mark.parametrize(
    ("event_type", "schema_version"),
    [("rss_item_new", 3), ("daily_digest", 2)],
)
def test_future_non_alert_smtp_schema_waits_without_consuming_an_attempt(
    db_session,
    monkeypatch,
    event_type,
    schema_version,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    delivery.event_type = event_type
    delivery.payload_json = {"schema_version": schema_version, "future": {}}
    db_session.add(delivery)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempts = db_session.scalars(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    ).all()
    assert result.status == "retry_wait"
    assert result.reason == "smtp_event_schema_unsupported"
    assert result.retry_at is not None
    assert delivery.attempt_count == 0
    assert delivery.last_error_retryable is True
    assert attempts == []
    assert send_calls == []


def test_future_smtp_configuration_waits_without_consuming_an_attempt(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.schema_version = 4
    db_session.add(instance)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempts = db_session.scalars(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    ).all()
    assert result.status == "retry_wait"
    assert result.reason == "smtp_config_schema_unsupported"
    assert delivery.attempt_count == 0
    assert delivery.last_error_retryable is True
    assert attempts == []
    assert send_calls == []


@pytest.mark.parametrize("upgrade_credential_source", [False, True])
def test_external_io_fence_revalidates_current_smtp_configuration_schema(
    db_session,
    upgrade_credential_source,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.schema_version = 3
    credential_source = None
    if upgrade_credential_source:
        credential_source = IntegrationInstance(
            id=uuid.uuid4(),
            name="SMTP credential source",
            integration_type="smtp",
            direction="destination",
            enabled=True,
            schema_version=3,
            config_json=dict(instance.config_json),
        )
        db_session.add(credential_source)
        db_session.flush()
        instance.credential_source_integration_id = credential_source.id
    db_session.add(instance)
    db_session.commit()

    ensure_smtp_delivery_schema_compatible(db_session, delivery=delivery)
    claim = claim_integration_delivery(db_session, delivery_id=delivery.id)
    assert claim.attempt_number == 1
    expected_settings = build_active_smtp_settings(
        instance,
        credential_source=credential_source,
    )
    upgraded = credential_source if upgrade_credential_source else instance
    assert upgraded is not None
    upgraded.schema_version = 4
    db_session.add(upgraded)
    db_session.commit()

    with pytest.raises(SMTPDeliverySourceCompatibilityError) as error:
        lock_smtp_delivery_external_io_eligibility(
            db_session,
            delivery_id=delivery.id,
            expected_attempt_number=claim.attempt_number,
            expected_settings=expected_settings,
        )

    assert error.value.code == "smtp_config_schema_unsupported"
    db_session.rollback()


@pytest.mark.parametrize("schema_version", [True, "9" * 5_000])
def test_invalid_non_alert_smtp_schema_is_terminal_before_external_io(
    db_session,
    monkeypatch,
    schema_version,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    delivery.payload_json = {
        **delivery.payload_json,
        "schema_version": schema_version,
    }
    db_session.add(delivery)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "dead_letter"
    assert result.reason == "smtp_event_schema_invalid"
    assert delivery.last_error_retryable is False
    assert attempt is not None
    assert attempt.error_code == "smtp_event_schema_invalid"
    assert attempt.response_json == {
        "failure_class": "smtp_event_schema",
        "delivery_outcome": "not_attempted",
        "external_side_effect_possible": False,
    }
    assert send_calls == []


@pytest.mark.parametrize("include_delivery_schema", [False, True])
def test_linked_non_alert_smtp_schema_mismatch_is_terminal_even_with_future_config(
    db_session,
    monkeypatch,
    include_delivery_schema,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.schema_version = 4
    event = IntegrationEvent(
        event_type="rss_item_new",
        schema_version=2,
        source_type="item",
        source_id=str(uuid.uuid4()),
        idempotency_key=f"smtp-schema-binding:{uuid.uuid4()}",
        payload_json={"schema_version": 2},
    )
    db_session.add_all([instance, event])
    db_session.flush()
    delivery.event_id = event.id
    if include_delivery_schema:
        delivery.payload_json = {**delivery.payload_json, "schema_version": 1}
    db_session.add(delivery)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_event_schema_mismatch"
    assert delivery.last_error_code == "smtp_event_schema_mismatch"
    assert send_calls == []


def test_linked_smtp_event_payload_schema_mismatch_is_terminal(
    db_session,
    monkeypatch,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    event = IntegrationEvent(
        event_type="rss_item_new",
        schema_version=1,
        source_type="item",
        source_id=str(uuid.uuid4()),
        idempotency_key=f"smtp-event-payload-schema-binding:{uuid.uuid4()}",
        payload_json={"schema_version": 2},
    )
    db_session.add(event)
    db_session.flush()
    delivery.event_id = event.id
    delivery.payload_json = {**delivery.payload_json, "schema_version": 2}
    db_session.add(delivery)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_event_schema_mismatch"
    assert delivery.last_error_code == "smtp_event_schema_mismatch"
    assert send_calls == []


@pytest.mark.parametrize(
    ("event_schema_version", "delivery_schema_version"),
    [(2, 4), (4, 2)],
)
def test_future_linked_smtp_schema_mismatch_is_terminal_before_compatibility_wait(
    db_session,
    monkeypatch,
    event_schema_version,
    delivery_schema_version,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    event = IntegrationEvent(
        event_type="rss_item_new",
        schema_version=event_schema_version,
        source_type="item",
        source_id=str(uuid.uuid4()),
        idempotency_key=f"smtp-future-schema-mismatch:{uuid.uuid4()}",
        payload_json={"schema_version": event_schema_version},
    )
    db_session.add(event)
    db_session.flush()
    delivery.event_id = event.id
    delivery.payload_json = {
        **delivery.payload_json,
        "schema_version": delivery_schema_version,
    }
    db_session.add(delivery)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_event_schema_mismatch"
    assert delivery.attempt_count == 1
    assert delivery.last_error_code == "smtp_event_schema_mismatch"
    assert send_calls == []


def test_linked_smtp_event_type_mismatch_is_terminal(db_session, monkeypatch):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    event = IntegrationEvent(
        event_type="feed_failing",
        schema_version=1,
        source_type="feed",
        source_id=str(uuid.uuid4()),
        idempotency_key=f"smtp-type-binding:{uuid.uuid4()}",
        payload_json={},
    )
    db_session.add(event)
    db_session.flush()
    delivery.event_id = event.id
    db_session.add(delivery)
    db_session.commit()
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_event_type_mismatch"
    assert delivery.last_error_code == "smtp_event_type_mismatch"
    assert send_calls == []


def test_stale_sending_future_smtp_schema_is_resolved_without_a_recovery_loop(
    db_session,
):
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={
            "schema_version": 4,
            "item_id": str(item.id),
            "feed_id": str(feed.id),
        },
    )
    started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    delivery.state = "sending"
    delivery.attempt_count = 1
    delivery.claimed_at = started_at
    delivery.not_before = started_at
    db_session.add(
        IntegrationAttempt(
            delivery_id=delivery.id,
            integration_id=delivery.integration_id,
            attempt_number=1,
            status="running",
            started_at=started_at,
            response_json={},
        )
    )
    db_session.add(delivery)
    db_session.commit()

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "terminal"
    assert result.reason == "unknown_delivery_outcome"
    assert delivery.state == "dead_letter"
    assert delivery.last_error_code == "unknown_delivery_outcome"
    assert attempt is not None and attempt.status == "interrupted"


def test_stale_pre_data_future_smtp_schema_waits_without_a_reclaim_loop(
    db_session,
):
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={
            "schema_version": 4,
            "item_id": str(item.id),
            "feed_id": str(feed.id),
        },
    )
    started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    delivery.state = "sending"
    delivery.attempt_count = 1
    delivery.claimed_at = started_at
    delivery.not_before = started_at
    db_session.add(
        IntegrationAttempt(
            delivery_id=delivery.id,
            integration_id=delivery.integration_id,
            attempt_number=1,
            status="running",
            started_at=started_at,
            response_json={
                "delivery_outcome": "not_attempted",
                "external_side_effect_possible": False,
            },
        )
    )
    db_session.add(delivery)
    db_session.commit()

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempts = db_session.scalars(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    ).all()
    assert result.status == "retry_wait"
    assert result.reason == "smtp_source_owner_context_unsupported"
    assert delivery.state == "retry_wait"
    assert delivery.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].status == "interrupted"
    assert attempts[0].response_json["delivery_outcome"] == "not_attempted"
    assert attempts[0].response_json["external_side_effect_possible"] is False


def test_fresh_future_schema_claim_is_not_stolen_after_integration_disable(
    db_session,
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    delivery.payload_json = {**delivery.payload_json, "schema_version": 3}
    db_session.add(delivery)
    db_session.commit()
    claimed = claim_integration_delivery(db_session, delivery_id=delivery.id)
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert claimed.attempt_number == 1
    assert instance is not None
    instance.enabled = False
    db_session.add(instance)
    db_session.commit()

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert result.status == "deferred"
    assert result.reason == "already_claimed"
    assert delivery.state == "sending"
    assert delivery.attempt_count == 1
    assert attempt is not None and attempt.status == "running"


def test_ownerless_legacy_v1_alert_delivery_backfills_source_owner_context(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=owner.id,
        name="Legacy SMTP alert",
    )
    item.title = "Mutated SMTP item"
    feed.name = "Mutated SMTP feed"
    db_session.add_all([item, feed])
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )

    captured: dict[str, str] = {}

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        captured["item_title"] = kwargs["item"].title
        captured["feed_name"] = kwargs["feed"].name
        return _successful_smtp_result(kwargs["delivery_id"], active.to_emails)

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "succeeded"
    assert delivery.payload_json[SMTP_SOURCE_OWNER_IDS_KEY] == [str(owner.id)]
    assert captured == {
        "item_title": "SMTP integration item",
        "feed_name": "SMTP feed",
    }


def test_personal_legacy_v1_alert_delivery_accepts_matching_owner_evidence(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _assign_personal_smtp_delivery_owner(db_session, delivery=delivery, owner=owner)
    _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=owner.id,
        name="Matching personal SMTP alert",
    )
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        return _successful_smtp_result(kwargs["delivery_id"], active.to_emails)

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "succeeded"
    assert delivery.payload_json[SMTP_SOURCE_OWNER_IDS_KEY] == [str(owner.id)]


def test_personal_legacy_v1_alert_delivery_rejects_unrelated_owner_evidence(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    unrelated_owner = seed_users["analyst"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _assign_personal_smtp_delivery_owner(db_session, delivery=delivery, owner=owner)
    _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=unrelated_owner.id,
        name="Unrelated personal SMTP alert",
    )
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_source_owner_context_mismatch"
    assert send_calls == []


def test_personal_legacy_v1_alert_delivery_rejects_multi_owner_evidence(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    other_owner = seed_users["analyst"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _assign_personal_smtp_delivery_owner(db_session, delivery=delivery, owner=owner)
    event = _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=owner.id,
        name="Multi-owner personal SMTP alert",
    )
    first_occurrence = db_session.scalar(
        select(AlertOccurrence).where(
            AlertOccurrence.integration_event_id == event.id
        )
    )
    assert first_occurrence is not None
    db_session.add(
        AlertOccurrence(
            rule_id_snapshot=uuid.uuid4(),
            owner_user_id=other_owner.id,
            item_id=item.id,
            item_id_snapshot=item.id,
            integration_event_id=event.id,
            rule_revision=1,
            item_content_hash=uuid.uuid4().hex * 2,
            alert_name_snapshot="Second owner alert",
            alert_category_snapshot="threat",
            alert_keywords_snapshot=["second owner"],
            matched_keywords=["second owner"],
            source_snapshot_json=dict(first_occurrence.source_snapshot_json),
            severity_snapshot="medium",
        )
    )
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_source_owner_context_mismatch"
    assert send_calls == []


def test_legacy_v1_delivery_does_not_fallback_to_mutable_resource_rows(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    event = _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=owner.id,
        name="Legacy immutable SMTP alert",
    )
    occurrence = db_session.scalar(
        select(AlertOccurrence).where(AlertOccurrence.integration_event_id == event.id)
    )
    assert occurrence is not None
    occurrence.source_snapshot_json = {}
    db_session.add(occurrence)
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_source_snapshot_missing"
    assert delivery.last_error_code == "smtp_source_snapshot_missing"
    assert send_calls == []


def test_legacy_v1_delivery_rejects_unrelated_persisted_source_owner(
    db_session,
    seed_users,
    monkeypatch,
):
    expected_owner = seed_users["viewer"]
    unrelated_owner = seed_users["analyst"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=expected_owner.id,
        name="Legacy SMTP evidence",
    )
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={
            "item_id": str(item.id),
            "feed_id": str(feed.id),
            SMTP_SOURCE_OWNER_IDS_KEY: [str(unrelated_owner.id)],
        },
    )
    send_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **_kwargs: send_calls.append(True),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert result.reason == "smtp_source_owner_context_mismatch"
    assert delivery.last_error_code == "smtp_source_owner_context_mismatch"
    assert delivery.last_error_retryable is False
    assert send_calls == []


def test_ownerless_legacy_v1_alert_deactivation_is_fenced_before_smtp_io(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    _attach_legacy_alert_event_evidence(
        db_session,
        delivery=delivery,
        item=item,
        owner_id=owner.id,
        name="Legacy SMTP alert",
    )
    owner.is_active = False
    db_session.add(owner)
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    external_io_started: list[bool] = []

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        external_io_started.append(True)
        raise AssertionError("SMTP I/O must not start for an inactive source owner")

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "succeeded"
    assert result.reason == "smtp_source_owner_not_eligible"
    assert delivery.payload_json[SMTP_SOURCE_OWNER_IDS_KEY] == [str(owner.id)]
    assert external_io_started == []


def test_ownerless_legacy_v2_alert_delivery_uses_event_owner_snapshot(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    feed, item, delivery = _persist_smtp_delivery(db_session)
    payload = _alert_snapshot_payload(feed=feed, item=item, owner_id=owner.id)
    event = IntegrationEvent(
        event_type="alert_match",
        schema_version=2,
        source_type="item",
        source_id=str(item.id),
        idempotency_key=f"legacy-v2-smtp:{uuid.uuid4()}",
        payload_json=payload,
    )
    db_session.add(event)
    db_session.flush()
    delivery.event_id = event.id
    _configure_delivery_as_alert(
        db_session,
        delivery=delivery,
        payload=dict(payload),
    )

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        return _successful_smtp_result(kwargs["delivery_id"], active.to_emails)

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "succeeded"
    assert delivery.payload_json[SMTP_SOURCE_OWNER_IDS_KEY] == [str(owner.id)]


def test_smtp_delivery_uses_v2_snapshot_when_source_rows_are_unavailable(
    db_session, monkeypatch
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    item_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    delivery.payload_json = {
        "schema_version": 2,
        "item_id": str(item_id),
        "feed_id": str(feed_id),
        "item": {
            "id": str(item_id),
            "feed_id": str(feed_id),
            "title": "Immutable SMTP item",
            "url": "https://snapshot.example/item",
            "canonical_url": "https://snapshot.example/item",
            "summary": "Persisted delivery context",
            "published_at": "2026-07-14T12:00:00+00:00",
            "first_seen_at": "2026-07-14T12:01:00+00:00",
            "status": "content_fetched",
        },
        "feed": {
            "id": str(feed_id),
            "name": "Immutable SMTP feed",
            "url": "https://snapshot.example/feed.xml",
            "site_url": "https://snapshot.example",
            "error_count": 0,
            "last_error": None,
            "last_fetch_at": None,
            "last_success_at": None,
        },
    }
    db_session.add(delivery)
    db_session.commit()
    captured: dict[str, str] = {}

    def _send(_active, **kwargs):
        captured["item_title"] = kwargs["item"].title
        captured["feed_name"] = kwargs["feed"].name
        return SMTPNotificationResult(
            success=True,
            duration_ms=12,
            recipient_count=2,
            accepted_count=2,
            error_code=None,
            error=None,
            server_message="250 accepted",
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
        )

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    assert result.status == "succeeded"
    assert captured == {
        "item_title": "Immutable SMTP item",
        "feed_name": "Immutable SMTP feed",
    }


def test_smtp_unknown_acceptance_outcome_requires_explicit_replay(
    db_session, monkeypatch
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)

    monkeypatch.setattr(
        "app.services.smtp_integration.send_smtp_notification",
        lambda *_args, **kwargs: SMTPNotificationResult(
            success=False,
            duration_ms=10_000,
            recipient_count=2,
            accepted_count=0,
            error_code="timeout",
            error="SMTP delivery timed out after DATA.",
            server_message=None,
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
            delivery_outcome="unknown",
            unknown_recipients=("soc@example.com", "ir@example.com"),
        ),
    )

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert delivery.state == "dead_letter"
    assert delivery.last_error_retryable is False
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert attempt is not None
    assert attempt.response_json["delivery_outcome"] == "unknown"
    assert attempt.response_json["external_side_effect_possible"] is True


def test_smtp_delivery_with_non_scalar_uuid_is_terminal_context_error(db_session):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    delivery.payload_json = {"item_id": {"unexpected": "object"}}
    db_session.add(delivery)
    db_session.commit()

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert delivery.attempt_count == 1
    assert delivery.last_error_code == "context_error"
    assert delivery.last_error_message == "Invalid item_id"


def test_smtp_replay_recipient_override_sends_only_still_refused_recipients(
    db_session, monkeypatch
):
    _feed, _item, delivery = _persist_smtp_delivery(db_session)
    delivery.delivery_kind = "replay"
    delivery.payload_json = {
        **delivery.payload_json,
        "smtp_recipient_override": ["ir@example.com"],
    }
    db_session.add(delivery)
    db_session.commit()
    captured_recipients: list[list[str]] = []

    def _send(active, **kwargs):
        kwargs["lease_heartbeat"](10, active)
        captured_recipients.append(list(active.to_emails))
        return SMTPNotificationResult(
            success=True,
            duration_ms=10,
            recipient_count=1,
            accepted_count=1,
            error_code=None,
            error=None,
            server_message="250 accepted",
            attempted_at=datetime.now(timezone.utc),
            delivery_id=kwargs["delivery_id"],
            delivery_outcome="accepted",
            accepted_recipients=("ir@example.com",),
        )

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    assert result.status == "succeeded"
    assert captured_recipients == [["ir@example.com"]]
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(IntegrationAttempt.delivery_id == delivery.id)
    )
    assert attempt is not None
    assert attempt.response_json["accepted_recipients"] == ["ir@example.com"]


def test_smtp_daily_digest_delivery_uses_persisted_ai_brief_snapshot(
    db_session, monkeypatch
):
    generated_at = datetime(2026, 7, 18, 9, 0, 5, tzinfo=timezone.utc)
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        system_key=f"smtp.test.{uuid.uuid4()}",
        name="AI Brief SMTP",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "from_email": "threatlens@example.com",
            "to_emails": ["soc@example.com"],
            "timeout_seconds": 10,
            "event_types": ["daily_digest"],
            "feed_scope": "selected",
            "feed_ids": [str(uuid.uuid4())],
            "subject_template": "{{ brief.title }}",
            "html_template": "<p>{{ brief.text }}</p>",
        },
    )
    db_session.add(instance)
    db_session.flush()
    subscription = IntegrationSubscription(
        integration_id=instance.id,
        subscription_key="event:daily_digest",
        event_type="daily_digest",
        feed_scope="selected",
    )
    db_session.add(subscription)
    db_session.flush()
    brief_id = uuid.uuid4()
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        subscription_id=subscription.id,
        connector_type="smtp",
        event_type="daily_digest",
        idempotency_key=f"smtp-daily-brief:{uuid.uuid4()}",
        payload_json={
            "daily_brief_id": str(brief_id),
            "scope_key": "ai_daily_brief:2026-07-18",
            "daily_brief": {
                "schema_version": 1,
                "id": str(brief_id),
                "date": "2026-07-18",
                "generated_at": generated_at.isoformat(),
                "window_start": (generated_at - timedelta(hours=24)).isoformat(),
                "window_end": generated_at.isoformat(),
                "title": "Stored AI brief title",
                "text": "Stored AI brief narrative",
                "key_points": ["Stored point"],
                "recommended_actions": ["Stored action"],
                "item_count": 4,
                "feed_names": ["CISA"],
                "top_titles": ["Stored source title"],
            },
        },
        max_attempts=3,
    )
    db_session.add(delivery)
    db_session.commit()
    captured_contexts = []

    def _send(_active, **kwargs):
        captured_contexts.append(kwargs["digest_context"])
        return SMTPNotificationResult(
            success=True,
            duration_ms=12,
            recipient_count=1,
            accepted_count=1,
            error_code=None,
            error=None,
            server_message="250 accepted",
            attempted_at=generated_at,
            delivery_id=kwargs["delivery_id"],
        )

    monkeypatch.setattr("app.services.smtp_integration.send_smtp_notification", _send)

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    assert result.status == "succeeded"
    assert len(captured_contexts) == 1
    assert captured_contexts[0].brief_id == brief_id
    assert captured_contexts[0].title == "Stored AI brief title"
    assert captured_contexts[0].brief_text == "Stored AI brief narrative"
    assert captured_contexts[0].key_points == ["Stored point"]


def _persist_smtp_delivery(db_session) -> tuple[Feed, Item, IntegrationDelivery]:
    feed = Feed(
        id=uuid.uuid4(), name="SMTP feed", url=f"https://example.com/{uuid.uuid4()}.xml"
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=str(uuid.uuid4()),
        url="https://example.com/item",
        canonical_url="https://example.com/item",
        title="SMTP integration item",
        summary="Delivery through the generic worker",
        dedupe_key=str(uuid.uuid4()),
        content_hash=uuid.uuid4().hex,
    )
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        system_key=f"smtp.test.{uuid.uuid4()}",
        name="SMTP",
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
            "to_emails": ["soc@example.com", "ir@example.com"],
            "timeout_seconds": 10,
            "event_types": ["rss_item_new"],
            "feed_scope": "all",
            "feed_ids": [],
            "subject_template": "{{ item.title }}",
            "html_template": "<p>{{ item.title }}</p>",
        },
    )
    db_session.add_all([feed, instance])
    db_session.flush()
    db_session.add(item)
    db_session.flush()
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
        idempotency_key=f"smtp-test:{uuid.uuid4()}",
        payload_json={"item_id": str(item.id), "feed_id": str(feed.id)},
        max_attempts=3,
    )
    db_session.add(delivery)
    db_session.commit()
    return feed, item, delivery


def _persist_routed_smtp_delivery(
    db_session,
    *,
    owner_user_id: uuid.UUID,
) -> IntegrationDelivery:
    feed = Feed(
        id=uuid.uuid4(),
        name="Owned SMTP feed",
        url=f"https://example.com/{uuid.uuid4()}.xml",
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=str(uuid.uuid4()),
        url="https://example.com/owned-item",
        canonical_url="https://example.com/owned-item",
        title="Owned SMTP integration item",
        summary="Delivery routed before access changes",
        dedupe_key=str(uuid.uuid4()),
        content_hash=uuid.uuid4().hex,
    )
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        owner_user_id=owner_user_id,
        name="Owned SMTP",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "from_email": "threatlens@example.com",
            "to_emails": ["owner@example.com"],
            "timeout_seconds": 10,
            "event_types": ["rss_item_new"],
            "feed_scope": "all",
            "feed_ids": [],
            "subject_template": "{{ item.title }}",
            "html_template": "<p>{{ item.title }}</p>",
        },
    )
    event = IntegrationEvent(
        id=uuid.uuid4(),
        event_type="rss_item_new",
        schema_version=1,
        source_type="item",
        source_id=str(item.id),
        idempotency_key=f"owned-smtp:{item.id}",
        payload_json={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    db_session.add_all([feed, item, instance, event])
    db_session.flush()

    connector = SMTPIntegrationConnector()
    connector.prepare_routing(db_session, event=event)
    routed = connector.route_event(db_session, event=event)
    assert len(routed.delivery_ids) == 1
    db_session.commit()
    delivery = db_session.get(IntegrationDelivery, routed.delivery_ids[0])
    assert delivery is not None
    return delivery


def _assign_personal_smtp_delivery_owner(
    db_session,
    *,
    delivery: IntegrationDelivery,
    owner,
) -> None:
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    assert instance is not None
    instance.owner_user_id = owner.id
    delivery.owner_user_id = owner.id
    db_session.add_all([instance, delivery])
    db_session.flush()


def _attach_legacy_alert_event_evidence(
    db_session,
    *,
    delivery: IntegrationDelivery,
    item: Item,
    owner_id: uuid.UUID,
    name: str,
) -> IntegrationEvent:
    feed = db_session.get(Feed, item.feed_id)
    assert feed is not None
    event = IntegrationEvent(
        event_type="alert_match",
        schema_version=1,
        source_type="item",
        source_id=str(item.id),
        idempotency_key=f"legacy-smtp-evidence:{uuid.uuid4()}",
        payload_json={
            "item_id": str(item.id),
            "feed_id": str(item.feed_id),
            "evaluation_request_id": str(uuid.uuid4()),
        },
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        AlertOccurrence(
            rule_id_snapshot=uuid.uuid4(),
            owner_user_id=owner_id,
            item_id=item.id,
            item_id_snapshot=item.id,
            integration_event_id=event.id,
            rule_revision=1,
            item_content_hash=uuid.uuid4().hex * 2,
            alert_name_snapshot=name,
            alert_category_snapshot="threat",
            alert_keywords_snapshot=["smtp integration"],
            matched_keywords=["smtp integration"],
            source_snapshot_json={
                "item": {
                    "id": str(item.id),
                    "title": item.title,
                    "summary": item.summary,
                    "url": item.url,
                    "canonical_url": item.canonical_url,
                    "published_at": item.published_at.isoformat()
                    if item.published_at is not None
                    else None,
                    "first_seen_at": item.first_seen_at.isoformat()
                    if item.first_seen_at is not None
                    else None,
                    "status": item.status,
                },
                "feed": {
                    "id": str(feed.id),
                    "name": feed.name,
                    "url": feed.url,
                },
            },
            severity_snapshot="medium",
        )
    )
    delivery.event_id = event.id
    db_session.add(delivery)
    db_session.flush()
    return event


def _configure_delivery_as_alert(
    db_session,
    *,
    delivery: IntegrationDelivery,
    payload: dict,
) -> None:
    instance = db_session.get(IntegrationInstance, delivery.integration_id)
    subscription = db_session.get(IntegrationSubscription, delivery.subscription_id)
    assert instance is not None
    assert subscription is not None
    config = dict(instance.config_json)
    config["event_types"] = ["alert_match"]
    instance.config_json = config
    subscription.subscription_key = "event:alert_match"
    subscription.event_type = "alert_match"
    delivery.event_type = "alert_match"
    delivery.payload_json = payload
    db_session.add_all([instance, subscription, delivery])
    db_session.commit()


def _successful_smtp_result(
    delivery_id: uuid.UUID,
    recipients: list[str],
) -> SMTPNotificationResult:
    return SMTPNotificationResult(
        success=True,
        duration_ms=10,
        recipient_count=len(recipients),
        accepted_count=len(recipients),
        error_code=None,
        error=None,
        server_message="250 accepted",
        attempted_at=datetime.now(timezone.utc),
        delivery_id=delivery_id,
        delivery_outcome="accepted",
        accepted_recipients=tuple(recipients),
    )


def _alert_snapshot_payload(
    *,
    feed: Feed,
    item: Item,
    owner_id: uuid.UUID,
) -> dict:
    alert = {
        "count": 1,
        "primary_name": "Legacy v2 alert",
        "names": ["Legacy v2 alert"],
        "categories": ["threat"],
        "matched_keywords": ["smtp integration"],
    }
    return {
        "schema_version": 2,
        "evaluation_request_id": str(uuid.uuid4()),
        "item_id": str(item.id),
        "feed_id": str(feed.id),
        "occurrence_ids": [],
        "occurrence_count": 1,
        "occurrence_ids_truncated": True,
        "item": {
            "id": str(item.id),
            "feed_id": str(feed.id),
            "title": item.title,
            "url": item.url,
            "canonical_url": item.canonical_url,
            "summary": item.summary,
            "published_at": None,
            "first_seen_at": None,
            "status": "new",
        },
        "feed": {
            "id": str(feed.id),
            "name": feed.name,
            "url": feed.url,
            "site_url": None,
            "error_count": 0,
            "last_error": None,
            "last_fetch_at": None,
            "last_success_at": None,
        },
        "alert": alert,
        "alert_matches": [{"owner_user_id": str(owner_id), **alert}],
    }
