"""Team ownership remains durable across evaluation, deletion and retention."""

from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import delete, select

from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceMetric
from app.models.feed import Feed
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.item import Item
from app.models.team import Team
from app.models.user import User
from app.services.alert_evaluation import (
    claim_alert_evaluation_request,
    evaluate_alert_request,
    persist_alert_evaluation_intent,
)
from app.services.alert_maintenance import maintain_alert_history


@pytest.fixture
def triage_context(db_session, seed_users):
    now = datetime.now(timezone.utc)
    group = IAMGroup(key="triage-members", name="Triage members")
    db_session.add(group)
    db_session.flush()
    team = Team(
        key="triage",
        name="Triage",
        membership_group_id=group.id,
        created_by_user_id=seed_users["viewer"].id,
    )
    managers = IAMGroup(key="triage-managers", name="Triage managers")
    db_session.add(managers)
    db_session.flush()
    team.manager_group_id = managers.id
    db_session.add_all(
        [
            IAMGroupMembership(group_id=group.id, user_id=seed_users["analyst"].id),
            IAMGroupMembership(group_id=group.id, user_id=seed_users["viewer"].id),
            IAMGroupMembership(group_id=managers.id, user_id=seed_users["admin"].id),
        ]
    )
    feed = Feed(name="Triage feed", url="https://example.com/triage.xml")
    db_session.add_all([team, feed])
    db_session.flush()
    rule = AlertInterest(
        team_id=team.id,
        name="Team watch",
        category="appliance",
        keywords=["fortinet"],
        durable_since=now - timedelta(days=1),
        due_after_minutes=60,
        escalation_after_minutes=30,
    )
    item = Item(
        feed_id=feed.id,
        source_guid="triage",
        url="https://example.com/triage",
        canonical_url="https://example.com/triage",
        title="Fortinet exploitation",
        summary="Current evidence",
        first_seen_at=now,
        dedupe_key="triage",
        content_hash="a" * 64,
        status="content_fetched",
    )
    db_session.add_all([rule, item])
    db_session.flush()
    db_session.commit()
    return now, team, group, rule, item


def _evaluate(db_session, item, now):
    intent = persist_alert_evaluation_intent(db_session, item=item)
    db_session.commit()
    claim = claim_alert_evaluation_request(db_session, request_id=intent.request_id)
    db_session.commit()
    outcome = evaluate_alert_request(
        db_session, request_id=intent.request_id, lease_token=claim.lease_token, now=now
    )
    db_session.commit()
    return outcome


def test_team_evaluation_preserves_sla_and_ownership_through_retention(
    db_session, seed_users, triage_context
):
    now, team, group, rule, item = triage_context
    intent = persist_alert_evaluation_intent(db_session, item=item)
    db_session.commit()
    # Accepted work retains its SLA even when the mutable rule is edited.
    rule.due_after_minutes = 120
    db_session.commit()
    claim = claim_alert_evaluation_request(db_session, request_id=intent.request_id)
    db_session.commit()
    outcome = evaluate_alert_request(
        db_session, request_id=intent.request_id, lease_token=claim.lease_token, now=now
    )
    db_session.commit()
    assert outcome.occurrences_created == 1
    assert not outcome.integration_event_ids
    occurrence = db_session.scalar(
        select(AlertOccurrence).where(AlertOccurrence.team_id == team.id)
    )
    assert occurrence.owner_user_id is None
    assert occurrence.due_at == now + timedelta(minutes=60)
    assert occurrence.escalation_after_minutes == 30
    occurrence_id = occurrence.id
    db_session.execute(delete(User).where(User.id == seed_users["viewer"].id))
    db_session.commit()
    assert db_session.get(AlertOccurrence, occurrence_id) is not None
    occurrence.lifecycle_state = "closed"
    occurrence.closure_disposition = "true_positive"
    occurrence.closed_at = now - timedelta(days=10)
    occurrence.created_at = now - timedelta(days=10)
    db_session.commit()
    result = maintain_alert_history(db_session, now=now, occurrence_retention_days=1)
    assert result.occurrences_aggregated == 1
    assert result.occurrences_deleted == 1
    metric = db_session.scalar(
        select(AlertOccurrenceMetric).where(AlertOccurrenceMetric.team_id == team.id)
    )
    assert metric.owner_user_id is None
    assert metric.occurrence_count == 1


def test_team_queue_claim_conflicts_and_membership_revocation(
    client, db_session, seed_users, auth_headers, triage_context
):
    now, team, group, rule, item = triage_context
    _evaluate(db_session, item, now)
    listed = client.get(
        "/alerts/occurrences",
        params={"team_id": str(team.id)},
        headers=auth_headers["analyst"],
    )
    assert listed.status_code == 200, listed.text
    occurrence = listed.json()["items"][0]
    occurrence_id = occurrence["id"]
    path = f"/alerts/occurrences/{occurrence_id}"
    # A viewer's legacy personal-alert write permission is insufficient for team writes.
    denied = client.patch(
        path + "/lifecycle",
        json={"expected_version": 1, "state": "acknowledged"},
        headers=auth_headers["viewer"],
    )
    assert denied.status_code == 403, denied.text
    claimed = client.patch(
        path + "/assignment",
        json={"expected_version": 1, "action": "claim"},
        headers=auth_headers["analyst"],
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["assignee_user_id"] == str(seed_users["analyst"].id)
    assert (
        client.patch(
            path + "/assignment",
            json={"expected_version": 1, "action": "claim"},
            headers=auth_headers["admin"],
        ).status_code
        == 409
    )
    assert (
        client.patch(
            path + "/assignment",
            json={"expected_version": 2, "action": "claim"},
            headers=auth_headers["admin"],
        ).json()["error"]["code"]
        == "alert_already_claimed"
    )
    forbidden_assignment = client.patch(
        path + "/assignment",
        json={
            "expected_version": 2,
            "action": "assign",
            "assignee_user_id": str(seed_users["admin"].id),
        },
        headers=auth_headers["analyst"],
    )
    assert forbidden_assignment.status_code == 403
    # Former members lose listing, evidence and mutation access immediately.
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.group_id == group.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.commit()
    assert client.get(path, headers=auth_headers["analyst"]).status_code == 404
    assert (
        client.get("/alerts/occurrences", headers=auth_headers["analyst"]).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.patch(
            path + "/assignment",
            json={"expected_version": 2, "action": "unclaim"},
            headers=auth_headers["analyst"],
        ).status_code
        == 404
    )
    reassigned = client.patch(
        path + "/assignment",
        json={
            "expected_version": 2,
            "action": "assign",
            "assignee_user_id": str(seed_users["admin"].id),
        },
        headers=auth_headers["admin"],
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["assignee_user_id"] == str(seed_users["admin"].id)


def test_team_watchlists_require_versions_and_credential_ceiling(
    client, db_session, auth_headers, seed_users, triage_context
):
    from app.core.security import generate_api_token
    from app.models.api_token import ApiToken

    now, team, group, rule, item = triage_context
    token, prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=seed_users["analyst"].id,
            name="limited",
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=["read:alerts", "write:alerts", "read:items"],
        )
    )
    db_session.commit()
    payload = {
        "team_id": str(team.id),
        "name": "Shared watch",
        "category": "team",
        "keywords": ["cve"],
        "due_after_minutes": 30,
        "escalation_after_minutes": 0,
    }
    assert (
        client.post(
            "/alerts", json=payload, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )
    created = client.post("/alerts", json=payload, headers=auth_headers["analyst"])
    assert created.status_code == 201, created.text
    assert created.json()["user_id"] is None
    path = f"/alerts/{created.json()['id']}"
    assert (
        client.patch(
            path, json={"name": "No baseline"}, headers=auth_headers["analyst"]
        ).status_code
        == 422
    )
    saved = client.patch(
        path,
        json={"name": "Versioned", "expected_row_version": 1},
        headers=auth_headers["analyst"],
    )
    assert saved.status_code == 200, saved.text
    assert (
        client.patch(
            path,
            json={"name": "Stale", "expected_row_version": 1},
            headers=auth_headers["analyst"],
        ).status_code
        == 409
    )
    assert client.delete(path, headers=auth_headers["analyst"]).status_code == 422
    assert (
        client.delete(
            path, params={"expected_row_version": 2}, headers=auth_headers["analyst"]
        ).status_code
        == 204
    )


def test_deadline_escalation_is_bounded_idempotent_and_respects_snooze(
    client, db_session, auth_headers, triage_context
):
    from app.models.alert_occurrence import AlertOccurrenceActivity
    from app.services.alert_triage import escalate_overdue_alerts

    now, team, group, rule, item = triage_context
    _evaluate(db_session, item, now)
    occurrence = db_session.scalar(
        select(AlertOccurrence).where(AlertOccurrence.team_id == team.id)
    )
    path = f"/alerts/occurrences/{occurrence.id}/deadline"
    payload = {
        "expected_version": 1,
        "due_at": (now - timedelta(minutes=1)).isoformat(),
        "escalation_after_minutes": 0,
    }
    assert (
        client.patch(path, json=payload, headers=auth_headers["analyst"]).status_code
        == 403
    )
    changed = client.patch(path, json=payload, headers=auth_headers["admin"])
    assert changed.status_code == 200, changed.text
    db_session.refresh(occurrence)
    occurrence.snoozed_until = now + timedelta(minutes=5)
    occurrence.snooze_reason = "Investigating another incident"
    db_session.commit()
    assert escalate_overdue_alerts(db_session, now=now) == 0
    assert (
        escalate_overdue_alerts(db_session, now=now + timedelta(minutes=5), limit=1)
        == 1
    )
    assert escalate_overdue_alerts(db_session, now=now + timedelta(minutes=6)) == 0
    db_session.commit()
    assert (
        len(
            db_session.scalars(
                select(AlertOccurrenceActivity).where(
                    AlertOccurrenceActivity.occurrence_id == occurrence.id,
                    AlertOccurrenceActivity.action == "escalated",
                )
            ).all()
        )
        == 1
    )
    overdue = client.get(
        "/alerts/occurrences", params={"escalated": True}, headers=auth_headers["admin"]
    )
    assert overdue.json()["total"] == 1


def test_team_metrics_are_scoped_before_and_after_retention(
    client, db_session, auth_headers, seed_users, triage_context
):
    now, team, group, rule, item = triage_context
    _evaluate(db_session, item, now)
    path = "/alerts/occurrences/metrics"
    params = {"team_id": str(team.id)}
    assert client.get(path, headers=auth_headers["analyst"]).json()["items"] == []
    live = client.get(path, params=params, headers=auth_headers["analyst"])
    assert live.status_code == 200, live.text
    assert sum(row["occurrence_count"] for row in live.json()["items"]) == 1
    assert live.json()["items"][0]["owner_user_id"] is None
    occurrence = db_session.scalar(
        select(AlertOccurrence).where(AlertOccurrence.team_id == team.id)
    )
    occurrence.lifecycle_state = "closed"
    occurrence.closure_disposition = "true_positive"
    occurrence.closed_at = now - timedelta(days=10)
    occurrence.created_at = now - timedelta(days=10)
    db_session.commit()
    maintain_alert_history(db_session, now=now, occurrence_retention_days=1)
    historical = client.get(path, params=params, headers=auth_headers["analyst"])
    assert historical.status_code == 200, historical.text
    assert sum(row["occurrence_count"] for row in historical.json()["items"]) == 1
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.group_id == group.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.commit()
    assert (
        client.get(path, params=params, headers=auth_headers["analyst"]).json()["items"]
        == []
    )


def test_watchlist_deadline_only_edits_advance_compatibility_versions(
    client, auth_headers, triage_context
):
    _now, _team, _group, rule, _item = triage_context
    path = f"/alerts/{rule.id}"
    updated = client.patch(
        path,
        json={"expected_row_version": 1, "due_after_minutes": 90},
        headers=auth_headers["analyst"],
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["due_after_minutes"] == 90
    assert updated.json()["revision"] == updated.json()["row_version"] == 2
    cleared = client.patch(
        path,
        json={"expected_row_version": 2, "due_after_minutes": None},
        headers=auth_headers["analyst"],
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["due_after_minutes"] is None
    assert cleared.json()["escalation_after_minutes"] is None
    assert cleared.json()["revision"] == cleared.json()["row_version"] == 3
