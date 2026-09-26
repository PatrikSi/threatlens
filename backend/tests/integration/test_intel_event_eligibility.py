"""Delivery readiness rejects stale approvals and uses nonblocking source fences."""

from copy import deepcopy

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.services.intel_event_eligibility import (
    IntelEventBusy,
    automation_event_current,
)
from app.services.intel_events import emit_intel_events
from tests.integration.test_indicator_intelligence import _extract
from tests.integration.test_investigations_api import _create_item
from tests.integration.test_team_assessments_api import (
    _queue,
    _ready,
    _review,
    assessment_setup as assessment_setup,
)


def test_delivery_fence_is_nonblocking_and_recovers_after_writer(database_engine):
    from app.models.feed import Feed
    from app.services.data_access_retention import prune_deleted_resource_envelopes

    with Session(database_engine) as db:
        item = _create_item(db)
        item_id, feed_id = item.id, item.feed_id
        events = _extract(db, item)
        db.commit()
        payload = deepcopy(db.get(IntegrationEvent, events[0]).payload_json)
    try:
        with Session(database_engine) as db, Session(database_engine) as writer:
            writer.scalar(select(Item).where(Item.id == item_id).with_for_update())
            with pytest.raises(IntelEventBusy):
                automation_event_current(
                    db, payload, "intel.extraction.ready", lock=True
                )
            assert db.scalar(text("SELECT 1")) == 1
            writer.rollback()
            assert automation_event_current(
                db, payload, "intel.extraction.ready", lock=True
            )
            db.rollback()
            item = db.get(Item, item_id)
            item.classification_required_version += 1
            db.commit()
            assert not automation_event_current(db, payload, "intel.extraction.ready")
    finally:
        with Session(database_engine) as db:
            db.execute(delete(IntegrationEvent).where(IntegrationEvent.id.in_(events)))
            prune_deleted_resource_envelopes(
                db, resources=[("integration_event", identity) for identity in events]
            )
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.commit()


def test_approval_ignores_annotations_but_rejection_and_reapproval_have_distinct_actions(
    client,
    db_session,
    auth_headers,
    assessment_setup,
):
    team, item, *_ = assessment_setup
    _extract(db_session, item)
    db_session.commit()
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    row, hunt_id = _ready(db_session, queued)
    approved = _review(
        client, assessment_setup, auth_headers["analyst"], hunt_id, version=row.version
    )
    assert approved.status_code == 200, approved.text
    events = db_session.scalars(
        select(IntegrationEvent).where(IntegrationEvent.event_type == "hunt.approved")
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.payload_json["indicators_complete"] is True
    assert automation_event_current(db_session, event.payload_json, event.event_type)
    db_session.commit()
    annotated = _review(
        client,
        assessment_setup,
        auth_headers["analyst"],
        hunt_id,
        version=approved.json()["assessment"]["version"],
        note="An additional annotation.",
    )
    assert annotated.status_code == 200, annotated.text
    db_session.expire_all()
    assert (
        len(
            db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.event_type == "hunt.approved"
                )
            ).all()
        )
        == 1
    )
    assert automation_event_current(db_session, event.payload_json, event.event_type)
    db_session.commit()
    rejected = _review(
        client,
        assessment_setup,
        auth_headers["analyst"],
        hunt_id,
        version=annotated.json()["assessment"]["version"],
        status="rejected",
    )
    assert rejected.status_code == 200, rejected.text
    assert not automation_event_current(
        db_session, event.payload_json, event.event_type
    )
    db_session.commit()
    accepted = _review(
        client,
        assessment_setup,
        auth_headers["analyst"],
        hunt_id,
        version=rejected.json()["assessment"]["version"],
    )
    assert accepted.status_code == 200, accepted.text
    events = db_session.scalars(
        select(IntegrationEvent)
        .where(IntegrationEvent.event_type == "hunt.approved")
        .order_by(IntegrationEvent.created_at)
    ).all()
    assert len(events) == 2
    assert events[0].payload_json["action_id"] != events[1].payload_json["action_id"]
    assert not automation_event_current(
        db_session, events[0].payload_json, "hunt.approved"
    )
    assert automation_event_current(db_session, events[1].payload_json, "hunt.approved")


def test_hunt_snapshot_applies_team_suppression_and_policy_changes_revoke_queue(
    client,
    db_session,
    auth_headers,
    assessment_setup,
):
    team, item, *_ = assessment_setup
    _extract(db_session, item)
    db_session.commit()
    created = client.post(
        f"/teams/{team['id']}/indicator-suppressions",
        headers=auth_headers["admin"],
        json={
            "ioc_type": "domain",
            "value": "evil.net",
            "reason": "Testing system.",
            "active": True,
        },
    )
    assert created.status_code == 201, created.text
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    row, hunt_id = _ready(db_session, queued)
    approved = _review(
        client, assessment_setup, auth_headers["analyst"], hunt_id, version=row.version
    )
    assert approved.status_code == 200, approved.text
    event = db_session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.event_type == "hunt.approved")
    )
    indicator = next(
        entry
        for entry in event.payload_json["indicators"]
        if entry["value"] == "evil.net"
    )
    assert indicator["excluded"] is True and "team_suppression" in indicator["reasons"]
    assert automation_event_current(db_session, event.payload_json, "hunt.approved")
    db_session.commit()
    changed = client.patch(
        f"/teams/{team['id']}/indicator-suppressions/{created.json()['id']}",
        headers=auth_headers["admin"],
        json={"expected_version": 1, "reason": "Testing ended.", "active": False},
    )
    assert changed.status_code == 200, changed.text
    assert not automation_event_current(db_session, event.payload_json, "hunt.approved")


def test_changed_ai_snapshot_supersedes_queued_extraction_event(
    db_session, monkeypatch
):
    item = _create_item(db_session)
    event_ids = _extract(db_session, item)
    event = db_session.get(IntegrationEvent, event_ids[0])
    assert automation_event_current(db_session, event.payload_json, event.event_type)
    monkeypatch.setattr(
        "app.services.intel_events.current_ai_indicator_links",
        lambda *_: {
            ("domain", "evil.net"): {
                "role": "reference",
                "assertion": "reported",
                "evidence": [],
            }
        },
    )
    emit_intel_events(db_session, item_id=item.id, deterministic=False)
    assert not automation_event_current(
        db_session, event.payload_json, event.event_type
    )


def test_malformed_retained_indicator_payloads_fail_closed_without_poisoning_transaction(
    db_session,
):
    item = _create_item(db_session)
    event_ids = _extract(db_session, item)
    event = db_session.get(IntegrationEvent, event_ids[0])
    original = deepcopy(event.payload_json)
    invalid_indicators = [
        None,
        {},
        [None],
        [{}],
        [{"id": "not-a-uuid", "type": "domain", "value": "evil.net"}],
        [{"id": 17, "type": "domain", "value": "evil.net"}],
        [{"id": original["indicators"][0]["id"], "type": None, "value": "evil.net"}],
        [{"id": original["indicators"][0]["id"], "type": "domain", "value": []}],
        [original["indicators"][0]] * 251,
    ]
    for indicators in invalid_indicators:
        payload = {**original, "indicators": indicators}
        assert not automation_event_current(
            db_session, payload, event.event_type, lock=True
        )
        assert db_session.scalar(text("SELECT 1")) == 1
    assert not automation_event_current(db_session, None, event.event_type, lock=True)
    assert automation_event_current(db_session, original, event.event_type, lock=True)
    assert event.payload_json == original
