from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread
from time import sleep

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    DataPolicyState,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.ai_daily_brief import AIDailyBrief
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.item import Item
from app.models.report import Report
from app.models.report_source_item import ReportSourceItem
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DATA_ACCESS_RESOURCE_REPORT,
)
from app.services.data_access_retention import (
    prune_deleted_resource_envelopes,
    prune_orphan_data_access_envelopes,
)
from app.services import data_access_retention, data_access_runtime
from app.services.data_access_runtime import (
    ensure_daily_brief_data_access_envelope,
    ensure_integration_delivery_data_access_envelope,
    ensure_integration_event_data_access_envelope,
    ensure_report_data_access_envelope,
)
from app.services.ai_reporting import prune_daily_brief_history
from app.services.report_storage import delete_report


def test_report_and_daily_brief_deletion_remove_leaf_envelopes(db_session):
    now = datetime.now(timezone.utc)
    report = Report(
        title="Leaf retention report",
        period_start=now - timedelta(days=1),
        period_end=now,
    )
    stale_brief = AIDailyBrief(
        brief_date=date(2026, 8, 1),
        window_start=now - timedelta(days=2),
        window_end=now - timedelta(days=1),
    )
    current_brief = AIDailyBrief(
        brief_date=date(2026, 8, 2),
        window_start=now - timedelta(days=1),
        window_end=now,
    )
    db_session.add_all([report, stale_brief, current_brief])
    db_session.flush()
    ensure_report_data_access_envelope(db_session, report_id=report.id)
    ensure_daily_brief_data_access_envelope(db_session, brief_id=stale_brief.id)
    ensure_daily_brief_data_access_envelope(db_session, brief_id=current_brief.id)
    db_session.commit()
    report_id, stale_brief_id, current_brief_id = (
        report.id,
        stale_brief.id,
        current_brief.id,
    )

    delete_report(db_session, report=report)
    assert prune_daily_brief_history(db_session, keep_limit=1) == 1
    db_session.commit()

    assert db_session.get(Report, report_id) is None
    assert db_session.get(AIDailyBrief, stale_brief_id) is None
    assert db_session.get(AIDailyBrief, current_brief_id) is not None
    remaining = set(
        db_session.execute(
            select(
                DataAccessEnvelope.resource_type,
                DataAccessEnvelope.resource_id,
            )
        ).all()
    )
    assert remaining == {("ai_daily_brief", current_brief_id)}


def test_lineage_retention_deletes_replay_chain_child_first(db_session):
    now = datetime.now(timezone.utc)
    feed = Feed(
        name="Retention lineage feed",
        url=f"https://example.com/retention-{uuid.uuid4()}.xml",
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        title="Retention lineage item",
        url=f"https://example.com/retention-{uuid.uuid4()}",
        dedupe_key=f"retention:{uuid.uuid4()}",
        content_hash="a" * 64,
        first_seen_at=now,
    )
    report = Report(
        title="Retention lineage report",
        period_start=now - timedelta(days=1),
        period_end=now,
    )
    db_session.add_all([item, report])
    db_session.flush()
    db_session.add(
        ReportSourceItem(
            report_id=report.id,
            item_id=item.id,
            citation_key="S1",
            included=True,
            rank=1,
            title_snapshot=item.title,
            feed_name_snapshot=feed.name,
            url_snapshot=item.url,
            first_seen_at_snapshot=item.first_seen_at,
            evidence_text=item.title,
        )
    )
    instance = IntegrationInstance(
        name="Retention SMTP",
        integration_type="smtp",
        direction="outbound",
    )
    event = IntegrationEvent(
        event_type="report_ready",
        source_type="report",
        source_id=str(report.id),
        idempotency_key=f"retention-event:{uuid.uuid4()}",
        payload_json={"report_id": str(report.id)},
    )
    db_session.add_all([instance, event])
    db_session.flush()
    ensure_report_data_access_envelope(db_session, report_id=report.id)
    ensure_integration_event_data_access_envelope(db_session, event_id=event.id)
    live = IntegrationDelivery(
        integration_id=instance.id,
        event_id=event.id,
        connector_type="smtp",
        event_type="report_ready",
        delivery_kind="live",
        idempotency_key=f"retention-live:{uuid.uuid4()}",
    )
    db_session.add(live)
    db_session.flush()
    ensure_integration_delivery_data_access_envelope(db_session, delivery_id=live.id)
    replay = IntegrationDelivery(
        integration_id=instance.id,
        event_id=event.id,
        source_delivery_id=live.id,
        connector_type="smtp",
        event_type="report_ready",
        delivery_kind="replay",
        idempotency_key=f"retention-replay:{uuid.uuid4()}",
    )
    db_session.add(replay)
    db_session.flush()
    ensure_integration_delivery_data_access_envelope(
        db_session,
        delivery_id=replay.id,
    )
    db_session.commit()
    report_id, event_id, live_id, replay_id = (
        report.id,
        event.id,
        live.id,
        replay.id,
    )

    db_session.execute(delete(Report).where(Report.id == report_id))
    assert (
        prune_deleted_resource_envelopes(
            db_session,
            resources=((DATA_ACCESS_RESOURCE_REPORT, report_id),),
        )
        == 0
    )
    db_session.execute(
        delete(IntegrationDelivery).where(IntegrationDelivery.id == live_id)
    )
    assert (
        prune_deleted_resource_envelopes(
            db_session,
            resources=((DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, live_id),),
        )
        == 0
    )
    db_session.execute(
        delete(IntegrationDelivery).where(IntegrationDelivery.id == replay_id)
    )
    assert (
        prune_deleted_resource_envelopes(
            db_session,
            resources=((DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, replay_id),),
        )
        == 2
    )
    db_session.execute(delete(IntegrationEvent).where(IntegrationEvent.id == event_id))
    assert (
        prune_deleted_resource_envelopes(
            db_session,
            resources=((DATA_ACCESS_RESOURCE_INTEGRATION_EVENT, event_id),),
        )
        == 2
    )
    assert db_session.scalar(select(func.count(DataAccessEnvelope.id))) == 0


def test_orphan_sweep_selects_lineage_leaves_instead_of_starving(db_session):
    policy_revision = db_session.get(DataPolicyState, 1).revision
    now = datetime.now(timezone.utc)
    envelopes = [
        DataAccessEnvelope(
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=uuid.uuid4(),
            source_count=1,
            policy_revision=policy_revision,
            created_at=now + timedelta(seconds=index),
        )
        for index in range(3)
    ]
    db_session.add_all(envelopes)
    db_session.flush()

    parent_source_id = None
    for index, envelope in enumerate(envelopes):
        source = DataAccessEnvelopeSource(
            envelope_id=envelope.id,
            source_type="system",
            source_id="orphan-retention-chain",
            source_version="v1",
            source_parent_id=parent_source_id,
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
            captured_policy_revision=policy_revision,
            captured_at=now + timedelta(seconds=index),
        )
        db_session.add_all(
            [
                source,
                DataAccessEnvelopeLabel(
                    envelope_id=envelope.id,
                    label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                    source_count=1,
                ),
            ]
        )
        db_session.flush()
        parent_source_id = source.id
    db_session.commit()

    first = prune_orphan_data_access_envelopes(db_session, limit=1)
    assert first.deleted_count == 1
    assert first.candidates_scanned == 1
    assert first.backlog_remaining is True

    second = prune_orphan_data_access_envelopes(db_session, limit=1)
    assert second.deleted_count == 1
    assert second.backlog_remaining is True

    third = prune_orphan_data_access_envelopes(db_session, limit=1)
    assert third.deleted_count == 1
    assert third.backlog_remaining is False
    assert db_session.scalar(select(func.count(DataAccessEnvelope.id))) == 0


def test_retention_rejects_invalid_target_references(db_session):
    with pytest.raises(ValueError, match="Unsupported"):
        prune_deleted_resource_envelopes(
            db_session,
            resources=(("future_resource", uuid.uuid4()),),
        )
    with pytest.raises(ValueError, match="UUID"):
        prune_deleted_resource_envelopes(
            db_session,
            resources=((DATA_ACCESS_RESOURCE_REPORT, "not-a-uuid"),),
        )


def test_unknown_resource_types_are_reported_and_retained(db_session, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        data_access_retention.logger,
        "warning",
        lambda message, count: warnings.append((message, count)),
    )
    policy_revision = db_session.get(DataPolicyState, 1).revision
    db_session.add(
        DataAccessEnvelope(
            resource_type="future_resource",
            resource_id=uuid.uuid4(),
            source_count=0,
            policy_revision=policy_revision,
        )
    )
    db_session.commit()

    result = prune_orphan_data_access_envelopes(db_session, limit=10)

    assert result.deleted_count == 0
    assert result.unknown_resource_types == 1
    assert result.backlog_remaining is True
    assert warnings == [("data_access_retention_unknown_resource_types count=%s", 1)]


def test_resource_deletion_waits_for_envelope_creation(
    database_engine,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    report = Report(
        title="Concurrent retention report",
        period_start=now - timedelta(days=1),
        period_end=now,
    )
    session_factory = sessionmaker(
        bind=database_engine,
        class_=Session,
        expire_on_commit=False,
    )
    with session_factory() as setup_session:
        setup_session.add(report)
        setup_session.commit()
    report_id = report.id

    target_locked = Event()
    release_target = Event()
    delete_started = Event()
    delete_finished = Event()
    errors: list[BaseException] = []
    original_lock = data_access_runtime._lock_envelope_target

    def blocking_target_lock(db, model, resource_id):
        target = original_lock(db, model, resource_id)
        if model is Report and resource_id == report_id:
            target_locked.set()
            assert release_target.wait(timeout=5)
        return target

    monkeypatch.setattr(
        data_access_runtime,
        "_lock_envelope_target",
        blocking_target_lock,
    )

    def create_envelope():
        try:
            with session_factory() as session:
                ensure_report_data_access_envelope(session, report_id=report_id)
                session.commit()
        except BaseException as exc:
            errors.append(exc)

    def delete_resource():
        try:
            with session_factory() as session:
                delete_started.set()
                session.execute(delete(Report).where(Report.id == report_id))
                prune_deleted_resource_envelopes(
                    session,
                    resources=((DATA_ACCESS_RESOURCE_REPORT, report_id),),
                )
                session.commit()
                delete_finished.set()
        except BaseException as exc:
            errors.append(exc)

    creator = Thread(target=create_envelope, daemon=True)
    deleter = Thread(target=delete_resource, daemon=True)
    creator.start()
    assert target_locked.wait(timeout=5)
    deleter.start()
    assert delete_started.wait(timeout=5)
    sleep(0.1)
    assert delete_finished.is_set() is False

    release_target.set()
    creator.join(timeout=5)
    deleter.join(timeout=5)

    assert creator.is_alive() is False
    assert deleter.is_alive() is False
    assert errors == []
    with session_factory() as verify_session:
        assert verify_session.get(Report, report_id) is None
        assert (
            verify_session.scalar(
                select(func.count(DataAccessEnvelope.id)).where(
                    DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                    DataAccessEnvelope.resource_id == report_id,
                )
            )
            == 0
        )
