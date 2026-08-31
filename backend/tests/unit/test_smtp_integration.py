import smtplib
import socket
import ssl
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from threading import Event, Thread, enumerate as enumerate_threads
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.alert_interest import AlertInterest
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.schemas.integration import SMTPSettingsUpdate
from app.services.daily_brief_notifications import emit_daily_brief_ready_event
from app.services.integration_storage import (
    apply_smtp_settings_update,
    build_active_smtp_settings,
)
from app.services.integration_events import route_integration_event
from app.services.integration_processors import process_smtp_integration_delivery
from app.services.smtp_integration import (
    SMTP_DELIVERY_AUDIT_ACTION,
    _fenced_smtp_io_timeout_seconds,
    dispatch_smtp_notification,
    send_smtp_notification,
    smtp_notification_event_enabled,
    test_smtp_integration as run_smtp_integration_test,
)
from app.services.smtp_delivery_history import smtp_delivery_dedupe_key
from app.services.smtp_deadlines import remaining_smtp_operation_seconds
from app.services.smtp_transport import (
    SMTP_DNS_RESOLVER_CONCURRENCY,
    SMTPStartTLSNotSupportedError,
    _resolve_smtp_addresses,
)
from app.tasks.feed_tasks import (
    dispatch_daily_digest_notification_webhooks,
    dispatch_smtp_alert_match_notification,
    dispatch_smtp_feed_failing_notification,
    dispatch_smtp_new_item_notification,
    dispatch_smtp_webhook_failed_notification,
)


class FakeSMTP:
    def __init__(self, sent_messages: list[EmailMessage], refused=None):
        self.sent_messages = sent_messages
        self.refused = refused or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return (250, b"OK")

    def starttls(self, context=None):
        _ = context
        return (220, b"ready")

    def login(self, username, password):
        _ = username, password
        return (235, b"authenticated")

    def send_message(self, message):
        self.sent_messages.append(message)
        return self.refused


def _use_feed_task_db_session(monkeypatch, db_session) -> None:
    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)
    monkeypatch.setattr("app.tasks.notification_tasks.db_session", _db_session_override)


def test_dispatch_smtp_notification_sends_and_records_audit(db_session, monkeypatch):
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: FakeSMTP(sent_messages),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            username="relay-user",
            password="relay-password",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com", "soc@example.com"],
            subject_template="[ThreatLens] {{ item.title }}",
            html_template="<p>{{ feed.name }}: {{ item.summary }}</p>",
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/1",
        title="Threat report",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp",
        content_hash="a" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    result = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )
    db_session.commit()

    assert result.sent is True
    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "analyst@example.com, soc@example.com"
    assert sent_messages[0]["Subject"] == "[ThreatLens] Threat report"
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    )
    assert audit is not None
    assert audit.success is True
    assert audit.resource_id == str(instance.id)
    assert audit.actor_user_id is None
    assert audit.metadata_json["event_type"] == "rss_item_new"
    assert audit.metadata_json["recipient_count"] == 2
    assert audit.metadata_json["accepted_count"] == 2
    assert "analyst@example.com" not in str(audit.metadata_json)
    assert instance.health_status == "healthy"
    assert instance.last_success_at is not None


def test_dispatch_smtp_notification_skips_duplicate_delivery(db_session, monkeypatch):
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: FakeSMTP(sent_messages),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/1",
        title="Threat report",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-duplicate",
        content_hash="b" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    first = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )
    db_session.commit()
    second = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )

    assert first.sent is True
    assert second.skipped is True
    assert second.reason == "duplicate_delivery"
    assert len(sent_messages) == 1
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    ).all()
    assert len(audits) == 1


def test_dispatch_smtp_notification_records_failure_audit(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: (_ for _ in ()).throw(OSError("network unreachable")),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/1",
        title="Threat report",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-failure",
        content_hash="c" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    result = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )
    db_session.commit()

    assert result.failed is True
    assert result.reason == "connection_error"
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    )
    assert audit is not None
    assert audit.success is False
    assert audit.metadata_json["error_code"] == "connection_error"
    assert audit.metadata_json["recipient_count"] == 1
    assert instance.health_status == "error"
    assert instance.last_error == "SMTP connection failed."


def test_smtp_partial_acceptance_records_per_recipient_disposition(monkeypatch):
    sent_messages: list[EmailMessage] = []
    active = build_active_smtp_settings(_configured_smtp_instance())
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: FakeSMTP(
            sent_messages,
            refused={"soc@example.com": (550, b"mailbox unavailable")},
        ),
    )

    result = send_smtp_notification(active, event_type="rss_item_new")

    assert result.success is False
    assert result.delivery_outcome == "partial"
    assert result.accepted_recipients == ("analyst@example.com",)
    assert result.refused_recipients == ("soc@example.com",)
    assert result.unknown_recipients == ()


def test_smtp_timeout_after_send_starts_records_unknown_outcome(monkeypatch):
    class TimeoutSMTP(FakeSMTP):
        def send_message(self, message):
            self.sent_messages.append(message)
            raise socket.timeout("timed out after DATA")

    sent_messages: list[EmailMessage] = []
    active = build_active_smtp_settings(_configured_smtp_instance())
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: TimeoutSMTP(sent_messages),
    )

    result = send_smtp_notification(active, event_type="rss_item_new")

    assert result.success is False
    assert result.error_code == "timeout"
    assert result.delivery_outcome == "unknown"
    assert result.unknown_recipients == ("analyst@example.com", "soc@example.com")


def test_smtp_data_rejection_records_definitive_rejected_outcome(monkeypatch):
    class _DataRejectedSMTP(FakeSMTP):
        def send_message(self, message):
            self.sent_messages.append(message)
            raise smtplib.SMTPDataError(554, b"message rejected")

    active = build_active_smtp_settings(_configured_smtp_instance())
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: _DataRejectedSMTP([]),
    )

    result = send_smtp_notification(active, event_type="rss_item_new")

    assert result.success is False
    assert result.error_code == "smtp_rejected"
    assert result.delivery_outcome == "rejected"
    assert result.refused_recipients == tuple(active.to_emails)
    assert result.unknown_recipients == ()


def test_missing_starttls_is_a_precise_terminal_capability_error(monkeypatch):
    class _MissingStartTLSSMTP(FakeSMTP):
        def starttls(self, context=None):
            _ = context
            raise SMTPStartTLSNotSupportedError(
                "STARTTLS extension not supported by server."
            )

    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        security="starttls",
    )
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: _MissingStartTLSSMTP([]),
    )

    delivery_result = send_smtp_notification(active, event_type="rss_item_new")
    test_result = run_smtp_integration_test(active, recipient_email=None)

    assert delivery_result.success is False
    assert delivery_result.error_code == "starttls_not_supported"
    assert delivery_result.delivery_outcome == "not_attempted"
    assert "STARTTLS" in (delivery_result.error or "")
    assert test_result.success is False
    assert test_result.error_code == "starttls_not_supported"
    assert "STARTTLS" in (test_result.error or "")


@pytest.mark.parametrize("failure_call", [1, 2])
def test_transient_ehlo_failure_before_or_after_starttls_is_retryable(
    monkeypatch,
    failure_call,
):
    class _TransientEHLOSMTP(FakeSMTP):
        def __init__(self):
            super().__init__([])
            self.ehlo_calls = 0

        def ehlo(self):
            self.ehlo_calls += 1
            if self.ehlo_calls == failure_call:
                return (451, b"temporary directory failure")
            return super().ehlo()

    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        security="starttls",
    )
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: _TransientEHLOSMTP(),
    )

    delivery_result = send_smtp_notification(active, event_type="rss_item_new")
    test_result = run_smtp_integration_test(active, recipient_email=None)

    assert delivery_result.success is False
    assert delivery_result.error_code == "transient_smtp_error"
    assert delivery_result.delivery_outcome == "not_attempted"
    assert test_result.success is False
    assert test_result.error_code == "transient_smtp_error"


@pytest.mark.parametrize(
    "smtp_error",
    [
        smtplib.SMTPAuthenticationError(454, b"temporary auth failure"),
        smtplib.SMTPRecipientsRefused(
            {"analyst@example.com": (451, b"temporary mailbox failure")}
        ),
        smtplib.SMTPSenderRefused(
            450,
            b"temporary sender failure",
            "threatlens@example.com",
        ),
    ],
)
def test_temporary_pre_data_smtp_refusals_are_retryable(monkeypatch, smtp_error):
    class _FailingSMTP(FakeSMTP):
        def login(self, username, password):
            _ = username, password
            if isinstance(smtp_error, smtplib.SMTPAuthenticationError):
                raise smtp_error
            return super().login(username, password)

        def send_message(self, message):
            if not isinstance(smtp_error, smtplib.SMTPAuthenticationError):
                raise smtp_error
            return super().send_message(message)

    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        username="relay-user",
        password="relay-password",
    )
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: _FailingSMTP([]),
    )

    delivery_result = send_smtp_notification(active, event_type="rss_item_new")
    test_result = run_smtp_integration_test(
        active,
        recipient_email="analyst@example.com",
    )

    assert delivery_result.success is False
    assert delivery_result.error_code == "transient_smtp_error"
    assert delivery_result.delivery_outcome in {"not_attempted", "rejected"}
    assert test_result.success is False
    assert test_result.error_code == "transient_smtp_error"


@pytest.mark.parametrize(
    ("response_code", "expected_error_code"),
    [(421, "transient_smtp_error"), (554, "connect_rejected")],
)
def test_smtp_connection_banner_uses_reply_classification(
    monkeypatch,
    response_code,
    expected_error_code,
):
    def _raise_connect_error(_active):
        raise smtplib.SMTPConnectError(response_code, b"connection rejected")

    active = build_active_smtp_settings(_configured_smtp_instance())
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        _raise_connect_error,
    )

    delivery_result = send_smtp_notification(active, event_type="rss_item_new")
    test_result = run_smtp_integration_test(active, recipient_email=None)

    assert delivery_result.success is False
    assert delivery_result.error_code == expected_error_code
    assert delivery_result.delivery_outcome == "not_attempted"
    assert test_result.success is False
    assert test_result.error_code == expected_error_code


def test_smtp_tls_failure_after_send_starts_records_unknown_outcome(monkeypatch):
    class _TLSFailureSMTP(FakeSMTP):
        def send_message(self, message):
            self.sent_messages.append(message)
            raise ssl.SSLError("TLS record failed after DATA began")

        def close(self):
            return None

    sent_messages: list[EmailMessage] = []
    marker_calls: list[bool] = []
    active = build_active_smtp_settings(_configured_smtp_instance())
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _settings: _TLSFailureSMTP(sent_messages),
    )

    result = send_smtp_notification(
        active,
        event_type="rss_item_new",
        on_external_side_effect_possible=lambda: marker_calls.append(True),
    )

    assert marker_calls == [True]
    assert result.success is False
    assert result.error_code == "tls_error"
    assert result.delivery_outcome == "unknown"
    assert result.unknown_recipients == tuple(active.to_emails)


def test_fenced_smtp_timeout_is_bounded_below_database_statement_timeout(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.smtp_deadlines.get_settings",
        lambda: SimpleNamespace(database_statement_timeout_ms=9_000),
    )

    assert _fenced_smtp_io_timeout_seconds(60) == 6
    assert _fenced_smtp_io_timeout_seconds(4) == 4
    monkeypatch.setattr(
        "app.services.smtp_deadlines.get_settings",
        lambda: SimpleNamespace(database_statement_timeout_ms=1_000),
    )
    assert 0 < _fenced_smtp_io_timeout_seconds(60) < 1
    monkeypatch.setattr(
        "app.services.smtp_deadlines.get_settings",
        lambda: SimpleNamespace(database_statement_timeout_ms=10),
    )
    assert 0 < _fenced_smtp_io_timeout_seconds(60) < 0.01
    monkeypatch.setattr(
        "app.services.smtp_deadlines.get_settings",
        lambda: SimpleNamespace(database_statement_timeout_ms=1),
    )
    assert 0 < _fenced_smtp_io_timeout_seconds(60) < 0.001


def test_sub_millisecond_smtp_deadline_preserves_database_margin(monkeypatch):
    monkeypatch.setattr(
        "app.services.smtp_deadlines.time.perf_counter",
        lambda: 100.0,
    )

    remaining = remaining_smtp_operation_seconds(100.000_5)

    assert 0 < remaining < 0.001


def test_smtp_commands_share_one_decreasing_operation_deadline(monkeypatch):
    class _Clock:
        current = 100.0

        def __call__(self):
            self.current += 0.2
            return self.current

    class _Socket:
        def __init__(self):
            self.timeouts: list[float] = []

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

    class _DeadlineSMTP(FakeSMTP):
        def __init__(self, sent_messages):
            super().__init__(sent_messages)
            self.sock = _Socket()
            self.closed = False

        def close(self):
            self.closed = True

    sent_messages: list[EmailMessage] = []
    opened_with: list[float] = []
    server = _DeadlineSMTP(sent_messages)
    clock = _Clock()
    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()), timeout_seconds=5
    )

    def _open(settings):
        opened_with.append(float(settings.timeout_seconds))
        return server

    monkeypatch.setattr("app.services.smtp_integration.time.perf_counter", clock)
    monkeypatch.setattr("app.services.smtp_deadlines.time.perf_counter", clock)
    monkeypatch.setattr("app.services.smtp_integration._open_smtp", _open)

    result = send_smtp_notification(active, event_type="rss_item_new")

    assert result.success is True
    assert len(sent_messages) == 1
    assert 0 < opened_with[0] < active.timeout_seconds
    assert len(server.sock.timeouts) == 2
    assert server.sock.timeouts == sorted(server.sock.timeouts, reverse=True)
    assert all(0 < timeout < opened_with[0] for timeout in server.sock.timeouts)
    assert server.closed is True


def test_smtp_total_deadline_interrupts_one_blocking_command(monkeypatch):
    class _BlockingSMTP(FakeSMTP):
        def send_message(self, message):
            self.sent_messages.append(message)
            time.sleep(1)
            return {}

        def close(self):
            return None

    sent_messages: list[EmailMessage] = []
    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        timeout_seconds=0.1,
    )
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _settings: _BlockingSMTP(sent_messages),
    )
    started_at = time.monotonic()

    result = send_smtp_notification(active, event_type="rss_item_new")

    elapsed = time.monotonic() - started_at
    assert elapsed < 0.5
    assert result.success is False
    assert result.error_code == "timeout"
    assert result.delivery_outcome == "unknown"
    assert len(sent_messages) == 1


def test_smtp_total_deadline_interrupts_slow_banner_in_worker_thread(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _slow_server() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                for byte in b"220 slow.example ESMTP ready\r\n":
                    connection.sendall(bytes([byte]))
                    time.sleep(0.04)
        except OSError:
            pass
        finally:
            listener.close()

    server_thread = Thread(target=_slow_server, daemon=True)
    server_thread.start()
    getaddrinfo_calls = 0
    original_getaddrinfo = socket.getaddrinfo

    def _counted_getaddrinfo(*args, **kwargs):
        nonlocal getaddrinfo_calls
        getaddrinfo_calls += 1
        return original_getaddrinfo(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.smtp_transport.socket.getaddrinfo",
        _counted_getaddrinfo,
    )
    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        host="127.0.0.1",
        port=port,
        timeout_seconds=0.1,
    )
    results = []
    started_at = time.monotonic()
    worker = Thread(
        target=lambda: results.append(
            send_smtp_notification(active, event_type="rss_item_new")
        )
    )
    worker.start()
    worker.join(timeout=1)
    elapsed = time.monotonic() - started_at
    server_thread.join(timeout=1)

    assert worker.is_alive() is False
    assert elapsed < 0.5
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error_code == "timeout"
    assert results[0].delivery_outcome == "not_attempted"
    assert getaddrinfo_calls == 1


def test_smtp_test_deadline_classifies_watchdog_closed_command_as_timeout():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _stalled_command_server() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                connection.sendall(b"220 test.example ESMTP ready\r\n")
                connection.recv(4096)
                time.sleep(1)
        except OSError:
            pass
        finally:
            listener.close()

    server_thread = Thread(target=_stalled_command_server, daemon=True)
    server_thread.start()
    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        host="127.0.0.1",
        port=port,
        security="none",
        timeout_seconds=0.1,
    )
    results = []
    started_at = time.monotonic()
    worker = Thread(
        target=lambda: results.append(
            run_smtp_integration_test(active, recipient_email=None)
        )
    )
    worker.start()
    worker.join(timeout=1)
    elapsed = time.monotonic() - started_at
    server_thread.join(timeout=1.5)

    assert worker.is_alive() is False
    assert elapsed < 0.5
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error_code == "timeout"
    assert results[0].error == "SMTP test timed out after 0.1s."


def test_smtp_dns_timeout_bounds_outstanding_resolver_threads(monkeypatch):
    release_resolvers = Event()
    started_resolvers: list[bool] = []

    def _blocked_getaddrinfo(*_args, **_kwargs):
        started_resolvers.append(True)
        release_resolvers.wait(timeout=2)
        return []

    monkeypatch.setattr(
        "app.services.smtp_transport.socket.getaddrinfo",
        _blocked_getaddrinfo,
    )
    try:
        for _ in range(SMTP_DNS_RESOLVER_CONCURRENCY + 8):
            with pytest.raises(TimeoutError):
                _resolve_smtp_addresses(
                    "blocked.example",
                    25,
                    operation_deadline=time.perf_counter() + 0.01,
                )
        resolver_threads = [
            thread
            for thread in enumerate_threads()
            if thread.name == "smtp-dns-resolution" and thread.is_alive()
        ]
        assert len(started_resolvers) == SMTP_DNS_RESOLVER_CONCURRENCY
        assert len(resolver_threads) <= SMTP_DNS_RESOLVER_CONCURRENCY
    finally:
        release_resolvers.set()

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and any(
        thread.name == "smtp-dns-resolution" and thread.is_alive()
        for thread in enumerate_threads()
    ):
        time.sleep(0.01)
    assert not any(
        thread.name == "smtp-dns-resolution" and thread.is_alive()
        for thread in enumerate_threads()
    )


def test_smtp_dns_capacity_waits_for_a_healthy_resolver_slot(monkeypatch):
    def _bounded_getaddrinfo(*_args, **_kwargs):
        time.sleep(0.08)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 25),
            )
        ]

    monkeypatch.setattr(
        "app.services.smtp_transport.socket.getaddrinfo",
        _bounded_getaddrinfo,
    )
    results: list[tuple] = []
    errors: list[Exception] = []

    def _resolve() -> None:
        try:
            results.append(
                _resolve_smtp_addresses(
                    "healthy.example",
                    25,
                    operation_deadline=time.perf_counter() + 1,
                )
            )
        except Exception as exc:
            errors.append(exc)

    workers = [Thread(target=_resolve) for _ in range(5)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=1)

    assert all(worker.is_alive() is False for worker in workers)
    assert errors == []
    assert len(results) == 5


@pytest.mark.parametrize("security", ["ssl_tls", "starttls"])
def test_smtp_tls_handshake_obeys_deadline_in_worker_thread(security):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _stalled_tls_server() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                if security == "starttls":
                    connection.sendall(b"220 test.example ESMTP ready\r\n")
                    connection.recv(4096)
                    connection.sendall(b"250-test.example\r\n250 STARTTLS\r\n")
                    connection.recv(4096)
                    connection.sendall(b"220 Ready to start TLS\r\n")
                time.sleep(1)
        except OSError:
            pass
        finally:
            listener.close()

    server_thread = Thread(target=_stalled_tls_server, daemon=True)
    server_thread.start()
    active = replace(
        build_active_smtp_settings(_configured_smtp_instance()),
        host="127.0.0.1",
        port=port,
        security=security,
        timeout_seconds=0.1,
    )
    results = []
    started_at = time.monotonic()
    worker = Thread(
        target=lambda: results.append(
            send_smtp_notification(active, event_type="rss_item_new")
        )
    )
    worker.start()
    worker.join(timeout=1)
    elapsed = time.monotonic() - started_at
    server_thread.join(timeout=1.5)

    assert worker.is_alive() is False
    assert elapsed < 0.5
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error_code == "timeout"
    assert results[0].delivery_outcome == "not_attempted"


def test_dispatch_smtp_notification_records_message_build_failures(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: (_ for _ in ()).throw(
            AssertionError("SMTP should not open when message building fails")
        ),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
            subject_template="{{ item.title }}",
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/header-failure",
        title="Bad\nSubject",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-header-failure",
        content_hash="1" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    result = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )
    db_session.commit()

    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    )
    assert result.failed is True
    assert result.reason == "render_error"
    assert audit is not None
    assert audit.success is False
    assert audit.metadata_json["error_code"] == "render_error"
    assert "Subject" in audit.metadata_json["error"]
    assert instance.health_status == "error"


def test_smtp_test_rejects_invalid_message_headers_before_connect(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: (_ for _ in ()).throw(
            AssertionError("SMTP should not open when validation fails")
        ),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
            subject_template="Bad\nSubject",
        ),
    )
    active = build_active_smtp_settings(instance)

    result = run_smtp_integration_test(active, recipient_email="analyst@example.com")

    assert result.success is False
    assert result.error_code == "validation_error"
    assert result.error is not None
    assert "Subject" in result.error


@pytest.mark.parametrize(
    ("smtp_error", "expected_code"),
    [
        (
            smtplib.SMTPSenderRefused(
                550,
                b"sender rejected",
                "threatlens@example.com",
            ),
            "sender_rejected",
        ),
        (smtplib.SMTPDataError(554, b"message rejected"), "smtp_rejected"),
    ],
)
def test_smtp_test_returns_structured_sender_and_data_errors(
    monkeypatch,
    smtp_error,
    expected_code,
):
    class _FailingSMTP(FakeSMTP):
        def send_message(self, message):
            self.sent_messages.append(message)
            raise smtp_error

    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _settings: _FailingSMTP([]),
    )
    active = build_active_smtp_settings(_configured_smtp_instance())

    result = run_smtp_integration_test(
        active,
        recipient_email="analyst@example.com",
    )

    assert result.success is False
    assert result.error_code == expected_code
    assert result.server_message is not None


def test_dispatch_smtp_notification_dedupes_unreadable_secret_failures(db_session):
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            username="relay-user",
            password="relay-password",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )
    instance.secret_json = {"_threatlens_encrypted": "enc:v1:not-a-valid-token"}
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/secret-error",
        title="Secret error",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-secret-error",
        content_hash="2" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    first = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )
    db_session.commit()
    second = dispatch_smtp_notification(
        db_session, event_type="rss_item_new", feed=feed, item=item
    )

    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    ).all()
    assert first.failed is True
    assert first.reason == "secret_error"
    assert second.skipped is True
    assert second.reason == "duplicate_delivery"
    assert len(audits) == 1
    assert audits[0].metadata_json["error_code"] == "secret_error"


def test_smtp_event_enabled_respects_saved_scope_when_secret_is_unreadable(db_session):
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            username="relay-user",
            password="relay-password",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
            event_types=["rss_item_new"],
        ),
    )
    instance.secret_json = {"_threatlens_encrypted": "enc:v1:not-a-valid-token"}
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    db_session.add_all([instance, feed])
    db_session.commit()

    assert (
        smtp_notification_event_enabled(
            db_session, event_type="rss_item_new", feed=feed
        )
        is True
    )
    assert (
        smtp_notification_event_enabled(db_session, event_type="alert_match", feed=feed)
        is False
    )


def test_dispatch_smtp_new_item_notification_task_emits_and_enqueues_generic_event(
    db_session, monkeypatch
):
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: FakeSMTP(sent_messages),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/task-new",
        title="Task new item",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-task-new",
        content_hash="d" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    _use_feed_task_db_session(monkeypatch, db_session)
    queued_event_ids: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_integration_event_routing",
        lambda event_ids: queued_event_ids.extend(event_ids) or True,
    )

    result = dispatch_smtp_new_item_notification(str(item.id))

    event = db_session.scalar(
        select(IntegrationEvent).where(
            IntegrationEvent.idempotency_key == f"item:{item.id}:rss_item_new:v1"
        )
    )
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    )
    assert result["status"] == "queued"
    assert result["sent"] == 0
    assert result["enqueue_failed"] is False
    assert sent_messages == []
    assert audit is None
    assert event is not None
    assert event.source_type == "item"
    assert event.source_id == str(item.id)
    assert event.payload_json["item_id"] == str(item.id)
    assert event.payload_json["feed_id"] == str(feed.id)
    assert result["integration_event_id"] == str(event.id)
    assert queued_event_ids == [event.id]

    event.routing_state = "failed"
    event.available_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.commit()
    backoff_result = dispatch_smtp_new_item_notification(str(item.id))

    assert backoff_result["status"] == "retry_scheduled"
    assert backoff_result["reason"] == "event_backoff"
    assert queued_event_ids == [event.id]

    event.routing_state = "pending"
    event.available_at = datetime.now(timezone.utc)
    db_session.commit()
    routed = route_integration_event(db_session, event_id=event.id)
    smtp_delivery = db_session.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event.id,
            IntegrationDelivery.connector_type == "smtp",
        )
    )
    assert routed.status == "routed"
    assert smtp_delivery is not None
    monkeypatch.setattr(
        "app.services.integration_processors.persist_external_side_effect_marker",
        lambda **_kwargs: True,
    )

    processed = process_smtp_integration_delivery(
        db_session,
        delivery_id=smtp_delivery.id,
    )
    db_session.commit()

    assert processed.status == "succeeded", processed.reason
    assert len(sent_messages) == 1
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    )
    assert audit is not None
    assert audit.success is True

    duplicate_result = dispatch_smtp_new_item_notification(str(item.id))

    assert duplicate_result["status"] == "already_routed"
    assert duplicate_result["event_status"] == "already_routed"
    assert duplicate_result["reason"] is None
    assert db_session.query(IntegrationEvent).count() == 1
    assert queued_event_ids == [event.id]


def test_generic_smtp_delivery_adopts_legacy_success_receipt(
    db_session,
    monkeypatch,
):
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
            event_types=["rss_item_new"],
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Legacy receipt feed",
        url="https://example.com/legacy-receipt.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/legacy-receipt",
        title="Legacy receipt item",
        summary="Already delivered by a rolling-upgrade worker.",
        published_at=datetime.now(timezone.utc),
        dedupe_key=f"legacy-receipt:{uuid.uuid4()}",
        content_hash="e" * 64,
        status="new",
    )
    legacy_dedupe_key = smtp_delivery_dedupe_key(
        instance_id=instance.id,
        event_type="rss_item_new",
        delivery_kind="live",
        item_id=item.id,
        feed_id=feed.id,
        source_delivery_id=None,
        scope_key=None,
    )
    db_session.add_all(
        [
            instance,
            feed,
            item,
            AuditLog(
                action=SMTP_DELIVERY_AUDIT_ACTION,
                resource_type="integration_instance",
                resource_id=str(instance.id),
                success=True,
                metadata_json={
                    "dedupe_key": legacy_dedupe_key,
                    "delivery_outcome": "accepted",
                },
            ),
        ]
    )
    db_session.commit()
    _use_feed_task_db_session(monkeypatch, db_session)
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_integration_event_routing",
        lambda _event_ids: True,
    )
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: (_ for _ in ()).throw(
            AssertionError("legacy success receipt must suppress SMTP I/O")
        ),
    )

    staged = dispatch_smtp_new_item_notification(str(item.id))
    event_id = uuid.UUID(staged["integration_event_id"])
    routed = route_integration_event(db_session, event_id=event_id)
    delivery = db_session.scalar(
        select(IntegrationDelivery).where(
            IntegrationDelivery.event_id == event_id,
            IntegrationDelivery.connector_type == "smtp",
        )
    )
    assert routed.status == "routed"
    assert delivery is not None

    processed = process_smtp_integration_delivery(
        db_session,
        delivery_id=delivery.id,
    )

    assert processed.status == "succeeded"
    assert processed.reason == "legacy_delivery_already_recorded"
    assert delivery.state == "succeeded"


def test_dispatch_smtp_alert_match_notification_queues_durable_evaluation(
    db_session, monkeypatch
):
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: FakeSMTP(sent_messages),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
            event_types=["alert_match"],
            subject_template="[ThreatLens] {{ alert.primary_name }}: {{ item.title }}",
            html_template="<p>{{ alert.names }}</p>",
        ),
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://example.com/articles/task-alert",
        title="LockBit phishing expands",
        summary="Credential theft activity observed.",
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-task-alert",
        content_hash="e" * 64,
        status="content_fetched",
    )
    user = User(
        id=uuid.uuid4(),
        email="alert-owner@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    db_session.add_all([instance, feed, item, user])
    db_session.flush()
    db_session.add_all(
        [
            ItemClassification(
                item_id=item.id,
                primary_category="malware",
                confidence=0.9,
                source_hash="alert-classification",
            ),
            AlertInterest(
                id=uuid.uuid4(),
                user_id=user.id,
                name="Ransomware Watch",
                category="malware",
                keywords=["lockbit"],
                enabled=True,
            ),
        ]
    )
    db_session.commit()

    _use_feed_task_db_session(monkeypatch, db_session)
    queued_request_ids: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_alert_evaluation_requests",
        lambda request_ids: queued_request_ids.extend(request_ids) or True,
    )

    result = dispatch_smtp_alert_match_notification(str(item.id))

    evaluation = db_session.scalar(
        select(AlertEvaluationRequest).where(AlertEvaluationRequest.item_id == item.id)
    )
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)
    )
    assert result["status"] == "queued"
    assert sent_messages == []
    assert audit is None
    assert evaluation is not None
    assert evaluation.notify is True
    assert evaluation.state == "pending"
    assert result["evaluation_request_id"] == str(evaluation.id)
    assert queued_request_ids == [evaluation.id]
    assert (
        db_session.scalar(
            select(IntegrationEvent.id).where(
                IntegrationEvent.event_type == "alert_match"
            )
        )
        is None
    )

    replay = dispatch_smtp_alert_match_notification(str(item.id))

    assert replay["status"] == "in_progress"
    assert replay["evaluation_request_id"] == str(evaluation.id)
    assert queued_request_ids == [evaluation.id]


def test_dispatch_smtp_feed_failing_notification_keeps_event_when_enqueue_fails(
    db_session, monkeypatch
):
    feed = Feed(
        id=uuid.uuid4(),
        name="Failing feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        error_count=3,
        last_error="http_status:500",
    )
    db_session.add(feed)
    db_session.commit()
    _use_feed_task_db_session(monkeypatch, db_session)
    queued_event_ids: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_integration_event_routing",
        lambda event_ids: queued_event_ids.extend(event_ids) or False,
    )

    result = dispatch_smtp_feed_failing_notification(str(feed.id))

    event = db_session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.event_type == "feed_failing")
    )
    assert result["status"] == "pending"
    assert result["reason"] == "event_enqueue_failed"
    assert result["enqueue_failed"] is True
    assert event is not None
    assert event.idempotency_key.startswith(f"feed:{feed.id}:feed_failing:")
    assert event.idempotency_key.endswith(":v1")
    assert event.payload_json["feed_id"] == str(feed.id)
    assert event.payload_json["error_count"] == 3
    assert result["integration_event_id"] == str(event.id)
    assert queued_event_ids == [event.id]


def test_dispatch_smtp_webhook_failed_notification_emits_source_event(
    db_session, monkeypatch
):
    user = User(
        id=uuid.uuid4(),
        email="failed-webhook-owner@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    feed = Feed(
        id=uuid.uuid4(),
        name="Unit42",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    source_webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Primary webhook",
        event_type="rss_item_new",
        url_template="https://example.com/source",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    failed_delivery = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=source_webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        feed_id=feed.id,
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=1,
        success=False,
        status_code=500,
        duration_ms=25,
        timeout_seconds=10,
        rendered_url="https://example.com/source",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        rendered_body=None,
        response_body_preview="error",
        error="HTTP 500",
        feed_name_snapshot=feed.name,
        attempted_at=datetime.now(timezone.utc),
    )
    db_session.add_all([user, feed])
    db_session.flush()
    db_session.add(source_webhook)
    db_session.flush()
    db_session.add(failed_delivery)
    db_session.commit()
    _use_feed_task_db_session(monkeypatch, db_session)
    queued_event_ids: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.tasks.notification_tasks.enqueue_integration_event_routing",
        lambda event_ids: queued_event_ids.extend(event_ids) or True,
    )

    result = dispatch_smtp_webhook_failed_notification(str(failed_delivery.id))

    event = db_session.scalar(
        select(IntegrationEvent).where(
            IntegrationEvent.idempotency_key
            == f"webhook_delivery:{failed_delivery.id}:webhook_failed:v1"
        )
    )
    assert result["status"] == "queued"
    assert event is not None
    assert event.source_type == "notification_webhook_delivery"
    assert event.source_id == str(failed_delivery.id)
    assert event.payload_json == {
        "source_delivery_id": str(failed_delivery.id),
        "feed_id": str(feed.id),
        "owner_user_id": str(user.id),
    }
    assert queued_event_ids == [event.id]


def test_dispatch_daily_digest_notification_webhooks_emits_durable_event(
    db_session, monkeypatch
):
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(
        "app.services.smtp_integration._open_smtp",
        lambda _active: FakeSMTP(sent_messages),
    )
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
            event_types=["daily_digest"],
            subject_template="[ThreatLens] Daily digest: {{ digest.total_items }}",
            html_template="<p>{{ digest.top_titles }}</p>",
        ),
    )
    now = datetime.now(timezone.utc)
    brief = AIDailyBrief(
        id=uuid.uuid4(),
        brief_date=now.date(),
        status="ready",
        window_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        window_end=now,
        title="AI Daily Brief",
        brief_text="A generated security briefing.",
        key_points_json=["Review identity telemetry"],
        recommended_actions_json=["Validate MFA coverage"],
        top_item_ids_json=[],
        item_count=1,
        generated_at=now,
    )
    db_session.add_all([instance, brief])
    db_session.flush()
    brief_id = brief.id
    db_session.add(
        AIDailyBriefSourceItem(
            daily_brief_id=brief.id,
            item_id=None,
            included=True,
            rank=1,
            title_snapshot="Brief source item",
            feed_name_snapshot="Unit42",
        )
    )
    db_session.commit()

    @contextmanager
    def _detaching_db_session():
        yield db_session
        db_session.expunge_all()

    monkeypatch.setattr(
        "app.tasks.notification_tasks.db_session", _detaching_db_session
    )
    queued_event_ids: list[str] = []
    monkeypatch.setattr(
        "app.tasks.feed_tasks.route_integration_event.delay",
        lambda event_id: queued_event_ids.append(event_id),
    )
    monkeypatch.setattr(
        "app.tasks.notification_tasks.load_active_ai_settings",
        lambda _db: type(
            "ActiveAISettings",
            (),
            {"ai_enabled": True, "ai_configured": True, "daily_brief_enabled": True},
        )(),
    )

    result = dispatch_daily_digest_notification_webhooks()

    event = db_session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.event_type == "daily_digest")
    )
    assert result["smtp_status"] == "queued"
    assert result["smtp_sent"] == 0
    assert sent_messages == []
    assert event is not None
    assert event.source_type == "ai_daily_brief"
    assert event.payload_json["daily_brief_id"] == str(brief_id)
    assert event.payload_json["daily_brief"]["text"] == "A generated security briefing."
    assert event.routing_state == "pending"
    assert queued_event_ids == [str(event.id)]

    event.routing_state = "failed"
    event.available_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.add(event)
    db_session.commit()
    queued_event_ids.clear()
    backoff = dispatch_daily_digest_notification_webhooks()

    assert backoff["smtp_status"] == "retry_scheduled"
    assert backoff["smtp_reason"] == "event_backoff"
    assert backoff["enqueue_failed"] is False
    assert queued_event_ids == []


def test_daily_brief_notification_reconciler_does_not_requeue_routed_event(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    brief = AIDailyBrief(
        id=uuid.uuid4(),
        brief_date=now.date(),
        status="ready",
        window_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        window_end=now,
        title="AI Daily Brief",
        brief_text="A generated security briefing.",
        key_points_json=[],
        recommended_actions_json=[],
        top_item_ids_json=[],
        item_count=0,
        generated_at=now,
    )
    db_session.add(brief)
    db_session.flush()
    event = emit_daily_brief_ready_event(db_session, brief=brief)
    event.routing_state = "routed"
    db_session.commit()

    _use_feed_task_db_session(monkeypatch, db_session)
    queued_event_ids: list[str] = []
    monkeypatch.setattr(
        "app.tasks.feed_tasks.route_integration_event.delay",
        lambda event_id: queued_event_ids.append(event_id),
    )
    monkeypatch.setattr(
        "app.tasks.notification_tasks.load_active_ai_settings",
        lambda _db: type(
            "ActiveAISettings",
            (),
            {"ai_enabled": True, "ai_configured": True, "daily_brief_enabled": True},
        )(),
    )

    result = dispatch_daily_digest_notification_webhooks()

    assert result["status"] == "ok"
    assert result["smtp_status"] == "already_routed"
    assert result["enqueue_failed"] is False
    assert queued_event_ids == []


def test_daily_brief_notification_reconciler_skips_when_ai_is_disabled(
    db_session, monkeypatch
):
    _use_feed_task_db_session(monkeypatch, db_session)
    monkeypatch.setattr(
        "app.tasks.notification_tasks.load_active_ai_settings",
        lambda _db: type(
            "ActiveAISettings",
            (),
            {"ai_enabled": False, "ai_configured": False, "daily_brief_enabled": False},
        )(),
    )

    result = dispatch_daily_digest_notification_webhooks()

    assert result == {"status": "skipped", "reason": "ai_disabled"}
    assert db_session.query(IntegrationEvent).count() == 0


def _smtp_instance() -> IntegrationInstance:
    now = datetime.now(timezone.utc)
    return IntegrationInstance(
        id=uuid.uuid4(),
        system_key="smtp.default",
        name="SMTP",
        integration_type="smtp",
        direction="destination",
        enabled=False,
        schema_version=1,
        config_json={},
        secret_json=None,
        health_status="unknown",
        created_at=now,
        updated_at=now,
    )


def _configured_smtp_instance() -> IntegrationInstance:
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            enabled=True,
            host="smtp.example.com",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com", "soc@example.com"],
            security="none",
            subject_template="ThreatLens notification",
            html_template="<p>ThreatLens notification</p>",
        ),
    )
    return instance
