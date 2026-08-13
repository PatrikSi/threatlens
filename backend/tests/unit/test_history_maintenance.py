import uuid
from datetime import datetime, timedelta, timezone

from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.integration import IntegrationInstance, IntegrationRun
from app.models.item import Item
from app.models.tag import TagFeedbackEvent
from app.models.user import User
from app.services.history_maintenance import prune_application_history


def test_application_history_retention_prunes_only_expired_terminal_rows(db_session, monkeypatch):
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=1)
    for setting_name in (
        "audit_log_retention_days",
        "ai_task_history_retention_days",
        "ai_usage_retention_days",
        "tag_feedback_retention_days",
        "integration_run_retention_days",
    ):
        monkeypatch.setattr(f"app.services.history_maintenance.settings.{setting_name}", 30)

    user = User(
        id=uuid.uuid4(),
        email="history@example.com",
        password_hash="unused",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    feed = Feed(id=uuid.uuid4(), name="History feed", url="https://history.example.com/rss")
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        url="https://history.example.com/item",
        title="History item",
        dedupe_key="history-item",
        content_hash="0" * 64,
        status="new",
    )
    integration = IntegrationInstance(
        id=uuid.uuid4(),
        name="History integration",
        integration_type="smtp",
        direction="outbound",
        enabled=False,
        config_json={},
    )
    db_session.add_all([user, feed, item, integration])
    db_session.flush()

    old_run = AITaskRun(
        id=uuid.uuid4(),
        task_type="item_enrichment",
        trigger_source="automatic",
        status="ready",
        finished_at=old,
    )
    unfinished_run = AITaskRun(
        id=uuid.uuid4(),
        task_type="item_enrichment",
        trigger_source="automatic",
        status="running",
        queued_at=old,
        finished_at=None,
    )
    records = [
        AuditLog(action="old", resource_type="test", metadata_json={}, created_at=old),
        AuditLog(action="recent", resource_type="test", metadata_json={}, created_at=recent),
        old_run,
        unfinished_run,
        AIUsageEvent(feature_type="item_enrichment", success=True, created_at=old),
        TagFeedbackEvent(
            item_id=item.id,
            user_id=user.id,
            tag_name="history",
            signal_type="read",
            signal_value=1.0,
            created_at=old,
        ),
        IntegrationRun(
            integration_id=integration.id,
            run_type="test",
            status="succeeded",
            started_at=old,
            finished_at=old,
            metadata_json={},
        ),
    ]
    db_session.add_all(records)
    db_session.commit()

    result = prune_application_history(db_session, now=now, batch_size=100)

    assert result.audit_logs_deleted == 1
    assert result.ai_task_runs_deleted == 1
    assert result.ai_usage_events_deleted == 1
    assert result.tag_feedback_events_deleted == 1
    assert result.integration_runs_deleted == 1
    assert db_session.get(AITaskRun, unfinished_run.id) is not None
    assert db_session.query(AuditLog).filter(AuditLog.action == "recent").count() == 1
