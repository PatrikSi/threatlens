from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import alerts as alerts_routes
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence
from app.models.feed import Feed
from app.models.item import Item


def _seed_rule_and_item(db_session, user, *, suffix: str):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name=f"Occurrence feed {suffix}",
        url=f"https://example.com/occurrence-{suffix}.xml",
        enabled=True,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=f"occurrence-{suffix}",
        url=f"https://example.com/occurrence/{suffix}",
        canonical_url=f"https://example.com/occurrence/{suffix}",
        title=f"Fortinet occurrence {suffix}",
        summary="A matching alert item.",
        first_seen_at=now,
        dedupe_key=f"occurrence-{suffix}",
        content_hash=(suffix[0] if suffix else "a") * 64,
        status="content_fetched",
    )
    rule = AlertInterest(
        id=uuid.uuid4(),
        user_id=user.id,
        name=f"Occurrence rule {suffix}",
        category="threat",
        keywords=["fortinet"],
        enabled=True,
        severity="high",
        revision=1,
        durable_since=now - timedelta(minutes=1),
    )
    db_session.add_all([feed, item, rule])
    db_session.commit()
    return rule, item


def _seed_occurrence(db_session, user, *, suffix: str, state: str = "new"):
    rule, item = _seed_rule_and_item(db_session, user, suffix=suffix)
    occurrence = AlertOccurrence(
        id=uuid.uuid4(),
        alert_interest_id=rule.id,
        rule_id_snapshot=rule.id,
        owner_user_id=user.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=rule.revision,
        item_content_hash=item.content_hash,
        alert_name_snapshot=rule.name,
        alert_category_snapshot=rule.category,
        alert_keywords_snapshot=rule.keywords,
        matched_keywords=["fortinet"],
        source_snapshot_json={"item": {"id": str(item.id), "title": item.title}},
        severity_snapshot=rule.severity,
        lifecycle_state=state,
    )
    db_session.add(occurrence)
    db_session.commit()
    return rule, item, occurrence


def test_v1_alert_payloads_remain_valid_and_rule_changes_are_versioned(
    client: TestClient,
    auth_headers,
):
    created = client.post(
        "/alerts",
        json={
            "name": "Legacy client rule",
            "category": "Threat Watch",
            "keywords": ["Fortinet"],
            "enabled": True,
        },
        headers=auth_headers["viewer"],
    )
    assert created.status_code == 201
    payload = created.json()
    assert {
        "id",
        "user_id",
        "name",
        "category",
        "keywords",
        "enabled",
        "created_at",
        "updated_at",
    } <= set(payload)
    assert payload["name"] == "Legacy client rule"
    assert payload["category"] == "threat_watch"
    assert payload["keywords"] == ["fortinet"]
    assert payload["severity"] == "medium"
    assert payload["revision"] == 1
    assert payload["durable_since"] is not None

    listed = client.get("/alerts", headers=auth_headers["viewer"])
    assert listed.status_code == 200
    assert any(row["id"] == payload["id"] for row in listed.json())

    updated = client.patch(
        f"/alerts/{payload['id']}",
        json={"keywords": ["Fortinet", "VPN"]},
        headers=auth_headers["viewer"],
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["keywords"] == ["fortinet", "vpn"]

    legacy_noop = client.patch(
        f"/alerts/{payload['id']}",
        json={},
        headers=auth_headers["viewer"],
    )
    assert legacy_noop.status_code == 200
    assert legacy_noop.json()["revision"] == 2


def test_occurrence_lifecycle_is_owner_scoped_conflict_safe_and_bounded(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    _rule, _item, first = _seed_occurrence(
        db_session,
        seed_users["viewer"],
        suffix="viewer-one",
    )
    _second_rule, _second_item, second = _seed_occurrence(
        db_session,
        seed_users["viewer"],
        suffix="viewer-two",
    )
    _other_rule, _other_item, other = _seed_occurrence(
        db_session,
        seed_users["analyst"],
        suffix="analyst-one",
    )

    listed = client.get("/alerts/occurrences", headers=auth_headers["viewer"])
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert {row["id"] for row in listed.json()["items"]} == {
        str(first.id),
        str(second.id),
    }
    timezone_mixed = client.get(
        "/alerts/occurrences",
        params={
            "since": "2020-01-01T00:00:00",
            "until": "2030-01-01T00:00:00+00:00",
        },
        headers=auth_headers["viewer"],
    )
    assert timezone_mixed.status_code == 200
    assert timezone_mixed.json()["total"] == 2
    assert (
        client.get(
            f"/alerts/occurrences/{other.id}",
            headers=auth_headers["viewer"],
        ).status_code
        == 404
    )

    acknowledged = client.patch(
        f"/alerts/occurrences/{first.id}/lifecycle",
        json={"expected_version": 1, "state": "acknowledged"},
        headers=auth_headers["viewer"],
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["lifecycle_state"] == "acknowledged"
    assert acknowledged.json()["version"] == 2
    activity = client.get(
        f"/alerts/occurrences/{first.id}/activity",
        headers=auth_headers["viewer"],
    )
    assert activity.status_code == 200
    assert activity.json()["total"] == 1
    assert activity.json()["items"][0]["action"] == "lifecycle_changed"

    stale = client.patch(
        f"/alerts/occurrences/{first.id}/lifecycle",
        json={"expected_version": 1, "state": "investigating"},
        headers=auth_headers["viewer"],
    )
    assert stale.status_code == 409
    assert "expected version 1" in stale.json()["detail"]
    assert "current version is 2" in stale.json()["detail"]
    assert stale.json()["error"]["code"] == "alert_occurrence_version_conflict"
    assert stale.headers["x-current-version"] == "2"

    missing_disposition = client.patch(
        f"/alerts/occurrences/{first.id}/lifecycle",
        json={"expected_version": 2, "state": "closed"},
        headers=auth_headers["viewer"],
    )
    assert missing_disposition.status_code == 422

    cross_owner = client.post(
        "/alerts/occurrences/bulk/acknowledge",
        json={
            "items": [
                {"occurrence_id": str(second.id), "expected_version": 1},
                {"occurrence_id": str(other.id), "expected_version": 1},
            ]
        },
        headers=auth_headers["viewer"],
    )
    assert cross_owner.status_code == 404
    db_session.refresh(second)
    assert second.lifecycle_state == "new"
    assert second.version == 1

    oversized = client.post(
        "/alerts/occurrences/bulk/acknowledge",
        json={
            "items": [
                {"occurrence_id": str(uuid.uuid4()), "expected_version": 1}
                for _ in range(101)
            ]
        },
        headers=auth_headers["viewer"],
    )
    assert oversized.status_code == 422

    closed = client.post(
        "/alerts/occurrences/bulk/close",
        json={
            "items": [{"occurrence_id": str(second.id), "expected_version": 1}],
            "disposition": "false_positive",
        },
        headers=auth_headers["viewer"],
    )
    assert closed.status_code == 200
    assert closed.json()["items"][0]["lifecycle_state"] == "closed"
    assert closed.json()["items"][0]["closure_disposition"] == "false_positive"

    corrected_disposition = client.patch(
        f"/alerts/occurrences/{second.id}/lifecycle",
        json={
            "expected_version": 2,
            "state": "closed",
            "disposition": "true_positive",
        },
        headers=auth_headers["viewer"],
    )
    assert corrected_disposition.status_code == 200
    assert corrected_disposition.json()["closure_disposition"] == "true_positive"
    assert corrected_disposition.json()["version"] == 3


def test_snooze_is_explicit_and_independent_from_suppression(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    _rule, _item, occurrence = _seed_occurrence(
        db_session,
        seed_users["viewer"],
        suffix="snooze",
    )
    occurrence.suppressed_at = datetime.now(timezone.utc)
    occurrence.suppression_reason = "Rule maintenance"
    db_session.add(occurrence)
    db_session.commit()
    snoozed_until = datetime.now(timezone.utc) + timedelta(hours=1)

    snoozed = client.patch(
        f"/alerts/occurrences/{occurrence.id}/snooze",
        json={
            "expected_version": 1,
            "snoozed_until": snoozed_until.isoformat(),
            "reason": "Analyst follow-up tomorrow",
        },
        headers=auth_headers["viewer"],
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["is_suppressed"] is True
    assert snoozed.json()["is_snoozed"] is True
    assert snoozed.json()["suppression_reason"] == "Rule maintenance"
    assert snoozed.json()["snooze_reason"] == "Analyst follow-up tomorrow"

    cleared = client.patch(
        f"/alerts/occurrences/{occurrence.id}/snooze",
        json={"expected_version": 2, "snoozed_until": None},
        headers=auth_headers["viewer"],
    )
    assert cleared.status_code == 200
    assert cleared.json()["is_suppressed"] is True
    assert cleared.json()["is_snoozed"] is False
    activity = client.get(
        f"/alerts/occurrences/{occurrence.id}/activity",
        headers=auth_headers["viewer"],
    )
    assert activity.status_code == 200
    actions = {
        entry["action"]: entry["details_json"] for entry in activity.json()["items"]
    }
    assert actions["snoozed"]["reason"] == "Analyst follow-up tomorrow"
    assert actions["snooze_cleared"]["previous_reason"] == "Analyst follow-up tomorrow"


def test_admin_backfill_is_bounded_durable_and_never_notifying(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    _rule, item = _seed_rule_and_item(
        db_session, seed_users["viewer"], suffix="backfill"
    )
    queued: list[uuid.UUID] = []
    monkeypatch.setattr(
        alerts_routes,
        "enqueue_alert_evaluation_requests",
        lambda request_ids: queued.extend(request_ids) or True,
    )
    window = {
        "since": (item.first_seen_at - timedelta(minutes=1)).isoformat(),
        "until": (item.first_seen_at + timedelta(minutes=1)).isoformat(),
        "limit": 10,
    }

    denied = client.post(
        "/alerts/occurrences/reconciliation/preview",
        json=window,
        headers=auth_headers["analyst"],
    )
    assert denied.status_code == 403

    preview = client.post(
        "/alerts/occurrences/reconciliation/preview",
        json=window,
        headers=auth_headers["admin"],
    )
    assert preview.status_code == 200
    assert preview.json()["returned_count"] == 1
    assert preview.json()["notifications_enabled"] is False

    applied = client.post(
        "/alerts/occurrences/reconciliation/apply",
        json={"preview_token": preview.json()["preview_token"]},
        headers=auth_headers["admin"],
    )
    assert applied.status_code == 202
    assert applied.json() == {
        "accepted": 1,
        "existing": 0,
        "skipped": 0,
        "enqueue_failed": False,
        "has_more": False,
        "next_cursor_first_seen_at": None,
        "next_cursor_item_id": None,
        "notifications_enabled": False,
    }
    request = db_session.scalar(
        select(AlertEvaluationRequest).where(AlertEvaluationRequest.item_id == item.id)
    )
    assert request is not None
    assert request.source == "backfill"
    assert request.notify is False
    assert request.respect_rule_cutover is False
    assert queued == [request.id]
    duplicate_apply = client.post(
        "/alerts/occurrences/reconciliation/apply",
        json={"preview_token": preview.json()["preview_token"]},
        headers=auth_headers["admin"],
    )
    assert duplicate_apply.status_code == 409
    assert duplicate_apply.json()["error"]["code"] == "alert_backfill_preview_consumed"


def test_deleting_a_rule_preserves_owned_occurrence_history(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    rule, _item, occurrence = _seed_occurrence(
        db_session,
        seed_users["viewer"],
        suffix="delete-rule",
    )
    rule_name = rule.name

    deleted = client.delete(f"/alerts/{rule.id}", headers=auth_headers["viewer"])
    assert deleted.status_code == 204
    db_session.expire_all()
    preserved = db_session.get(AlertOccurrence, occurrence.id)
    assert preserved is not None
    assert preserved.alert_interest_id is None
    detail = client.get(
        f"/alerts/occurrences/{occurrence.id}",
        headers=auth_headers["viewer"],
    )
    assert detail.status_code == 200
    assert detail.json()["alert_name_snapshot"] == rule_name
    filtered = client.get(
        "/alerts/occurrences",
        params={"alert_interest_id": str(rule.id)},
        headers=auth_headers["viewer"],
    )
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()["items"]] == [str(occurrence.id)]
