import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

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
from app.services.integration_processors import (
    SMTP_OWNER_NOT_ELIGIBLE,
    process_smtp_integration_delivery,
)
from app.services.smtp_integration import SMTPNotificationResult


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
    delivery.payload_json = {"item_id": str(uuid.uuid4())}
    db_session.add(delivery)
    db_session.commit()

    result = process_smtp_integration_delivery(db_session, delivery_id=delivery.id)

    db_session.refresh(delivery)
    assert result.status == "dead_letter"
    assert delivery.last_error_code == "context_error"
    assert "Referenced item" in (delivery.last_error_message or "")


def test_routed_smtp_delivery_is_skipped_when_owner_is_deactivated_before_send(
    db_session,
    seed_users,
    monkeypatch,
):
    owner = seed_users["viewer"]
    delivery = _persist_routed_smtp_delivery(db_session, owner_user_id=owner.id)
    owner.is_active = False
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

    def _attempt(*_args, lease_heartbeat, **_kwargs):
        owner.is_approved = False
        db_session.add(owner)
        db_session.commit()
        lease_heartbeat(30)
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
    assert attempt.response_json == {
        "skipped": True,
        "reason": SMTP_OWNER_NOT_ELIGIBLE,
    }
    assert send_calls == []


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
