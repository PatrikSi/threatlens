import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select

from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.models.user import User
from app.schemas.notification import NotificationWebhookTestResponse
from app.services.daily_brief_notifications import daily_brief_context_from_payload
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_connectors.webhook import WebhookIntegrationConnector
from app.services.integration_events import route_integration_event
from app.services.integration_connectors.base import IntegrationEventContextError
from app.services.integration_processors import process_smtp_integration_delivery
from app.services.notification_webhooks import process_notification_webhook_delivery
from app.services.notification_webhook_templates import (
    render_notification_template_text,
)
from app.services.webhook_delivery_eligibility import (
    WebhookDeliveryTemporarilyIneligibleError,
)
from app.services.report_notifications import emit_report_ready_event
from app.services.report_event_compatibility import report_ready_event_owner_id
from app.services.report_rendering import (
    render_report_html,
    render_report_markdown,
    render_report_pdf,
)
from app.services.report_storage import report_detail_response


def test_report_ready_event_is_idempotent_and_uses_immutable_report_snapshot(
    db_session,
):
    report = _persist_ready_report(db_session)
    original_owner_id = report.owner_user_id

    first = emit_report_ready_event(db_session, report=report)
    report.owner_user_id = _persist_user(db_session).id
    second = emit_report_ready_event(db_session, report=report)
    context = daily_brief_context_from_payload(first.payload_json)

    assert second.id == first.id
    assert db_session.query(IntegrationEvent).count() == 1
    assert first.event_type == "report_ready"
    assert first.source_type == "report"
    assert first.schema_version == 2
    assert first.idempotency_key == f"report:{report.id}:ready:v1"
    assert first.actor_user_id == original_owner_id
    assert first.payload_json["owner_user_id"] == str(original_owner_id)
    assert first.payload_json["report_url"] == f"/reporting/{report.id}"
    assert context.brief_id == report.id
    assert context.title == report.title
    assert context.brief_url == f"/reporting/{report.id}"
    assert context.total_items == 1
    assert context.key_points == ["Validate identity controls"]
    rendered_url = render_notification_template_text(
        "{{ brief.url }}|{{ brief.url_html }}",
        user=SimpleNamespace(id=uuid.uuid4(), email="analyst@example.com"),
        feed=None,
        item=None,
        event_type="report_ready",
        digest_context=context,
    )
    assert rendered_url == f"/reporting/{report.id}|/reporting/{report.id}"


def test_report_ready_event_reuses_alternate_v2_idempotency_key(db_session):
    report = _persist_ready_report(db_session)
    existing = emit_report_ready_event(db_session, report=report)
    existing.idempotency_key = f"report:{report.id}:ready:v2"
    db_session.flush()

    replayed = emit_report_ready_event(db_session, report=report)

    assert replayed.id == existing.id
    assert db_session.query(IntegrationEvent).count() == 1


def test_report_ready_is_supported_by_destination_connectors():
    assert SMTPIntegrationConnector().supports_event_type("report_ready")
    assert WebhookIntegrationConnector().supports_event_type("report_ready")


def test_report_ready_routes_smtp_and_webhook_only_to_report_owner(
    db_session,
    monkeypatch,
):
    owner = _persist_user(db_session)
    other_user = _persist_user(db_session)
    owner_smtp = _persist_report_smtp(db_session, owner=owner)
    _persist_report_smtp(db_session, owner=other_user)
    global_smtp = _persist_report_smtp(db_session, owner=None)
    owner_webhook = _persist_report_webhook(db_session, owner=owner)
    _persist_report_webhook(db_session, owner=other_user)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    event = emit_report_ready_event(db_session, report=report)

    result = route_integration_event(db_session, event_id=event.id)

    deliveries = db_session.scalars(
        select(IntegrationDelivery).where(IntegrationDelivery.event_id == event.id)
    ).all()
    webhook_deliveries = db_session.scalars(
        select(NotificationWebhookDelivery).where(
            NotificationWebhookDelivery.integration_delivery_id.in_(
                [delivery.id for delivery in deliveries]
            )
        )
    ).all()
    assert result.status == "routed"
    assert len(deliveries) == 3
    assert {delivery.connector_type for delivery in deliveries} == {"smtp", "webhook"}
    assert {delivery.owner_user_id for delivery in deliveries} == {owner.id}
    assert {delivery.payload_json["owner_user_id"] for delivery in deliveries} == {
        str(owner.id)
    }
    assert {
        delivery.integration_id
        for delivery in deliveries
        if delivery.connector_type == "smtp"
    } == {owner_smtp.id, global_smtp.id}
    assert {delivery.webhook_id for delivery in webhook_deliveries} == {
        owner_webhook.id
    }
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: _FakeSMTP(sent_messages),
    )
    monkeypatch.setattr(
        "app.services.integration_processors.persist_external_side_effect_marker",
        lambda **_kwargs: True,
    )
    global_delivery = next(
        delivery for delivery in deliveries if delivery.integration_id == global_smtp.id
    )

    processed = process_smtp_integration_delivery(
        db_session,
        delivery_id=global_delivery.id,
    )

    assert processed.status == "succeeded", processed.reason
    assert len(sent_messages) == 1


def test_legacy_report_ready_snapshots_source_owner_once_for_all_connectors(
    db_session,
):
    owner = _persist_user(db_session)
    _persist_report_smtp(db_session, owner=owner)
    _persist_report_webhook(db_session, owner=owner)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    event = emit_report_ready_event(db_session, report=report)
    legacy_payload = dict(event.payload_json)
    legacy_payload.pop("owner_user_id")
    event.payload_json = legacy_payload
    event.schema_version = 1
    report_owner_queries = 0

    def count_report_owner_query(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal report_owner_queries
        normalized = statement.lower()
        if "select reports.id" in normalized and "from reports" in normalized:
            report_owner_queries += 1

    bind = db_session.get_bind()
    sqlalchemy_event.listen(bind, "before_cursor_execute", count_report_owner_query)
    try:
        with db_session.no_autoflush:
            resolved_owner_id = report_ready_event_owner_id(db_session, event=event)
    finally:
        sqlalchemy_event.remove(bind, "before_cursor_execute", count_report_owner_query)
    result = route_integration_event(db_session, event_id=event.id)

    deliveries = db_session.scalars(
        select(IntegrationDelivery).where(IntegrationDelivery.event_id == event.id)
    ).all()
    assert result.status == "routed"
    assert resolved_owner_id == owner.id
    assert report_owner_queries == 1
    assert event.schema_version == 2
    assert event.payload_json["schema_version"] == 2
    assert event.payload_json["owner_user_id"] == str(owner.id)
    assert len(deliveries) == 2
    assert {delivery.owner_user_id for delivery in deliveries} == {owner.id}


def test_legacy_report_ready_without_actor_owner_fails_closed_without_deliveries(
    db_session,
):
    owner = _persist_user(db_session)
    _persist_report_smtp(db_session, owner=owner)
    _persist_report_webhook(db_session, owner=owner)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    event = emit_report_ready_event(db_session, report=report)
    legacy_payload = dict(event.payload_json)
    legacy_payload.pop("owner_user_id")
    event.payload_json = legacy_payload
    event.schema_version = 1
    event.actor_user_id = None

    with pytest.raises(
        IntegrationEventContextError,
        match="missing its immutable actor owner",
    ):
        with db_session.no_autoflush:
            report_ready_event_owner_id(db_session, event=event)

    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == event.id
            )
        )
        is None
    )
    assert db_session.scalar(select(NotificationWebhookDelivery.id)) is None


def test_legacy_report_ready_actor_must_match_persisted_report_owner(db_session):
    owner = _persist_user(db_session)
    other_user = _persist_user(db_session)
    _persist_report_smtp(db_session, owner=other_user)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    event = emit_report_ready_event(db_session, report=report)
    legacy_payload = dict(event.payload_json)
    legacy_payload.pop("owner_user_id")
    event.payload_json = legacy_payload
    event.schema_version = 1
    event.actor_user_id = other_user.id

    with pytest.raises(
        IntegrationEventContextError,
        match="does not match its source report owner",
    ):
        with db_session.no_autoflush:
            report_ready_event_owner_id(db_session, event=event)

    assert db_session.scalar(select(IntegrationDelivery.id)) is None


def test_report_ready_rejects_mismatched_source_and_snapshot_report_ids(db_session):
    owner = _persist_user(db_session)
    other_report = _persist_ready_report(db_session, owner_user_id=owner.id)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    integration_event.source_id = str(other_report.id)
    db_session.flush()

    result = route_integration_event(db_session, event_id=integration_event.id)

    assert result.status == "dead_letter"
    assert "report identifiers do not match" in (integration_event.last_error or "")
    assert db_session.scalar(select(IntegrationDelivery.id)) is None


def test_report_ready_with_ineligible_owner_waits_and_recovers(db_session):
    owner = _persist_user(db_session)
    _persist_report_smtp(db_session, owner=owner)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    owner.is_active = False
    db_session.add(owner)
    db_session.flush()

    result = route_integration_event(db_session, event_id=integration_event.id)

    assert result.status == "failed"
    assert "temporarily inactive or unapproved" in (integration_event.last_error or "")
    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == integration_event.id
            )
        )
        is None
    )

    owner.is_active = True
    integration_event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.add_all([owner, integration_event])
    db_session.flush()
    recovered = route_integration_event(db_session, event_id=integration_event.id)

    assert recovered.status == "routed"
    assert (
        db_session.scalar(
            select(IntegrationDelivery.id).where(
                IntegrationDelivery.event_id == integration_event.id
            )
        )
        is not None
    )


def test_report_ready_with_persistently_ineligible_owner_exhausts_routing_budget(
    db_session,
    monkeypatch,
):
    owner = _persist_user(db_session)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    owner.is_active = False
    db_session.add(owner)
    db_session.flush()
    monkeypatch.setattr(
        "app.services.integration_events.settings.integration_event_routing_max_attempts",
        2,
    )

    first = route_integration_event(db_session, event_id=integration_event.id)
    integration_event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.add(integration_event)
    db_session.flush()
    second = route_integration_event(db_session, event_id=integration_event.id)

    assert first.status == "failed"
    assert second.status == "dead_letter"
    assert integration_event.routing_attempt_count == 2


def test_report_ready_smtp_deactivation_after_routing_retries_then_recovers(
    db_session,
    monkeypatch,
):
    owner = _persist_user(db_session)
    smtp = _persist_report_smtp(db_session, owner=owner)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    routed = route_integration_event(db_session, event_id=integration_event.id)
    delivery = db_session.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == integration_event.id,
            IntegrationDelivery.integration_id == smtp.id,
        )
    )
    assert routed.status == "routed"
    assert delivery is not None
    owner.is_active = False
    db_session.add(owner)
    db_session.commit()
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: _FakeSMTP(sent_messages),
    )
    monkeypatch.setattr(
        "app.services.integration_processors.persist_external_side_effect_marker",
        lambda **_kwargs: True,
    )

    deferred = process_smtp_integration_delivery(
        db_session,
        delivery_id=delivery.id,
    )

    assert deferred.status == "retry_wait"
    assert deferred.reason == "smtp_owner_not_eligible"
    assert sent_messages == []

    owner.is_active = True
    delivery.not_before = datetime.now(timezone.utc)
    db_session.add_all([owner, delivery])
    db_session.commit()
    recovered = process_smtp_integration_delivery(
        db_session,
        delivery_id=delivery.id,
    )

    assert recovered.status == "succeeded", recovered.reason
    assert len(sent_messages) == 1


def test_report_ready_webhook_deactivation_after_routing_retries_then_recovers(
    db_session,
    monkeypatch,
):
    owner = _persist_user(db_session)
    _persist_report_webhook(db_session, owner=owner)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    routed = route_integration_event(db_session, event_id=integration_event.id)
    webhook_delivery = db_session.scalar(select(NotificationWebhookDelivery))
    assert routed.status == "routed"
    assert webhook_delivery is not None
    assert webhook_delivery.integration_delivery_id is not None
    generic_delivery = db_session.get(
        IntegrationDelivery,
        webhook_delivery.integration_delivery_id,
    )
    assert generic_delivery is not None
    owner.is_active = False
    db_session.add(owner)
    db_session.commit()

    deferred = process_notification_webhook_delivery(
        db_session,
        delivery_id=webhook_delivery.id,
    )

    assert deferred.result.success is False
    assert deferred.delivery.delivery_state == "pending"
    assert generic_delivery.state == "retry_wait"
    assert generic_delivery.last_error_code == "webhook_owner_not_eligible"

    owner.is_active = True
    retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    webhook_delivery.not_before = retry_at
    generic_delivery.not_before = retry_at
    db_session.add_all([owner, webhook_delivery, generic_delivery])
    db_session.commit()
    monkeypatch.setattr(
        "app.services.notification_webhook_compatibility.persist_external_side_effect_marker",
        lambda **_kwargs: True,
    )

    def _fake_send(rendered):
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=5,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=None,
            error=None,
        )

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _fake_send,
    )
    recovered = process_notification_webhook_delivery(
        db_session,
        delivery_id=webhook_delivery.id,
    )

    assert recovered.result.success is True
    assert recovered.delivery.delivery_state == "succeeded"


def test_report_ready_webhook_policy_change_after_io_stops_automatic_retry(
    db_session,
    monkeypatch,
):
    owner = _persist_user(db_session)
    _persist_report_webhook(db_session, owner=owner)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    routed = route_integration_event(db_session, event_id=integration_event.id)
    webhook_delivery = db_session.scalar(select(NotificationWebhookDelivery))
    assert routed.status == "routed"
    assert webhook_delivery is not None
    assert webhook_delivery.integration_delivery_id is not None
    generic_delivery = db_session.get(
        IntegrationDelivery,
        webhook_delivery.integration_delivery_id,
    )
    assert generic_delivery is not None
    monkeypatch.setattr(
        "app.services.notification_webhook_compatibility._external_side_effect_marker",
        lambda *_args, **_kwargs: True,
    )
    send_calls = 0

    def _policy_change_after_initial_request(_rendered):
        nonlocal send_calls
        send_calls += 1
        raise WebhookDeliveryTemporarilyIneligibleError(
            "webhook_owner_not_eligible",
            "Report owner became inactive after the initial webhook request.",
        )

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _policy_change_after_initial_request,
    )

    result = process_notification_webhook_delivery(
        db_session,
        delivery_id=webhook_delivery.id,
    )

    db_session.refresh(generic_delivery)
    assert send_calls == 1
    assert result.delivery.delivery_state == "failed"
    assert result.delivery.not_before is None
    assert generic_delivery.state == "failed"
    assert generic_delivery.not_before is None
    assert generic_delivery.last_error_retryable is False
    attempt = db_session.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == generic_delivery.id,
            IntegrationAttempt.attempt_number == 1,
        )
    )
    assert attempt is not None
    assert attempt.retryable is False
    assert attempt.response_json["delivery_outcome"] == "unknown"
    assert attempt.response_json["automatic_retry_suppressed"] is True


def test_report_ready_rejects_owner_actor_mismatch(db_session):
    owner = _persist_user(db_session)
    report = _persist_ready_report(db_session, owner_user_id=owner.id)
    integration_event = emit_report_ready_event(db_session, report=report)
    integration_event.actor_user_id = _persist_user(db_session).id
    db_session.flush()

    result = route_integration_event(db_session, event_id=integration_event.id)

    assert result.status == "dead_letter"
    assert "does not match its immutable actor" in (integration_event.last_error or "")


def test_report_artifacts_render_from_canonical_snapshot_and_escape_html(db_session):
    report = _persist_ready_report(db_session, title="Weekly <script>alert(1)</script>")
    detail = report_detail_response(db_session, report=report)

    markdown = render_report_markdown(detail)
    html = render_report_html(detail)
    pdf = render_report_pdf(detail)

    assert "# Weekly <script>alert(1)</script>" in markdown
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert pdf.startswith(b"%PDF")


def _persist_ready_report(
    db_session,
    *,
    title: str = "Weekly identity landscape",
    owner_user_id: uuid.UUID | None = None,
) -> Report:
    generated_at = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    resolved_owner_id = owner_user_id or _persist_user(db_session).id
    report = Report(
        id=uuid.uuid4(),
        owner_user_id=resolved_owner_id,
        title=title,
        report_type="weekly_landscape",
        status="ready",
        trigger_source="manual",
        generation_stage="ready",
        period_start=generated_at - timedelta(days=7),
        period_end=generated_at,
        filters_json={},
        prompt_config_json={
            "audience": "security_team",
            "objective": "Summarize identity threats.",
            "tone": "analytical",
            "detail_level": "standard",
            "use_company_context": True,
            "focus_topics": [],
            "excluded_topics": [],
        },
        sections_config_json=[
            {"key": "executive_summary", "title": "Executive Summary", "enabled": True}
        ],
        metrics_json={"feeds": {"CISA": 1}},
        coverage_json={"coverage_percent": 100.0, "warnings": []},
        summary_text="Identity abuse remains material [S1].",
        source_count=1,
        included_source_count=1,
        citation_count=1,
        estimated_input_tokens=100,
        context_window_tokens=8192,
        model_calls=2,
        generation_batches=1,
        provider="openai_compatible",
        model="local-test",
        delivery_requested=True,
        delivery_mode="summary",
        generated_at=generated_at,
    )
    db_session.add(report)
    db_session.flush()
    db_session.add(
        ReportSection(
            report_id=report.id,
            section_key="executive_summary",
            title="Executive Summary",
            position=1,
            status="ready",
            body_markdown="Identity abuse remains material [S1].",
            key_points_json=["Validate identity controls"],
            citations_json=["S1"],
        )
    )
    db_session.add(
        ReportSourceItem(
            report_id=report.id,
            item_id=None,
            citation_key="S1",
            included=True,
            rank=1,
            title_snapshot="Identity campaign expands",
            feed_name_snapshot="CISA",
            url_snapshot="https://example.com/report",
            first_seen_at_snapshot=generated_at - timedelta(days=1),
            tags_snapshot_json=["identity"],
            iocs_snapshot_json=[],
            evidence_text="[S1] Identity campaign expands",
            estimated_tokens=10,
        )
    )
    db_session.flush()
    return report


def _persist_user(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"report-notification-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="analyst",
        is_active=True,
        is_approved=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _persist_report_smtp(
    db_session,
    *,
    owner: User | None,
) -> IntegrationInstance:
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        owner_user_id=owner.id if owner is not None else None,
        system_key=f"smtp.report-test.{uuid.uuid4()}",
        name=f"Report SMTP {uuid.uuid4()}",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={
            "host": "smtp.example.com",
            "from_email": "threatlens@example.com",
            "to_emails": ["soc@example.com"],
            "event_types": ["report_ready"],
            "feed_scope": "all",
            "feed_ids": [],
        },
    )
    db_session.add(instance)
    db_session.flush()
    return instance


def _persist_report_webhook(
    db_session,
    *,
    owner: User,
) -> NotificationWebhook:
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=owner.id,
        name=f"Report webhook {uuid.uuid4()}",
        enabled=True,
        event_type="report_ready",
        url_template="https://example.com/report-hook",
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
    ensure_webhook_integration(db_session, webhook)
    return webhook


class _FakeSMTP:
    def __init__(self, sent_messages: list[EmailMessage]) -> None:
        self.sent_messages = sent_messages

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return 250, b"OK"

    def starttls(self, context=None):
        _ = context
        return 220, b"ready"

    def login(self, username, password):
        _ = username, password
        return 235, b"authenticated"

    def send_message(self, message):
        self.sent_messages.append(message)
        return {}
