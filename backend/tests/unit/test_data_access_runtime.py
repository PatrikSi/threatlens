from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.data_policy import (
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.investigation import Investigation, InvestigationEvidence
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.report import Report
from app.models.report_source_item import ReportSourceItem
from app.services.ai_reporting import get_latest_daily_brief, get_recent_daily_briefs
from app.services.ai_task_projection import list_daily_brief_source_items
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DATA_ACCESS_RESOURCE_REPORT,
    get_data_access_envelope,
    get_data_access_envelope_sources,
)
from app.services.data_access_runtime import (
    ensure_daily_brief_data_access_envelope,
    ensure_integration_delivery_data_access_envelope,
    ensure_integration_event_data_access_envelope,
    ensure_investigation_data_access_envelope,
    ensure_report_data_access_envelope,
    merge_investigation_evidence_data_access,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyRevisionConflict,
)


def _restricted_feed_and_item(db_session) -> tuple[HandlingLabel, Feed, Item]:
    label = HandlingLabel(
        key=f"runtime-{uuid.uuid4().hex[:12]}",
        name="Runtime restricted",
        description="Runtime lineage test label.",
        color="#B91C1C",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
    )
    db_session.add(label)
    db_session.flush()
    feed = Feed(name=f"Runtime feed {uuid.uuid4()}", handling_label_id=label.id)
    feed.url = f"https://example.com/runtime/{uuid.uuid4()}.xml"
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        url=f"https://example.com/runtime/{uuid.uuid4()}",
        title="Runtime lineage item",
        dedupe_key=f"runtime-lineage:{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex * 2,
        first_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(item)
    db_session.flush()
    return label, feed, item


def _report_with_source(db_session, item: Item) -> Report:
    now = datetime.now(timezone.utc)
    report = Report(
        title="Runtime report",
        period_start=now - timedelta(days=1),
        period_end=now,
    )
    db_session.add(report)
    db_session.flush()
    db_session.add(
        ReportSourceItem(
            report_id=report.id,
            item_id=item.id,
            citation_key="S1",
            included=True,
            rank=1,
            title_snapshot=item.title,
            feed_name_snapshot="Runtime feed",
            url_snapshot=item.url,
            first_seen_at_snapshot=item.first_seen_at,
            evidence_text=item.title,
        )
    )
    db_session.flush()
    return report


def test_report_event_delivery_and_replay_copy_normalized_lineage(db_session):
    label, _feed, item = _restricted_feed_and_item(db_session)
    report = _report_with_source(db_session, item)
    ensure_report_data_access_envelope(db_session, report_id=report.id)
    captured_content_hash = item.content_hash
    item.content_hash = uuid.uuid4().hex * 2
    db_session.flush()

    event = IntegrationEvent(
        event_type="report_ready",
        source_type="report",
        source_id=str(report.id),
        idempotency_key=f"runtime-report:{uuid.uuid4()}",
        payload_json={"report_id": str(report.id)},
    )
    instance = IntegrationInstance(
        name="Runtime SMTP",
        integration_type="smtp",
        direction="outbound",
    )
    db_session.add_all([event, instance])
    db_session.flush()
    ensure_integration_event_data_access_envelope(db_session, event_id=event.id)
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        event_id=event.id,
        connector_type="smtp",
        event_type=event.event_type,
        delivery_kind="live",
        state="pending",
        idempotency_key=f"runtime-live:{uuid.uuid4()}",
    )
    db_session.add(delivery)
    db_session.flush()
    ensure_integration_delivery_data_access_envelope(
        db_session, delivery_id=delivery.id
    )
    replay = IntegrationDelivery(
        integration_id=instance.id,
        event_id=event.id,
        source_delivery_id=delivery.id,
        connector_type="smtp",
        event_type=event.event_type,
        delivery_kind="replay",
        state="pending",
        idempotency_key=f"runtime-replay:{uuid.uuid4()}",
    )
    db_session.add(replay)
    db_session.flush()
    ensure_integration_delivery_data_access_envelope(db_session, delivery_id=replay.id)

    resources = (
        (DATA_ACCESS_RESOURCE_REPORT, report.id),
        (DATA_ACCESS_RESOURCE_INTEGRATION_EVENT, event.id),
        (DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, delivery.id),
        (DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, replay.id),
    )
    source_sets = [
        get_data_access_envelope_sources(
            db_session, resource_type=resource_type, resource_id=resource_id
        )
        for resource_type, resource_id in resources
    ]
    assert all(len(sources) == 1 for sources in source_sets)
    assert all(sources[0].handling_label_id == label.id for sources in source_sets)
    assert source_sets[0][0].source_digest == captured_content_hash
    assert source_sets[0][0].source_parent_id is None
    assert source_sets[1][0].source_parent_id == source_sets[0][0].id
    assert source_sets[2][0].source_parent_id == source_sets[1][0].id
    assert source_sets[3][0].source_parent_id == source_sets[2][0].id


def test_report_lineage_rejects_a_stale_planning_revision(db_session):
    _label, _feed, item = _restricted_feed_and_item(db_session)
    report = _report_with_source(db_session, item)
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    planned_revision = state.revision
    state.revision += 1
    db_session.flush()

    with pytest.raises(DataPolicyRevisionConflict):
        ensure_report_data_access_envelope(
            db_session,
            report_id=report.id,
            expected_policy_revision=planned_revision,
        )

    assert (
        get_data_access_envelope(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report.id,
        )
        is None
    )


def test_missing_event_parent_is_quarantined_for_rolling_compatibility(db_session):
    missing_brief_id = uuid.uuid4()
    event = IntegrationEvent(
        event_type="daily_digest",
        source_type="ai_daily_brief",
        source_id=str(missing_brief_id),
        idempotency_key=f"runtime-missing:{uuid.uuid4()}",
        payload_json={"brief_id": str(missing_brief_id)},
    )
    db_session.add(event)
    db_session.flush()

    ensure_integration_event_data_access_envelope(db_session, event_id=event.id)

    sources = get_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=event.id,
    )
    assert len(sources) == 1
    assert sources[0].source_type == "unresolved"
    assert sources[0].handling_label_id == QUARANTINE_HANDLING_LABEL_ID


def test_orphan_delivery_requires_explicit_test_kind_for_unrestricted_lineage(
    db_session,
):
    instance = IntegrationInstance(
        name="Runtime orphan",
        integration_type="smtp",
        direction="outbound",
    )
    db_session.add(instance)
    db_session.flush()
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        connector_type="smtp",
        event_type="contest_update",
        delivery_kind="live",
        state="pending",
        idempotency_key=f"runtime-contest:{uuid.uuid4()}",
    )
    db_session.add(delivery)
    db_session.flush()

    snapshot = ensure_integration_delivery_data_access_envelope(
        db_session, delivery_id=delivery.id
    )

    assert snapshot.label_ids == frozenset({QUARANTINE_HANDLING_LABEL_ID})


def test_conflicting_legacy_webhook_item_and_feed_lineage_is_quarantined(
    db_session,
    seed_users,
):
    _label, item_feed, item = _restricted_feed_and_item(db_session)
    conflicting_feed = Feed(
        name=f"Conflicting legacy feed {uuid.uuid4()}",
        url=f"https://example.com/conflicting/{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    webhook = NotificationWebhook(
        user_id=seed_users["admin"].id,
        name="Conflicting legacy lineage",
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
    instance = IntegrationInstance(
        name="Conflicting legacy integration",
        integration_type="webhook",
        direction="destination",
    )
    db_session.add_all([conflicting_feed, webhook, instance])
    db_session.flush()
    assert item.feed_id == item_feed.id
    legacy = NotificationWebhookDelivery(
        webhook_id=webhook.id,
        user_id=seed_users["admin"].id,
        event_type_snapshot="rss_item_new",
        item_id=item.id,
        feed_id=conflicting_feed.id,
        delivery_state="failed",
        success=False,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
    )
    db_session.add(legacy)
    db_session.flush()
    event = IntegrationEvent(
        event_type="rss_item_new",
        source_type="notification_webhook_delivery",
        source_id=str(legacy.id),
        idempotency_key=f"conflicting-legacy-event:{uuid.uuid4()}",
        payload_json={},
    )
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        connector_type="webhook",
        event_type="rss_item_new",
        delivery_kind="live",
        state="dead_letter",
        idempotency_key=f"conflicting-legacy-delivery:{uuid.uuid4()}",
        payload_json={"legacy_webhook_delivery_id": str(legacy.id)},
        max_attempts=3,
    )
    db_session.add_all([event, delivery])
    db_session.flush()

    event_snapshot = ensure_integration_event_data_access_envelope(
        db_session, event_id=event.id
    )
    delivery_snapshot = ensure_integration_delivery_data_access_envelope(
        db_session, delivery_id=delivery.id
    )

    assert event_snapshot.label_ids == frozenset({QUARANTINE_HANDLING_LABEL_ID})
    assert delivery_snapshot.label_ids == frozenset({QUARANTINE_HANDLING_LABEL_ID})
    for resource_type, resource_id in (
        (DATA_ACCESS_RESOURCE_INTEGRATION_EVENT, event.id),
        (DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, delivery.id),
    ):
        sources = get_data_access_envelope_sources(
            db_session,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        assert len(sources) == 1
        assert sources[0].source_type == "unresolved"


def test_item_required_legacy_event_is_quarantined_after_item_deletion(
    db_session,
    seed_users,
):
    _label, item_feed, item = _restricted_feed_and_item(db_session)
    misleading_feed = Feed(
        name=f"Delayed legacy feed {uuid.uuid4()}",
        url=f"https://example.com/delayed/{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    webhook = NotificationWebhook(
        user_id=seed_users["admin"].id,
        name="Delayed legacy lineage",
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
    db_session.add_all([misleading_feed, webhook])
    db_session.flush()
    assert item.feed_id == item_feed.id
    legacy = NotificationWebhookDelivery(
        webhook_id=webhook.id,
        user_id=seed_users["admin"].id,
        event_type_snapshot="rss_item_new",
        item_id=item.id,
        feed_id=misleading_feed.id,
        delivery_state="failed",
        success=False,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
    )
    db_session.add(legacy)
    db_session.flush()
    event = IntegrationEvent(
        event_type="rss_item_new",
        source_type="notification_webhook_delivery",
        source_id=str(legacy.id),
        idempotency_key=f"delayed-legacy-event:{uuid.uuid4()}",
        payload_json={},
    )
    db_session.add(event)
    db_session.flush()

    db_session.delete(item)
    db_session.flush()
    db_session.expire(legacy)
    assert legacy.item_id is None

    snapshot = ensure_integration_event_data_access_envelope(
        db_session,
        event_id=event.id,
    )
    sources = get_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=event.id,
    )

    assert snapshot.label_ids == frozenset({QUARANTINE_HANDLING_LABEL_ID})
    assert len(sources) == 1
    assert sources[0].source_type == "unresolved"
    assert sources[0].source_feed_id is None


def test_feed_failing_legacy_event_retains_unambiguous_feed_lineage(
    db_session,
    seed_users,
):
    label, feed, _item = _restricted_feed_and_item(db_session)
    webhook = NotificationWebhook(
        user_id=seed_users["admin"].id,
        name="Feed-only legacy lineage",
        event_type="feed_failing",
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
        webhook_id=webhook.id,
        user_id=seed_users["admin"].id,
        event_type_snapshot="feed_failing",
        item_id=None,
        feed_id=feed.id,
        delivery_state="failed",
        success=False,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
    )
    db_session.add(legacy)
    db_session.flush()
    event = IntegrationEvent(
        event_type="feed_failing",
        source_type="notification_webhook_delivery",
        source_id=str(legacy.id),
        idempotency_key=f"feed-only-legacy-event:{uuid.uuid4()}",
        payload_json={},
    )
    db_session.add(event)
    db_session.flush()

    snapshot = ensure_integration_event_data_access_envelope(
        db_session,
        event_id=event.id,
    )
    sources = get_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
        resource_id=event.id,
    )

    assert snapshot.label_ids == frozenset({label.id})
    assert len(sources) == 1
    assert sources[0].source_type == "feed"
    assert sources[0].source_feed_id == feed.id


def test_investigation_lineage_only_broadens_after_evidence_removal(db_session):
    label, _feed, item = _restricted_feed_and_item(db_session)
    investigation = Investigation(title="Runtime investigation")
    db_session.add(investigation)
    db_session.flush()
    initial = ensure_investigation_data_access_envelope(
        db_session, investigation_id=investigation.id
    )
    assert initial.label_ids == frozenset({UNRESTRICTED_HANDLING_LABEL_ID})

    evidence = InvestigationEvidence(
        investigation_id=investigation.id,
        source_type="item",
        source_id=item.id,
        title_snapshot=item.title,
        metadata_snapshot_json={},
    )
    db_session.add(evidence)
    db_session.flush()
    expanded = merge_investigation_evidence_data_access(db_session, evidence=evidence)
    assert expanded.label_ids == frozenset({UNRESTRICTED_HANDLING_LABEL_ID, label.id})

    db_session.commit()
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.revision += 1
    db_session.commit()
    second_label, _second_feed, second_item = _restricted_feed_and_item(db_session)
    second_evidence = InvestigationEvidence(
        investigation_id=investigation.id,
        source_type="item",
        source_id=second_item.id,
        title_snapshot=second_item.title,
        metadata_snapshot_json={},
    )
    db_session.add(second_evidence)
    db_session.flush()
    expanded = merge_investigation_evidence_data_access(
        db_session, evidence=second_evidence
    )
    assert expanded.label_ids == frozenset(
        {UNRESTRICTED_HANDLING_LABEL_ID, label.id, second_label.id}
    )

    db_session.delete(evidence)
    db_session.flush()
    retained = ensure_investigation_data_access_envelope(
        db_session, investigation_id=investigation.id
    )
    assert retained.label_ids == expanded.label_ids


def test_missing_historical_investigation_parent_is_quarantined(db_session):
    investigation = Investigation(title="Runtime missing parent")
    db_session.add(investigation)
    db_session.flush()
    evidence = InvestigationEvidence(
        investigation_id=investigation.id,
        source_type="report",
        source_id=uuid.uuid4(),
        title_snapshot="Removed report",
        metadata_snapshot_json={},
    )
    db_session.add(evidence)
    db_session.flush()

    snapshot = ensure_investigation_data_access_envelope(
        db_session, investigation_id=investigation.id
    )

    assert snapshot.label_ids == frozenset(
        {UNRESTRICTED_HANDLING_LABEL_ID, QUARANTINE_HANDLING_LABEL_ID}
    )


def test_daily_brief_captures_every_audited_source_item(db_session):
    label, _feed, item = _restricted_feed_and_item(db_session)
    now = datetime.now(timezone.utc)
    brief = AIDailyBrief(
        brief_date=date.today(),
        window_start=now - timedelta(days=1),
        window_end=now,
    )
    db_session.add(brief)
    db_session.flush()
    source_row = AIDailyBriefSourceItem(
        daily_brief_id=brief.id,
        item_id=item.id,
        included=False,
        rank=1,
        exclusion_reason="brief_item_cap",
        title_snapshot=item.title,
    )
    db_session.add(source_row)
    db_session.flush()

    snapshot = ensure_daily_brief_data_access_envelope(db_session, brief_id=brief.id)

    assert snapshot.label_ids == frozenset({label.id})
    sources = get_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
    )
    assert len(sources) == 1
    assert sources[0].source_id == str(item.id)
    assert sources[0].source_digest == item.content_hash

    db_session.commit()
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.revision += 1
    db_session.commit()
    db_session.delete(source_row)
    db_session.flush()
    db_session.add(
        AIDailyBriefSourceItem(
            daily_brief_id=brief.id,
            item_id=item.id,
            included=True,
            rank=1,
            title_snapshot=item.title,
        )
    )
    db_session.flush()
    ensure_daily_brief_data_access_envelope(db_session, brief_id=brief.id)
    sources = get_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
    )
    assert len(sources) == 1


def test_daily_brief_reads_filter_normalized_lineage(db_session):
    restricted_label, _restricted_feed, restricted_item = _restricted_feed_and_item(
        db_session
    )
    unrestricted_feed = Feed(
        name=f"Runtime unrestricted {uuid.uuid4()}",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    unrestricted_feed.url = f"https://example.com/runtime/{uuid.uuid4()}.xml"
    db_session.add(unrestricted_feed)
    db_session.flush()
    unrestricted_item = Item(
        feed_id=unrestricted_feed.id,
        url=f"https://example.com/runtime/{uuid.uuid4()}",
        title="Unrestricted daily brief item",
        dedupe_key=f"runtime-daily:{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex * 2,
        first_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(unrestricted_item)
    db_session.flush()

    now = datetime.now(timezone.utc)
    unrestricted_brief = AIDailyBrief(
        brief_date=date.today() - timedelta(days=1),
        status="ready",
        window_start=now - timedelta(days=2),
        window_end=now - timedelta(days=1),
        title="Unrestricted brief",
        generated_at=now - timedelta(days=1),
    )
    restricted_brief = AIDailyBrief(
        brief_date=date.today(),
        status="ready",
        window_start=now - timedelta(days=1),
        window_end=now,
        title="Restricted brief",
        generated_at=now,
    )
    db_session.add_all([unrestricted_brief, restricted_brief])
    db_session.flush()
    db_session.add_all(
        [
            AIDailyBriefSourceItem(
                daily_brief_id=unrestricted_brief.id,
                item_id=unrestricted_item.id,
                included=True,
                rank=1,
                title_snapshot=unrestricted_item.title,
            ),
            AIDailyBriefSourceItem(
                daily_brief_id=restricted_brief.id,
                item_id=restricted_item.id,
                included=True,
                rank=1,
                title_snapshot=restricted_item.title,
            ),
        ]
    )
    db_session.flush()
    ensure_daily_brief_data_access_envelope(db_session, brief_id=unrestricted_brief.id)
    ensure_daily_brief_data_access_envelope(db_session, brief_id=restricted_brief.id)

    restricted_context = DataAccessContext(
        mode="enforced",
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )
    privileged_context = DataAccessContext(
        mode="enforced",
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset(
            {UNRESTRICTED_HANDLING_LABEL_ID, restricted_label.id}
        ),
    )

    assert (
        get_latest_daily_brief(db_session, data_access=restricted_context).id
        == unrestricted_brief.id
    )
    assert [
        brief.id
        for brief in get_recent_daily_briefs(
            db_session, limit=10, data_access=restricted_context
        )
    ] == [unrestricted_brief.id]
    assert (
        list_daily_brief_source_items(
            db_session,
            daily_brief_id=restricted_brief.id,
            data_access=restricted_context,
        )
        is None
    )
    assert (
        get_latest_daily_brief(db_session, data_access=privileged_context).id
        == restricted_brief.id
    )
