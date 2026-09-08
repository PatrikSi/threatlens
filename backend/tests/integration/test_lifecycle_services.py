from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, insert, select

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.alert_evaluation_request import (
    AlertEvaluationRequest,
    AlertEvaluationRequestActivity,
)
from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceActivity
from app.models.article import Article
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.feed import Feed
from app.models.item import Item
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.models.lifecycle import LifecyclePolicy, LifecyclePreview, LifecycleRun
from app.models.iam import IAMUserRoleAssignment
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.lifecycle import LifecyclePolicyDraft, LifecyclePolicyUpdateRequest
from app.services.lifecycle import (
    LifecycleConflict,
    LifecycleNotFound,
    LifecycleValidationError,
    create_lifecycle_preview,
    ensure_lifecycle_policies,
    lifecycle_overview,
    update_lifecycle_policy,
)
from app.services.lifecycle_dependencies import (
    MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
)
from app.services.lifecycle_execution import (
    dispatch_due_lifecycle_runs,
    execute_lifecycle_run,
    mark_lifecycle_run_published,
)
from app.services.lifecycle_targets import (
    TargetBatch,
    TargetPreview,
    _candidate_query,
    execute_lifecycle_target_batch,
    preview_lifecycle_target,
)


def _browser_login(
    client,
    *,
    email: str = "admin@example.com",
    password: str = "AdminPass123!",
) -> dict[str, str]:
    client.cookies.clear()
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _policy_draft(target: dict) -> dict:
    policy = target["policy"]
    return {
        "enabled": policy["enabled"],
        "retention_days": policy["retention_days"],
        "schedule_cadence": policy["schedule_cadence"],
        "schedule_hour_utc": policy["schedule_hour_utc"],
        "schedule_weekday": policy["schedule_weekday"],
        "max_records_per_run": policy["max_records_per_run"],
        "options": policy["options"],
    }


def _article_options(**overrides: bool) -> dict[str, bool]:
    options = {
        "protect_starred": True,
        "protect_notes": True,
        "protect_investigations": True,
        "protect_reports": True,
        "protect_active_alerts": True,
    }
    options.update(overrides)
    return options


def _policy_run_snapshot(policy: LifecyclePolicy) -> dict:
    return {
        "target_key": policy.target_key,
        "enabled": policy.enabled,
        "retention_days": policy.retention_days,
        "schedule_cadence": policy.schedule_cadence,
        "schedule_hour_utc": policy.schedule_hour_utc,
        "schedule_weekday": policy.schedule_weekday,
        "max_records_per_run": policy.max_records_per_run,
        "options": dict(policy.options_json or {}),
    }


def test_bootstrap_uses_runtime_defaults_once_and_staggers_schedules(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "123")
    get_settings.cache_clear()
    policies = ensure_lifecycle_policies(
        db_session,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        commit_missing=True,
    )
    by_key = {policy.target_key: policy for policy in policies}
    assert by_key["audit_logs"].retention_days == 123
    enabled_hours = {policy.schedule_hour_utc for policy in policies if policy.enabled}
    assert len(enabled_hours) > 1

    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "456")
    get_settings.cache_clear()
    policies = ensure_lifecycle_policies(db_session)
    assert {policy.target_key: policy for policy in policies}[
        "audit_logs"
    ].retention_days == 123


def test_bootstrapped_catalog_validation_uses_read_only_fast_path(db_session):
    ensure_lifecycle_policies(db_session)
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement.lower())

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        policies = ensure_lifecycle_policies(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert policies
    assert not any("for update" in statement for statement in statements)


def test_partial_policy_catalog_fails_closed_instead_of_reenabling_defaults(
    db_session,
):
    policies = ensure_lifecycle_policies(db_session)
    db_session.delete(policies[0])
    db_session.flush()
    with pytest.raises(LifecycleConflict, match="catalog is incomplete"):
        ensure_lifecycle_policies(db_session)


def test_initialized_empty_policy_catalog_fails_closed(
    db_session,
):
    policies = ensure_lifecycle_policies(db_session)
    for policy in policies:
        db_session.delete(policy)
    db_session.flush()
    with pytest.raises(LifecycleConflict, match="catalog is incomplete"):
        ensure_lifecycle_policies(db_session)


def test_disabling_article_safeguard_requires_destructive_confirmation(
    db_session,
    seed_users,
):
    policies = ensure_lifecycle_policies(db_session)
    article_policy = next(
        policy for policy in policies if policy.target_key == "article_content"
    )
    payload = LifecyclePolicyUpdateRequest(
        expected_revision=article_policy.revision,
        enabled=False,
        retention_days=article_policy.retention_days,
        schedule_cadence=article_policy.schedule_cadence,
        schedule_hour_utc=article_policy.schedule_hour_utc,
        schedule_weekday=article_policy.schedule_weekday,
        max_records_per_run=article_policy.max_records_per_run,
        options=_article_options(protect_reports=False),
    )
    with pytest.raises(LifecycleValidationError, match="PURGE"):
        update_lifecycle_policy(
            db_session,
            target_key="article_content",
            payload=payload,
            actor=seed_users["admin"],
        )


def test_destructive_policy_save_consumes_exact_actor_owned_preview(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "article_content"
    )
    draft = LifecyclePolicyDraft(
        enabled=True,
        retention_days=policy.retention_days,
        schedule_cadence=policy.schedule_cadence,
        schedule_hour_utc=policy.schedule_hour_utc,
        schedule_weekday=policy.schedule_weekday,
        max_records_per_run=policy.max_records_per_run,
        options=_article_options(),
    )
    preview = create_lifecycle_preview(
        db_session,
        target_key=policy.target_key,
        expected_revision=policy.revision,
        draft=draft,
        requested_by_user_id=seed_users["admin"].id,
        now=now,
    )
    payload = LifecyclePolicyUpdateRequest(
        **draft.model_dump(),
        expected_revision=policy.revision,
        preview_id=preview.id,
        confirmation="PURGE",
        reason="Enable lifecycle cleanup after reviewing the exact preview.",
    )
    with pytest.raises(LifecycleNotFound, match="preview not found"):
        update_lifecycle_policy(
            db_session,
            target_key=policy.target_key,
            payload=payload,
            actor=seed_users["analyst"],
            now=now,
        )
    updated = update_lifecycle_policy(
        db_session,
        target_key=policy.target_key,
        payload=payload,
        actor=seed_users["admin"],
        now=now,
    )
    assert updated.enabled is True
    assert updated.updated_by == seed_users["admin"].email
    db_session.refresh(policy)
    assert updated.configuration_updated_at == policy.configuration_updated_at
    stored_preview = db_session.get(LifecyclePreview, preview.id)
    assert stored_preview is not None
    assert stored_preview.used_at == now


def test_destructive_policy_save_rejects_expired_preview(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "article_content"
    )
    draft = LifecyclePolicyDraft(
        enabled=True,
        retention_days=policy.retention_days,
        schedule_cadence=policy.schedule_cadence,
        schedule_hour_utc=policy.schedule_hour_utc,
        schedule_weekday=policy.schedule_weekday,
        max_records_per_run=policy.max_records_per_run,
        options=_article_options(),
    )
    preview = create_lifecycle_preview(
        db_session,
        target_key=policy.target_key,
        expected_revision=policy.revision,
        draft=draft,
        requested_by_user_id=seed_users["admin"].id,
        now=now - timedelta(minutes=20),
    )
    payload = LifecyclePolicyUpdateRequest(
        **draft.model_dump(),
        expected_revision=policy.revision,
        preview_id=preview.id,
        confirmation="PURGE",
        reason="Enable lifecycle cleanup after reviewing the exact preview.",
    )
    with pytest.raises(LifecycleConflict, match="preview expired"):
        update_lifecycle_policy(
            db_session,
            target_key=policy.target_key,
            payload=payload,
            actor=seed_users["admin"],
            now=now,
        )


def test_identical_preview_request_reuses_current_actor_preview(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "audit_logs"
    )
    draft = LifecyclePolicyDraft(
        enabled=policy.enabled,
        retention_days=policy.retention_days,
        schedule_cadence=policy.schedule_cadence,
        schedule_hour_utc=policy.schedule_hour_utc,
        schedule_weekday=policy.schedule_weekday,
        max_records_per_run=policy.max_records_per_run,
        options={},
    )

    first = create_lifecycle_preview(
        db_session,
        target_key=policy.target_key,
        expected_revision=policy.revision,
        draft=draft,
        requested_by_user_id=seed_users["admin"].id,
        now=now,
    )
    second = create_lifecycle_preview(
        db_session,
        target_key=policy.target_key,
        expected_revision=policy.revision,
        draft=draft,
        requested_by_user_id=seed_users["admin"].id,
        now=now + timedelta(seconds=1),
    )

    assert second.id == first.id
    assert first.observed_at == now
    assert second.observed_at == now + timedelta(seconds=1)
    overview = lifecycle_overview(
        db_session,
        requested_by_user_id=seed_users["admin"].id,
        now=now + timedelta(seconds=2),
    )
    overview_target = next(
        target for target in overview.targets if target.key == policy.target_key
    )
    assert overview_target.latest_preview is not None
    assert overview_target.latest_preview.observed_at == now + timedelta(seconds=2)
    assert db_session.scalar(
        select(func.count()).select_from(LifecyclePreview).where(
            LifecyclePreview.target_key == policy.target_key,
            LifecyclePreview.requested_by_user_id == seed_users["admin"].id,
            LifecyclePreview.used_at.is_(None),
        )
    ) == 1


def test_policy_update_rejects_normalized_noop_without_revision_change(
    db_session,
    seed_users,
):
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "audit_logs"
    )
    original_revision = policy.revision
    payload = LifecyclePolicyUpdateRequest(
        expected_revision=policy.revision,
        enabled=policy.enabled,
        retention_days=policy.retention_days,
        schedule_cadence=policy.schedule_cadence,
        schedule_hour_utc=policy.schedule_hour_utc,
        schedule_weekday=policy.schedule_weekday,
        max_records_per_run=policy.max_records_per_run,
        options={},
    )

    with pytest.raises(LifecycleValidationError, match="No lifecycle policy changes"):
        update_lifecycle_policy(
            db_session,
            target_key=policy.target_key,
            payload=payload,
            actor=seed_users["admin"],
        )

    db_session.refresh(policy)
    assert policy.revision == original_revision


def test_enabled_policy_record_cap_expansion_requires_destructive_preview(
    db_session,
    seed_users,
):
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "audit_logs"
    )
    assert policy.enabled is True
    payload = LifecyclePolicyUpdateRequest(
        expected_revision=policy.revision,
        enabled=True,
        retention_days=policy.retention_days,
        schedule_cadence=policy.schedule_cadence,
        schedule_hour_utc=policy.schedule_hour_utc,
        schedule_weekday=policy.schedule_weekday,
        max_records_per_run=policy.max_records_per_run + 100,
        options={},
        confirmation="PURGE",
        reason="Increase the bounded cleanup capacity after operator review.",
    )
    with pytest.raises(LifecycleValidationError, match="fresh lifecycle preview"):
        update_lifecycle_policy(
            db_session,
            target_key=policy.target_key,
            payload=payload,
            actor=seed_users["admin"],
        )


def test_article_payload_purge_preserves_source_and_respects_active_work(
    db_session,
):
    now = datetime.now(timezone.utc)
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "article_content"
    )
    feed = Feed(name="Lifecycle feed", url=f"https://example.com/{uuid.uuid4()}")
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        url="https://example.com/article",
        title="Retained source",
        published_at=now - timedelta(days=90),
        first_seen_at=now - timedelta(days=89),
        dedupe_key=uuid.uuid4().hex,
        content_hash="a" * 64,
    )
    db_session.add(item)
    db_session.flush()
    article = Article(
        item_id=item.id,
        final_url=item.url,
        retrieved_at=now - timedelta(days=88),
        http_status=200,
        title_extracted="Extracted title",
        text="sensitive payload",
        extraction_method="readability",
        language="en",
        word_count=2,
    )
    active_task = AITaskRun(
        task_type="enrichment",
        trigger_source="manual",
        status="queued",
        item_id=item.id,
        data_access_scope="governed",
        queued_at=now,
    )
    db_session.add_all((article, active_task))
    db_session.flush()
    preview = preview_lifecycle_target(
        db_session,
        target_key="article_content",
        cutoff=now - timedelta(days=30),
        options=_article_options(),
        now=now,
    )
    assert preview.eligible_count == 0
    assert preview.protected_counts["active_ai_work"] == 1

    db_session.delete(active_task)
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json={"options": _article_options()},
        cutoff_at=now - timedelta(days=30),
        scheduled_for=now,
        max_records=100,
        queued_at=now,
    )
    db_session.add(run)
    db_session.flush()
    result = execute_lifecycle_target_batch(
        db_session,
        target_key="article_content",
        cutoff=run.cutoff_at,
        batch_size=100,
        run_id=run.id,
        options=_article_options(),
        now=now,
    )
    db_session.flush()
    db_session.refresh(article)
    db_session.refresh(item)
    assert result.affected_count == 1
    assert result.affected_bytes and result.affected_bytes > 0
    assert article.text is None
    assert article.extraction_method == "retention_purged"
    assert article.content_purged_at is not None
    assert article.content_purge_run_id == run.id
    assert item.dedupe_key


def test_idle_expired_session_uses_earliest_terminal_expiry(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    session = AuthSession(
        user_id=seed_users["admin"].id,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        auth_method="local",
        authenticated_at=now - timedelta(days=70),
        last_seen_at=now - timedelta(days=70),
        idle_expires_at=now - timedelta(days=60),
        absolute_expires_at=now + timedelta(days=1),
        created_at=now - timedelta(days=70),
    )
    db_session.add(session)
    db_session.flush()
    preview = preview_lifecycle_target(
        db_session,
        target_key="inactive_auth_sessions",
        cutoff=now - timedelta(days=30),
        now=now,
    )
    assert preview.eligible_count == 1
    result = execute_lifecycle_target_batch(
        db_session,
        target_key="inactive_auth_sessions",
        cutoff=now - timedelta(days=30),
        batch_size=100,
        run_id=uuid.uuid4(),
        now=now,
    )
    assert result.affected_count == 1


def test_ai_task_query_keeps_report_and_unresolved_receipt_pins():
    now = datetime.now(timezone.utc)
    query = _candidate_query(
        "ai_task_history",
        cutoff=now - timedelta(days=30),
        now=now,
    )
    sql = str(query.predicate.compile(compile_kwargs={"literal_binds": True}))
    assert "reports" in sql
    assert "ai_provider_attempt_receipts" in sql
    assert "reconciliation_action IS NULL" in sql


def test_integration_delivery_target_includes_eligible_legacy_webhook_history(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "integration_delivery_history"
    )
    webhook = NotificationWebhook(
        user_id=seed_users["admin"].id,
        name="Legacy lifecycle webhook",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
    )
    db_session.add(webhook)
    db_session.flush()
    delivery_id = uuid.uuid4()
    delivery = NotificationWebhookDelivery(
        id=delivery_id,
        webhook_id=webhook.id,
        user_id=seed_users["admin"].id,
        delivery_state="failed",
        success=False,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        error="historical failure",
        attempted_at=now - timedelta(days=120),
    )
    db_session.add(delivery)
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json={"options": {}},
        cutoff_at=now - timedelta(days=90),
        scheduled_for=now,
        max_records=100,
        queued_at=now,
    )
    db_session.add(run)
    db_session.flush()

    preview = preview_lifecycle_target(
        db_session,
        target_key=policy.target_key,
        cutoff=run.cutoff_at,
        now=now,
    )
    assert preview.eligible_count == 1

    result = execute_lifecycle_target_batch(
        db_session,
        target_key=policy.target_key,
        cutoff=run.cutoff_at,
        batch_size=100,
        run_id=run.id,
        now=now,
    )
    assert result.affected_count == 1
    assert result.details["legacy_webhook_deliveries_deleted"] == 1
    db_session.expire_all()
    assert db_session.get(NotificationWebhookDelivery, delivery_id) is None


def test_integration_delivery_active_retry_protects_terminal_source(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=120)
    instance = IntegrationInstance(
        owner_user_id=seed_users["admin"].id,
        name="Lifecycle retry fence",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(instance)
    db_session.flush()
    source = IntegrationDelivery(
        integration_id=instance.id,
        owner_user_id=seed_users["admin"].id,
        connector_type="smtp",
        event_type="rss_item_new",
        delivery_kind="live",
        state="failed",
        idempotency_key=f"lifecycle-source:{uuid.uuid4()}",
        payload_json={},
        completed_at=old,
        metrics_aggregated_at=old,
        created_at=old,
        updated_at=old,
    )
    db_session.add(source)
    db_session.flush()
    retry = IntegrationDelivery(
        integration_id=instance.id,
        owner_user_id=seed_users["admin"].id,
        source_delivery_id=source.id,
        connector_type="smtp",
        event_type="rss_item_new",
        delivery_kind="retry",
        state="pending",
        idempotency_key=f"lifecycle-retry:{uuid.uuid4()}",
        payload_json={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(retry)
    db_session.flush()

    preview = preview_lifecycle_target(
        db_session,
        target_key="integration_delivery_history",
        cutoff=now - timedelta(days=90),
        now=now,
    )
    result = execute_lifecycle_target_batch(
        db_session,
        target_key="integration_delivery_history",
        cutoff=now - timedelta(days=90),
        batch_size=100,
        run_id=uuid.uuid4(),
        now=now,
    )

    assert preview.eligible_count == 0
    assert result.affected_count == 0
    assert db_session.get(IntegrationDelivery, source.id) is not None
    assert db_session.get(IntegrationDelivery, retry.id) is not None


def test_stale_publication_reuses_durable_task_id_instead_of_starving_queue(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    policies = ensure_lifecycle_policies(db_session)
    policy = next(policy for policy in policies if policy.target_key == "audit_logs")
    task_id = f"durable-{uuid.uuid4()}"
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json={"options": {}},
        reason="Queue-delay regression test.",
        cutoff_at=now - timedelta(days=90),
        scheduled_for=now - timedelta(minutes=10),
        max_records=100,
        celery_task_id=task_id,
        queued_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )
    db_session.add(run)
    db_session.commit()

    queued = dispatch_due_lifecycle_runs(db_session, now=now)
    assert run.id in queued
    db_session.refresh(run)
    assert run.celery_task_id == task_id
    durable_task_id, newly_reserved = mark_lifecycle_run_published(
        db_session,
        run_id=run.id,
        celery_task_id=f"replacement-{uuid.uuid4()}",
    )
    assert durable_task_id == task_id
    assert newly_reserved is False


def test_closed_alert_lifecycle_drains_large_activity_bundle_within_budget(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=120)
    occurrence_id = uuid.uuid4()
    occurrence = AlertOccurrence(
        id=occurrence_id,
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=seed_users["admin"].id,
        item_id_snapshot=uuid.uuid4(),
        rule_revision=1,
        item_content_hash="c" * 64,
        alert_name_snapshot="Lifecycle activity bound",
        alert_category_snapshot="test",
        alert_keywords_snapshot=[],
        matched_keywords=[],
        source_snapshot_json={},
        severity_snapshot="high",
        lifecycle_state="closed",
        closure_disposition="true_positive",
        closed_at=old,
        metrics_aggregated_at=old + timedelta(minutes=1),
        created_at=old,
        updated_at=old,
    )
    unaggregated_id = uuid.uuid4()
    unaggregated = AlertOccurrence(
        id=unaggregated_id,
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=seed_users["admin"].id,
        item_id_snapshot=uuid.uuid4(),
        rule_revision=1,
        item_content_hash="u" * 64,
        alert_name_snapshot="Unaggregated lifecycle evidence",
        alert_category_snapshot="test",
        alert_keywords_snapshot=[],
        matched_keywords=[],
        source_snapshot_json={},
        severity_snapshot="high",
        lifecycle_state="closed",
        closure_disposition="true_positive",
        closed_at=old,
        metrics_aggregated_at=None,
        created_at=old,
        updated_at=old,
    )
    activities = [
        AlertOccurrenceActivity(
            occurrence_id=occurrence_id,
            actor_user_id=seed_users["admin"].id,
            action="status_changed",
            details_json={"sequence": index},
            created_at=old + timedelta(seconds=index),
        )
        for index in range(150)
    ]
    db_session.add_all((occurrence, unaggregated, *activities))
    db_session.flush()
    cutoff = now - timedelta(days=90)
    preview = preview_lifecycle_target(
        db_session,
        target_key="closed_alert_history",
        cutoff=cutoff,
        now=now,
    )
    assert preview.eligible_count == 151
    assert preview.protected_count == 1
    assert preview.protected_counts == {"awaiting_metric_rollup": 1}

    first = execute_lifecycle_target_batch(
        db_session,
        target_key="closed_alert_history",
        cutoff=cutoff,
        batch_size=100,
        run_id=uuid.uuid4(),
        now=now,
    )
    assert first.affected_count == 100
    assert first.details["alert_occurrence_activities_deleted"] == 100
    assert first.details["alert_occurrences_deleted"] == 0
    assert db_session.get(AlertOccurrence, occurrence_id) is not None

    second = execute_lifecycle_target_batch(
        db_session,
        target_key="closed_alert_history",
        cutoff=cutoff,
        batch_size=100,
        run_id=uuid.uuid4(),
        now=now,
    )
    assert second.affected_count == 51
    assert second.details["alert_occurrence_activities_deleted"] == 50
    assert second.details["alert_occurrences_deleted"] == 1
    db_session.expire_all()
    assert db_session.get(AlertOccurrence, occurrence_id) is None
    assert db_session.get(AlertOccurrence, unaggregated_id) is not None


def test_wide_alert_evaluation_is_protected_without_starving_newer_candidates(
    db_session,
):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=120)
    wide = AlertEvaluationRequest(
        item_id=uuid.uuid4(),
        item_content_hash="w" * 64,
        state="succeeded",
        source="live",
        active_source="live",
        completed_at=old,
        accepted_at=old,
        available_at=old,
        created_at=old,
        updated_at=old,
    )
    ordinary = AlertEvaluationRequest(
        item_id=uuid.uuid4(),
        item_content_hash="o" * 64,
        state="succeeded",
        source="live",
        active_source="live",
        completed_at=old + timedelta(minutes=1),
        accepted_at=old,
        available_at=old,
        created_at=old,
        updated_at=old,
    )
    db_session.add_all((wide, ordinary))
    db_session.flush()
    db_session.execute(
        insert(AlertEvaluationRequestActivity),
        [
            {
                "id": uuid.uuid4(),
                "request_id": wide.id,
                "action": "historical_step",
                "details_json": {},
                "created_at": old,
            }
            for _ in range(MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH + 1)
        ],
    )
    db_session.flush()
    cutoff = now - timedelta(days=30)
    wide_id = wide.id
    ordinary_id = ordinary.id

    preview = preview_lifecycle_target(
        db_session,
        target_key="alert_evaluation_history",
        cutoff=cutoff,
        now=now,
    )
    result = execute_lifecycle_target_batch(
        db_session,
        target_key="alert_evaluation_history",
        cutoff=cutoff,
        batch_size=100,
        run_id=uuid.uuid4(),
        now=now,
    )

    assert preview.eligible_count == 1
    assert preview.protected_counts == {"dependent_row_limit": 1}
    assert result.affected_count == 1
    db_session.expire_all()
    assert db_session.get(AlertEvaluationRequest, wide_id) is not None
    assert db_session.get(AlertEvaluationRequest, ordinary_id) is None
    assert db_session.scalar(
        select(func.count())
        .select_from(AlertEvaluationRequestActivity)
        .where(AlertEvaluationRequestActivity.request_id == wide_id)
    ) == MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH + 1


def test_alert_activity_lifecycle_keeps_created_baseline_on_active_alert(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=400)
    occurrence = AlertOccurrence(
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=seed_users["admin"].id,
        item_id_snapshot=uuid.uuid4(),
        rule_revision=1,
        item_content_hash="a" * 64,
        alert_name_snapshot="Active alert timeline",
        alert_category_snapshot="test",
        alert_keywords_snapshot=[],
        matched_keywords=[],
        source_snapshot_json={},
        severity_snapshot="medium",
        lifecycle_state="new",
        created_at=old,
        updated_at=old,
    )
    db_session.add(occurrence)
    db_session.flush()
    baseline = AlertOccurrenceActivity(
        occurrence_id=occurrence.id,
        actor_user_id=seed_users["admin"].id,
        action="created",
        details_json={},
        created_at=old,
    )
    historical = AlertOccurrenceActivity(
        occurrence_id=occurrence.id,
        actor_user_id=seed_users["admin"].id,
        action="status_changed",
        details_json={},
        created_at=old + timedelta(minutes=1),
    )
    baseline_id = uuid.uuid4()
    historical_id = uuid.uuid4()
    baseline.id = baseline_id
    historical.id = historical_id
    db_session.add_all((baseline, historical))
    db_session.flush()
    cutoff = now - timedelta(days=365)

    preview = preview_lifecycle_target(
        db_session,
        target_key="alert_activity_history",
        cutoff=cutoff,
        now=now,
    )
    assert preview.eligible_count == 1
    result = execute_lifecycle_target_batch(
        db_session,
        target_key="alert_activity_history",
        cutoff=cutoff,
        batch_size=100,
        run_id=uuid.uuid4(),
        now=now,
    )
    assert result.affected_count == 1
    db_session.expire_all()
    assert db_session.get(AlertOccurrenceActivity, baseline_id) is not None
    assert db_session.get(AlertOccurrenceActivity, historical_id) is None


def test_worker_fails_closed_when_catalog_loses_a_policy(
    db_session,
):
    now = datetime.now(timezone.utc)
    policies = ensure_lifecycle_policies(db_session)
    policy = next(policy for policy in policies if policy.target_key == "audit_logs")
    missing = next(
        policy for policy in policies if policy.target_key == "article_content"
    )
    candidate = AuditLog(
        action="lifecycle.catalog.fence.fixture",
        resource_type="test_fixture",
        success=True,
        metadata_json={},
        created_at=now - timedelta(days=policy.retention_days + 1),
    )
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json={
            "target_key": policy.target_key,
            "enabled": policy.enabled,
            "retention_days": policy.retention_days,
            "schedule_cadence": policy.schedule_cadence,
            "schedule_hour_utc": policy.schedule_hour_utc,
            "schedule_weekday": policy.schedule_weekday,
            "max_records_per_run": policy.max_records_per_run,
            "options": {},
        },
        cutoff_at=now - timedelta(days=policy.retention_days),
        scheduled_for=now,
        max_records=policy.max_records_per_run,
        queued_at=now,
    )
    db_session.add_all((candidate, run))
    db_session.delete(missing)
    db_session.commit()

    result = execute_lifecycle_run(db_session, run_id=run.id, now=now)

    db_session.refresh(run)
    assert result["status"] == "ignored"
    assert run.status == "failed"
    assert run.stop_reason == "invalid_catalog"
    assert run.affected_count == 0
    assert db_session.get(AuditLog, candidate.id) is not None


def test_dispatcher_terminalizes_all_active_runs_when_catalog_is_incomplete(
    db_session,
):
    now = datetime.now(timezone.utc)
    policies = {policy.target_key: policy for policy in ensure_lifecycle_policies(db_session)}
    queued_policy = policies["audit_logs"]
    running_policy = policies["system_health_samples"]
    queued = LifecycleRun(
        target_key=queued_policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=queued_policy.revision,
        policy_snapshot_json=_policy_run_snapshot(queued_policy),
        cutoff_at=now - timedelta(days=queued_policy.retention_days),
        scheduled_for=now,
        max_records=queued_policy.max_records_per_run,
        queued_at=now,
    )
    running = LifecycleRun(
        target_key=running_policy.target_key,
        trigger_source="scheduled",
        status="running",
        policy_revision=running_policy.revision,
        policy_snapshot_json=_policy_run_snapshot(running_policy),
        cutoff_at=now - timedelta(days=running_policy.retention_days),
        scheduled_for=now,
        max_records=running_policy.max_records_per_run,
        queued_at=now,
        started_at=now,
        heartbeat_at=now - timedelta(minutes=10),
        lease_token=uuid.uuid4().hex,
        lease_expires_at=now - timedelta(minutes=5),
    )
    db_session.add_all((queued, running))
    db_session.delete(policies["article_content"])
    db_session.commit()

    assert dispatch_due_lifecycle_runs(db_session, now=now) == []

    db_session.refresh(queued)
    db_session.refresh(running)
    for fenced in (queued, running):
        assert fenced.status == "failed"
        assert fenced.stop_reason == "invalid_catalog"
        assert fenced.error_code == "invalid_catalog"
        assert fenced.finished_at == now
        assert fenced.lease_token is None
        assert fenced.lease_expires_at is None
        assert fenced.details_json["catalog_integrity_failure"] == "lifecycle_conflict"


def test_no_progress_retries_then_finishes_with_truthful_partial_reason(
    db_session,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    policy = next(
        policy
        for policy in ensure_lifecycle_policies(db_session)
        if policy.target_key == "audit_logs"
    )
    run = LifecycleRun(
        target_key=policy.target_key,
        trigger_source="scheduled",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json=_policy_run_snapshot(policy),
        cutoff_at=now - timedelta(days=policy.retention_days),
        scheduled_for=now,
        max_records=policy.max_records_per_run,
        queued_at=now,
    )
    db_session.add(run)
    db_session.commit()
    run_id = run.id
    monkeypatch.setattr(
        "app.services.lifecycle_execution.execute_lifecycle_target_batch",
        lambda *args, **kwargs: TargetBatch(evaluated_count=1, affected_count=0),
    )
    monkeypatch.setattr(
        "app.services.lifecycle_execution.preview_lifecycle_target",
        lambda *args, **kwargs: TargetPreview(
            eligible_count=1,
            protected_count=0,
        ),
    )

    first = execute_lifecycle_run(db_session, run_id=run_id, now=now)
    second = execute_lifecycle_run(db_session, run_id=run_id, now=now)
    third = execute_lifecycle_run(db_session, run_id=run_id, now=now)
    stored = db_session.get(LifecycleRun, run_id)

    assert first["status"] == "queued"
    assert second["status"] == "queued"
    assert third["status"] == "partial"
    assert third["stop_reason"] == "candidates_locked_or_protected"
    assert third["remaining_count"] == 1
    assert stored is not None
    assert stored.details_json["no_progress_batch_count"] == 3


def test_lifecycle_routes_enforce_scope_browser_auth_idempotency_and_audit(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    denied_read = client.get(
        "/operations/lifecycle",
        headers=auth_headers["analyst"],
    )
    assert denied_read.status_code == 403

    overview = client.get(
        "/operations/lifecycle",
        headers=auth_headers["admin"],
    )
    assert overview.status_code == 200, overview.text
    targets = overview.json()["targets"]
    assert db_session.scalar(select(func.count()).select_from(LifecyclePolicy)) == len(
        targets
    )
    audit_target = next(target for target in targets if target["key"] == "audit_logs")
    draft = _policy_draft(audit_target)
    update_payload = {
        **draft,
        "expected_revision": audit_target["policy"]["revision"],
        "schedule_hour_utc": (draft["schedule_hour_utc"] + 1) % 24,
    }

    token_attempt = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers={
            **auth_headers["admin"],
            "Idempotency-Key": "lifecycle-policy-token-attempt-1",
        },
        json=update_payload,
    )
    assert token_attempt.status_code == 403
    assert token_attempt.json()["error"]["code"] == "browser_session_required"

    browser = _browser_login(client)
    headers = {**browser, "Idempotency-Key": "lifecycle-policy-update-1"}
    changed = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers=headers,
        json=update_payload,
    )
    assert changed.status_code == 200, changed.text
    replay = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers=headers,
        json=update_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == changed.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers=headers,
        json={**update_payload, "schedule_hour_utc": draft["schedule_hour_utc"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "governance_idempotency_conflict"

    session_token = client.cookies.get(get_settings().auth_cookie_name)
    assert session_token
    session = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash
            == hashlib.sha256(session_token.encode()).hexdigest()
        )
    )
    assert session is not None
    session.authenticated_at = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().auth_recent_auth_seconds + 1
    )
    db_session.add(session)
    db_session.commit()
    stale_attempt = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers={
            **browser,
            "Idempotency-Key": "lifecycle-policy-stale-auth-1",
        },
        json={
            **update_payload,
            "expected_revision": changed.json()["revision"],
            "schedule_hour_utc": (changed.json()["schedule_hour_utc"] + 1) % 24,
        },
    )
    assert stale_attempt.status_code == 403
    assert stale_attempt.json()["error"]["code"] == "local_reauthentication_required"

    audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "lifecycle.policy.update",
                AuditLog.actor_user_id == seed_users["admin"].id,
            )
        ).all()
    )
    assert any(entry.success for entry in audits)
    assert any(not entry.success for entry in audits)
    success_audit = next(entry for entry in audits if entry.success)
    before = success_audit.metadata_json["before"]
    after = success_audit.metadata_json["after"]
    expected_policy_fields = {
        "target_key",
        "enabled",
        "retention_days",
        "schedule_cadence",
        "schedule_hour_utc",
        "schedule_weekday",
        "max_records_per_run",
        "options",
        "revision",
        "next_run_at",
        "configuration_updated_at",
    }
    assert set(before) == expected_policy_fields
    assert set(after) == expected_policy_fields
    assert before["revision"] + 1 == after["revision"]
    assert before["schedule_hour_utc"] != after["schedule_hour_utc"]


def test_lifecycle_routes_distinguish_revision_preview_conflicts_and_noop(
    client,
    db_session,
    seed_users,
):
    browser = _browser_login(client)
    overview = client.get("/operations/lifecycle")
    assert overview.status_code == 200, overview.text
    target = next(
        item for item in overview.json()["targets"] if item["key"] == "audit_logs"
    )
    draft = _policy_draft(target)

    noop = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers={**browser, "Idempotency-Key": "lifecycle-policy-noop-1"},
        json={**draft, "expected_revision": target["policy"]["revision"]},
    )
    assert noop.status_code == 400, noop.text
    assert noop.json()["error"]["code"] == "lifecycle_invalid"
    unchanged = client.get("/operations/lifecycle")
    unchanged_target = next(
        item
        for item in unchanged.json()["targets"]
        if item["key"] == "audit_logs"
    )
    assert unchanged_target["policy"]["revision"] == target["policy"]["revision"]

    revision_conflict = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers={**browser, "Idempotency-Key": "lifecycle-policy-revision-1"},
        json={**draft, "expected_revision": target["policy"]["revision"] + 1},
    )
    assert revision_conflict.status_code == 409, revision_conflict.text
    assert (
        revision_conflict.json()["error"]["code"]
        == "lifecycle_revision_conflict"
    )

    preview = client.post(
        "/operations/lifecycle/preview",
        headers=browser,
        json={
            "target_key": "audit_logs",
            "expected_revision": target["policy"]["revision"],
            "draft": draft,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["observed_at"]
    stored_preview = db_session.get(
        LifecyclePreview,
        uuid.UUID(preview.json()["id"]),
    )
    assert stored_preview is not None
    stored_preview.used_at = datetime.now(timezone.utc)
    db_session.add(stored_preview)
    db_session.commit()

    preview_conflict = client.post(
        "/operations/lifecycle/runs",
        headers={**browser, "Idempotency-Key": "lifecycle-preview-used-1"},
        json={
            "target_key": "audit_logs",
            "expected_revision": target["policy"]["revision"],
            "preview_id": preview.json()["id"],
            "confirmation": "PURGE",
            "reason": "Validate stable preview conflict classification.",
        },
    )
    assert preview_conflict.status_code == 409, preview_conflict.text
    assert (
        preview_conflict.json()["error"]["code"] == "lifecycle_preview_conflict"
    )

def test_lifecycle_manual_run_requires_owned_fresh_preview_and_replays_cancel(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.routes.lifecycle._publish_run_best_effort",
        lambda _db, _run_id: None,
    )
    browser = _browser_login(client)
    overview = client.get("/operations/lifecycle")
    assert overview.status_code == 200, overview.text
    audit_target = next(
        target for target in overview.json()["targets"] if target["key"] == "audit_logs"
    )
    draft = _policy_draft(audit_target)
    preview = client.post(
        "/operations/lifecycle/preview",
        headers=browser,
        json={
            "target_key": "audit_logs",
            "expected_revision": audit_target["policy"]["revision"],
            "draft": draft,
        },
    )
    assert preview.status_code == 200, preview.text
    run_payload = {
        "target_key": "audit_logs",
        "expected_revision": audit_target["policy"]["revision"],
        "preview_id": preview.json()["id"],
        "confirmation": "PURGE",
        "reason": "Validate lifecycle manual execution and replay handling.",
    }
    run_headers = {**browser, "Idempotency-Key": "lifecycle-run-create-1"}
    created = client.post(
        "/operations/lifecycle/runs",
        headers=run_headers,
        json=run_payload,
    )
    assert created.status_code == 202, created.text
    replay = client.post(
        "/operations/lifecycle/runs",
        headers=run_headers,
        json=run_payload,
    )
    assert replay.status_code == 202, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == created.json()
    run_request_audit = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "lifecycle.run.request",
            AuditLog.resource_id == created.json()["id"],
            AuditLog.success.is_(True),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert run_request_audit is not None
    assert run_request_audit.metadata_json["preview"]["preview_id"] == preview.json()[
        "id"
    ]
    assert run_request_audit.metadata_json["preview"]["eligible_count"] >= 0

    consumed_overview = client.get("/operations/lifecycle")
    consumed_target = next(
        target
        for target in consumed_overview.json()["targets"]
        if target["key"] == "audit_logs"
    )
    assert consumed_target["latest_preview"] is None

    cancel_payload = {"reason": "Operator cancelled the queued lifecycle test run."}
    cancel_headers = {**browser, "Idempotency-Key": "lifecycle-run-cancel-1"}
    cancelled = client.post(
        f"/operations/lifecycle/runs/{created.json()['id']}/cancel",
        headers=cancel_headers,
        json=cancel_payload,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancellation_requested_by"] == "admin@example.com"
    assert cancelled.json()["cancellation_reason"] == cancel_payload["reason"]
    cancel_replay = client.post(
        f"/operations/lifecycle/runs/{created.json()['id']}/cancel",
        headers=cancel_headers,
        json=cancel_payload,
    )
    assert cancel_replay.status_code == 200, cancel_replay.text
    assert cancel_replay.headers["Idempotency-Replayed"] == "true"
    assert cancel_replay.json() == cancelled.json()

def test_lifecycle_durable_authority_recheck_blocks_mid_request_revocation(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    role = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": "lifecycle-operator",
            "name": "Lifecycle operator",
            "permissions": ["read:operations", "write:operations"],
        },
    )
    assert role.status_code == 201, role.text
    assignment = client.post(
        f"/iam/users/{seed_users['analyst'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={
            "role_id": role.json()["id"],
            "expected_role_revision": role.json()["revision"],
        },
    )
    assert assignment.status_code == 201, assignment.text
    browser = _browser_login(
        client,
        email="analyst@example.com",
        password="AnalystPass123!",
    )
    overview = client.get("/operations/lifecycle")
    assert overview.status_code == 200, overview.text
    target = next(
        item for item in overview.json()["targets"] if item["key"] == "audit_logs"
    )
    payload = {
        **_policy_draft(target),
        "expected_revision": target["policy"]["revision"],
        "schedule_hour_utc": (target["policy"]["schedule_hour_utc"] + 1) % 24,
    }

    from app.api.routes import lifecycle as lifecycle_route

    original_authorize = lifecycle_route.authorize_governance_actor

    def revoke_before_recheck(db, **kwargs):
        row = db.scalar(
            select(IAMUserRoleAssignment).where(
                IAMUserRoleAssignment.id == uuid.UUID(assignment.json()["id"])
            )
        )
        assert row is not None
        db.delete(row)
        db.flush()
        return original_authorize(db, **kwargs)

    monkeypatch.setattr(
        lifecycle_route,
        "authorize_governance_actor",
        revoke_before_recheck,
    )
    rejected = client.put(
        "/operations/lifecycle/policies/audit_logs",
        headers={
            **browser,
            "Idempotency-Key": "lifecycle-revoked-during-mutation-1",
        },
        json=payload,
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["error"]["code"] == "governance_authorization_denied"
    rejection = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "lifecycle.policy.update",
            AuditLog.actor_user_id == seed_users["analyst"].id,
            AuditLog.success.is_(False),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert rejection is not None
