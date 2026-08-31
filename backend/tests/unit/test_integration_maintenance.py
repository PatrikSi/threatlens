import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.data_policy import (
    DataAccessEnvelopeLabel,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationDeliveryMetric,
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortFeed,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
    IntegrationEvent,
    IntegrationInstance,
    IntegrationSubscription,
)
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.integration_maintenance import (
    prune_integration_delivery_history,
    rollup_terminal_integration_deliveries,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DataAccessSourceInput,
    merge_data_access_envelope_sources,
)
from app.services.data_access_policy import (
    DataAccessContext,
    assign_feed_handling_label,
)
from app.services.integration_metric_data_policy import (
    integration_metric_cohort_data_access_predicate,
    integration_metric_cohort_integrity,
)


def test_terminal_delivery_metrics_are_rolled_up_exactly_once(db_session, monkeypatch):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    _event, delivery, _legacy = _persist_terminal_webhook_delivery(
        db_session,
        completed_at=now - timedelta(hours=2),
    )
    db_session.add_all(
        [
            IntegrationAttempt(
                delivery_id=delivery.id,
                integration_id=delivery.integration_id,
                attempt_number=1,
                status="failed",
                started_at=now - timedelta(hours=2, seconds=2),
                finished_at=now - timedelta(hours=2, seconds=1),
                duration_ms=100,
            ),
            IntegrationAttempt(
                delivery_id=delivery.id,
                integration_id=delivery.integration_id,
                attempt_number=2,
                status="failed",
                started_at=now - timedelta(hours=2, seconds=1),
                finished_at=now - timedelta(hours=2),
                duration_ms=250,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_metrics_delay_seconds",
        0,
    )

    first = rollup_terminal_integration_deliveries(db_session, now=now)
    second = rollup_terminal_integration_deliveries(db_session, now=now)

    metric = db_session.query(IntegrationDeliveryMetric).one()
    cohort = db_session.query(IntegrationDeliveryMetricCohort).one()
    assert first == 1
    assert second == 0
    assert metric.dead_letter_count == 1
    assert metric.failed_count == 0
    assert metric.attempt_count == 2
    assert metric.duration_total_ms == 350
    assert metric.duration_max_ms == 250
    assert cohort.metric_id == metric.id
    assert cohort.provenance_complete is True
    assert cohort.source_count == 1
    assert cohort.dead_letter_count == metric.dead_letter_count
    assert cohort.attempt_count == metric.attempt_count
    assert cohort.duration_total_ms == metric.duration_total_ms
    assert cohort.duration_max_ms == metric.duration_max_ms
    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortLabel).all()
    } == {UNRESTRICTED_HANDLING_LABEL_ID}
    assert db_session.query(IntegrationDeliveryMetricCohortFeed).count() == 0


def test_metric_rollup_retains_feed_provenance_across_relabel(
    db_session,
    monkeypatch,
):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    label = HandlingLabel(
        key=f"integration-metric-{uuid.uuid4().hex[:12]}",
        name="Integration metric restricted",
        description="Integration delivery metric cohort test.",
        color="#B91C1C",
    )
    db_session.add(label)
    db_session.flush()
    feed = Feed(
        name=f"Integration metric feed {uuid.uuid4()}",
        url=f"https://example.com/metric/{uuid.uuid4()}.xml",
        handling_label_id=label.id,
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        url=f"https://example.com/metric/item/{uuid.uuid4()}",
        title="Integration metric item",
        dedupe_key=f"integration-metric:{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex * 2,
        first_seen_at=now - timedelta(hours=3),
    )
    db_session.add(item)
    db_session.commit()
    event, delivery, _legacy = _persist_terminal_webhook_delivery(
        db_session,
        completed_at=now - timedelta(hours=2),
    )
    event.source_type = "item"
    event.source_id = str(item.id)
    db_session.add(event)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_metrics_delay_seconds",
        0,
    )

    assert rollup_terminal_integration_deliveries(db_session, now=now) == 1

    cohort = db_session.query(IntegrationDeliveryMetricCohort).one()
    captured_key = cohort.policy_cohort_key
    assert cohort.provenance_complete is True
    assert cohort.source_count == 1
    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortLabel).all()
    } == {label.id}
    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortCapturedLabel).all()
    } == {label.id}
    assert db_session.query(IntegrationDeliveryMetricCohortTaintLabel).count() == 0
    assert {
        row.source_feed_id_snapshot
        for row in db_session.query(IntegrationDeliveryMetricCohortFeed).all()
    } == {feed.id}
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    assert delivery.owner_user_id is not None
    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        expected_policy_revision=state.revision,
        actor_user_id=delivery.owner_user_id,
    )
    db_session.commit()

    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortLabel).all()
    } == {label.id, UNRESTRICTED_HANDLING_LABEL_ID}
    db_session.refresh(cohort)
    assert cohort.policy_cohort_key == captured_key
    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortCapturedLabel).all()
    } == {label.id}
    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortTaintLabel).all()
    } == {UNRESTRICTED_HANDLING_LABEL_ID}
    unrestricted_only = DataAccessContext(
        mode="enforced",
        policy_revision=state.revision,
        coverage_version=1,
        principal_type="user",
        principal_id=delivery.owner_user_id,
        principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )
    assert (
        db_session.scalar(
            select(IntegrationDeliveryMetricCohort.id).where(
                integration_metric_cohort_data_access_predicate(unrestricted_only)
            )
        )
        is None
    )

    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=label.id,
        expected_policy_revision=state.revision,
        actor_user_id=delivery.owner_user_id,
    )
    db_session.commit()
    assert {
        row.label_id
        for row in db_session.query(IntegrationDeliveryMetricCohortTaintLabel).all()
    } == {label.id, UNRESTRICTED_HANDLING_LABEL_ID}
    restricted_only = DataAccessContext(
        mode="enforced",
        policy_revision=state.revision,
        coverage_version=1,
        principal_type="user",
        principal_id=delivery.owner_user_id,
        principal_eligible=True,
        allowed_label_ids=frozenset({label.id}),
    )
    assert (
        db_session.scalar(
            select(IntegrationDeliveryMetricCohort.id).where(
                integration_metric_cohort_data_access_predicate(restricted_only)
            )
        )
        is None
    )
    assert integration_metric_cohort_integrity(db_session).valid is True

    cohort.policy_cohort_key = "0" * 64
    db_session.add(cohort)
    db_session.flush()
    corrupted = integration_metric_cohort_integrity(db_session)
    assert corrupted.invalid_identity_count == 1
    assert corrupted.valid is False
    db_session.rollback()
    cohort = db_session.query(IntegrationDeliveryMetricCohort).one()
    assert cohort.policy_cohort_key == captured_key
    db_session.delete(feed)
    db_session.commit()
    assert {
        row.source_feed_id_snapshot
        for row in db_session.query(IntegrationDeliveryMetricCohortFeed).all()
    } == {feed.id}
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_metrics_retention_days",
        1,
    )
    retention = prune_integration_delivery_history(
        db_session,
        now=now + timedelta(days=2),
    )
    assert retention["metrics_deleted"] == 1
    assert db_session.query(IntegrationDeliveryMetric).count() == 0
    assert db_session.query(IntegrationDeliveryMetricCohort).count() == 0


def test_retention_deletes_legacy_projection_only_after_generic_rollup(
    db_session, monkeypatch
):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    event, delivery, legacy = _persist_terminal_webhook_delivery(
        db_session,
        completed_at=now - timedelta(days=2),
    )
    event_id = event.id
    delivery_id = delivery.id
    legacy_id = legacy.id
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_metrics_delay_seconds",
        0,
    )
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_retention_days",
        1,
    )
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_event_retention_days",
        1,
    )

    before_rollup = prune_integration_delivery_history(db_session, now=now)
    rolled_up = rollup_terminal_integration_deliveries(db_session, now=now)
    after_rollup = prune_integration_delivery_history(db_session, now=now)

    assert before_rollup["deliveries_deleted"] == 0
    assert before_rollup["webhook_deliveries_deleted"] == 0
    assert rolled_up == 1
    assert after_rollup["deliveries_deleted"] == 1
    assert after_rollup["webhook_deliveries_deleted"] == 1
    assert after_rollup["events_deleted"] == 1
    assert db_session.get(IntegrationDelivery, delivery_id) is None
    assert db_session.get(NotificationWebhookDelivery, legacy_id) is None
    assert db_session.get(IntegrationEvent, event_id) is None
    assert db_session.query(IntegrationDeliveryMetric).count() == 1


def test_retention_prunes_only_safe_unlinked_legacy_deliveries(db_session, monkeypatch):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=2)
    user = User(
        id=uuid.uuid4(),
        email=f"legacy-retention-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
        is_approved=True,
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Legacy retention webhook",
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
    db_session.add(user)
    db_session.flush()
    db_session.add(webhook)
    db_session.flush()

    pruneable = _unlinked_legacy_delivery(webhook, user, attempted_at=old)
    active_retry_root = _unlinked_legacy_delivery(webhook, user, attempted_at=old)
    repair_root = _unlinked_legacy_delivery(webhook, user, attempted_at=old)
    repaired_root = _unlinked_legacy_delivery(webhook, user, attempted_at=old)
    malformed_root = _unlinked_legacy_delivery(webhook, user, attempted_at=old)
    db_session.add_all(
        [
            pruneable,
            active_retry_root,
            repair_root,
            repaired_root,
            malformed_root,
        ]
    )
    db_session.flush()
    active_retry = _unlinked_legacy_delivery(
        webhook,
        user,
        attempted_at=now,
        delivery_state="pending",
        source_delivery_id=active_retry_root.id,
    )
    event_needing_repair = IntegrationEvent(
        event_type="rss_item_new",
        source_type="notification_webhook_delivery",
        source_id=str(repair_root.id),
        idempotency_key=f"legacy-repair:{uuid.uuid4()}",
        payload_json={},
        routing_state="pending",
        created_at=now,
    )
    repaired_event = IntegrationEvent(
        event_type="rss_item_new",
        source_type="notification_webhook_delivery",
        source_id=str(repaired_root.id),
        idempotency_key=f"legacy-repaired:{uuid.uuid4()}",
        payload_json={},
        routing_state="pending",
        created_at=now,
    )
    malformed_event = IntegrationEvent(
        event_type="rss_item_new",
        source_type="notification_webhook_delivery",
        source_id=str(malformed_root.id),
        idempotency_key=f"legacy-malformed:{uuid.uuid4()}",
        payload_json={},
        routing_state="pending",
        created_at=now,
    )
    db_session.add_all(
        [active_retry, event_needing_repair, repaired_event, malformed_event]
    )
    db_session.flush()
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=repaired_event.id,
        sources=(
            DataAccessSourceInput(
                source_type="legacy_fixture",
                source_id=str(repaired_root.id),
                source_version="1",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                captured_policy_revision=state.revision,
            ),
        ),
    )
    malformed_snapshot = merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=malformed_event.id,
        sources=(
            DataAccessSourceInput(
                source_type="legacy_fixture",
                source_id=str(malformed_root.id),
                source_version="1",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                captured_policy_revision=state.revision,
            ),
        ),
    )
    malformed_label = db_session.scalar(
        select(DataAccessEnvelopeLabel).where(
            DataAccessEnvelopeLabel.envelope_id == malformed_snapshot.envelope_id
        )
    )
    assert malformed_label is not None
    malformed_label.source_count = 2
    db_session.add(malformed_label)
    pruneable_id = pruneable.id
    active_retry_root_id = active_retry_root.id
    active_retry_id = active_retry.id
    repair_root_id = repair_root.id
    repaired_root_id = repaired_root.id
    malformed_root_id = malformed_root.id
    db_session.commit()
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_retention_days",
        1,
    )

    result = prune_integration_delivery_history(db_session, now=now, batch_size=20)

    assert result["webhook_deliveries_deleted"] == 2
    assert db_session.get(NotificationWebhookDelivery, pruneable_id) is None
    assert db_session.get(NotificationWebhookDelivery, repaired_root_id) is None
    assert db_session.get(NotificationWebhookDelivery, active_retry_root_id) is not None
    assert db_session.get(NotificationWebhookDelivery, active_retry_id) is not None
    assert db_session.get(NotificationWebhookDelivery, repair_root_id) is not None
    assert db_session.get(NotificationWebhookDelivery, malformed_root_id) is not None


def _persist_terminal_webhook_delivery(
    db_session,
    *,
    completed_at: datetime,
) -> tuple[IntegrationEvent, IntegrationDelivery, NotificationWebhookDelivery]:
    user = User(
        id=uuid.uuid4(),
        email=f"metric-{uuid.uuid4()}@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
        is_approved=True,
    )
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        name="Metric webhook",
        integration_type="webhook",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(instance)
    db_session.flush()
    subscription = IntegrationSubscription(
        integration_id=instance.id,
        subscription_key="legacy-webhook",
        event_type="rss_item_new",
    )
    db_session.add(subscription)
    db_session.flush()
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        integration_id=instance.id,
        subscription_id=subscription.id,
        user_id=user.id,
        name="Metric webhook",
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
    event = IntegrationEvent(
        id=uuid.uuid4(),
        event_type="rss_item_new",
        source_type="test",
        idempotency_key=f"metric-event:{uuid.uuid4()}",
        payload_json={},
        routing_state="routed",
        available_at=completed_at,
        routed_at=completed_at,
        created_at=completed_at,
    )
    delivery = IntegrationDelivery(
        id=uuid.uuid4(),
        integration_id=instance.id,
        subscription_id=subscription.id,
        event_id=event.id,
        owner_user_id=user.id,
        connector_type="webhook",
        event_type="rss_item_new",
        delivery_kind="live",
        state="dead_letter",
        idempotency_key=f"metric-delivery:{uuid.uuid4()}",
        payload_json={},
        attempt_count=2,
        completed_at=completed_at,
        dead_lettered_at=completed_at,
        created_at=completed_at,
        updated_at=completed_at,
    )
    db_session.add_all([webhook, event])
    db_session.flush()
    db_session.add(delivery)
    db_session.flush()
    legacy = NotificationWebhookDelivery(
        id=uuid.uuid4(),
        integration_delivery_id=delivery.id,
        webhook_id=webhook.id,
        user_id=user.id,
        event_type_snapshot="rss_item_new",
        delivery_kind="live",
        delivery_state="failed",
        attempt_count=2,
        success=False,
        timeout_seconds=10,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        error="HTTP 503",
        attempted_at=completed_at,
    )
    db_session.add(legacy)
    db_session.commit()
    return event, delivery, legacy


def _unlinked_legacy_delivery(
    webhook: NotificationWebhook,
    user: User,
    *,
    attempted_at: datetime,
    delivery_state: str = "failed",
    source_delivery_id: uuid.UUID | None = None,
) -> NotificationWebhookDelivery:
    return NotificationWebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook.id,
        user_id=user.id,
        source_delivery_id=source_delivery_id,
        event_type_snapshot="rss_item_new",
        delivery_kind="retry" if source_delivery_id is not None else "live",
        delivery_state=delivery_state,
        success=delivery_state == "succeeded",
        timeout_seconds=10,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        attempted_at=attempted_at,
    )
