import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.feed import Feed
from app.models.integration import IntegrationInstance, IntegrationRun
from app.models.item import Item
from app.models.mfa import MFALoginChallenge, UserTOTPCredential
from app.models.report import Report
from app.models.tag import TagFeedbackEvent
from app.models.user import User
from app.services.history_maintenance import prune_application_history


def test_application_history_retention_prunes_only_expired_terminal_rows(
    db_session, monkeypatch
):
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=1)
    for setting_name in (
        "audit_log_retention_days",
        "ai_task_history_retention_days",
        "ai_usage_retention_days",
        "tag_feedback_retention_days",
        "integration_run_retention_days",
        "auth_session_retention_days",
    ):
        monkeypatch.setattr(
            f"app.services.history_maintenance.settings.{setting_name}", 30
        )

    user = User(
        id=uuid.uuid4(),
        email="history@example.com",
        password_hash="unused",
        role="viewer",
        is_active=True,
        is_approved=True,
    )
    feed = Feed(
        id=uuid.uuid4(), name="History feed", url="https://history.example.com/rss"
    )
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

    report = Report(
        title="Retained report",
        report_type="custom",
        status="ready",
        trigger_source="manual",
        generation_stage="complete",
        period_start=old - timedelta(days=7),
        period_end=old,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )
    db_session.add(report)
    db_session.flush()
    report_request_run = AITaskRun(
        id=uuid.uuid4(),
        task_type="report",
        trigger_source="manual",
        status="ready",
        report_id=report.id,
        finished_at=old,
    )
    db_session.add(report_request_run)
    db_session.flush()
    report.request_task_run_id = report_request_run.id

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
    expired_auth_session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash="a" * 64,
        auth_token_version=0,
        auth_method="local",
        mfa_method=None,
        authenticated_at=old,
        last_seen_at=old,
        idle_expires_at=old,
        absolute_expires_at=old,
        revoked_at=old,
        revoked_reason="test",
        created_at=old,
    )
    enrollment_auth_session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash="c" * 64,
        auth_token_version=0,
        auth_method="local",
        mfa_method=None,
        authenticated_at=recent,
        last_seen_at=recent,
        idle_expires_at=now + timedelta(hours=1),
        absolute_expires_at=now + timedelta(days=1),
        created_at=recent,
    )
    records = [
        AuditLog(action="old", resource_type="test", metadata_json={}, created_at=old),
        AuditLog(
            action="recent", resource_type="test", metadata_json={}, created_at=recent
        ),
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
        expired_auth_session,
        enrollment_auth_session,
        MFALoginChallenge(
            user_id=user.id,
            token_hash="b" * 64,
            attempt_count=0,
            max_attempts=6,
            password_authenticated_at=old,
            expires_at=old,
            consumed_at=old,
            created_at=old,
        ),
        UserTOTPCredential(
            user_id=user.id,
            secret_encrypted="enc:v1:test-pending-secret",
            enrollment_session_id=enrollment_auth_session.id,
            enrollment_auth_token_version=0,
            status="pending",
            created_at=old,
            updated_at=old,
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
    assert result.auth_sessions_deleted == 1
    assert result.mfa_challenges_deleted == 1
    assert result.pending_mfa_enrollments_deleted == 1
    assert db_session.get(AITaskRun, unfinished_run.id) is not None
    assert db_session.get(AITaskRun, report_request_run.id) is not None
    assert db_session.query(AuditLog).filter(AuditLog.action == "recent").count() == 1
    prune_entry = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "history.audit.prune")
    )
    assert prune_entry is not None
    assert prune_entry.actor_principal_type == "system"
    assert prune_entry.metadata_json["deleted_count"] == 1
