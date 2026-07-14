import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import select

from app.models.alert_interest import AlertInterest
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.integration import IntegrationInstance
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.schemas.integration import SMTPSettingsUpdate
from app.services.integration_storage import apply_smtp_settings_update
from app.services.smtp_integration import SMTP_DELIVERY_AUDIT_ACTION, dispatch_smtp_notification
from app.tasks.feed_tasks import (
    dispatch_daily_digest_notification_webhooks,
    dispatch_smtp_alert_match_notification,
    dispatch_smtp_new_item_notification,
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

    result = dispatch_smtp_notification(db_session, event_type="rss_item_new", feed=feed, item=item)
    db_session.commit()

    assert result.sent is True
    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "analyst@example.com, soc@example.com"
    assert sent_messages[0]["Subject"] == "[ThreatLens] Threat report"
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION))
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

    first = dispatch_smtp_notification(db_session, event_type="rss_item_new", feed=feed, item=item)
    db_session.commit()
    second = dispatch_smtp_notification(db_session, event_type="rss_item_new", feed=feed, item=item)

    assert first.sent is True
    assert second.skipped is True
    assert second.reason == "duplicate_delivery"
    assert len(sent_messages) == 1
    audits = db_session.scalars(select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION)).all()
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

    result = dispatch_smtp_notification(db_session, event_type="rss_item_new", feed=feed, item=item)
    db_session.commit()

    assert result.failed is True
    assert result.reason == "connection_error"
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION))
    assert audit is not None
    assert audit.success is False
    assert audit.metadata_json["error_code"] == "connection_error"
    assert audit.metadata_json["recipient_count"] == 1
    assert instance.health_status == "error"
    assert instance.last_error == "SMTP connection failed."


def test_dispatch_smtp_new_item_notification_task_sends_and_records_audit(db_session, monkeypatch):
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

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_smtp_new_item_notification(str(item.id))

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION))
    assert result["status"] == "sent"
    assert result["sent"] == 1
    assert len(sent_messages) == 1
    assert audit is not None
    assert audit.success is True
    assert audit.metadata_json["event_type"] == "rss_item_new"


def test_dispatch_smtp_alert_match_notification_uses_global_alert_context(db_session, monkeypatch):
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

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_smtp_alert_match_notification(str(item.id))

    assert result["status"] == "sent"
    assert len(sent_messages) == 1
    assert sent_messages[0]["Subject"] == "[ThreatLens] Ransomware Watch: LockBit phishing expands"
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION))
    assert audit is not None
    assert audit.metadata_json["event_type"] == "alert_match"


def test_dispatch_daily_digest_notification_webhooks_sends_smtp_digest(db_session, monkeypatch):
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
        url="https://example.com/articles/digest",
        title="Digest item",
        summary="summary",
        first_seen_at=datetime.now(timezone.utc),
        published_at=datetime.now(timezone.utc),
        dedupe_key="dedupe:item:smtp-digest",
        content_hash="f" * 64,
        status="new",
    )
    db_session.add_all([instance, feed, item])
    db_session.commit()

    @contextmanager
    def _db_session_override():
        yield db_session

    monkeypatch.setattr("app.tasks.feed_tasks.db_session", _db_session_override)

    result = dispatch_daily_digest_notification_webhooks()

    assert result["smtp_status"] == "sent"
    assert result["smtp_sent"] == 1
    assert len(sent_messages) == 1
    assert sent_messages[0]["Subject"] == "[ThreatLens] Daily digest: 1"
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == SMTP_DELIVERY_AUDIT_ACTION))
    assert audit is not None
    assert audit.metadata_json["event_type"] == "daily_digest"


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
