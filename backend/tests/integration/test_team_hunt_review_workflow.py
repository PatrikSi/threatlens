from copy import deepcopy
from datetime import datetime, timedelta, timezone
import uuid
from app.models.team_hunt_claim import TeamHuntClaim
from app.services.team_hunt_reminders import dispatch_hunt_review_reminders
from tests.integration.test_team_assessments_api import (
    assessment_setup as setup_fixture,
    _queue,
    _ready,
    _review,
)
from tests.integration.test_team_hunt_worklist import _list, _claim

assessment_setup = setup_fixture


def _schedule(client, setup, headers, row, hunt_id, **changes):
    return client.patch(
        f"/teams/{setup[0]['id']}/hunts/{row.id}/{hunt_id}/schedule",
        headers=headers,
        json={
            "expected_version": 0,
            "expected_assessment_version": row.version,
            "priority": "high",
            "due_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            **changes,
        },
    )


def test_review_deadline_is_versioned_and_does_not_claim_execution(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    saved = _schedule(client, assessment_setup, auth_headers["analyst"], row, hunt)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1 and saved.json()["overdue"]
    assert (
        _schedule(
            client, assessment_setup, auth_headers["analyst"], row, hunt
        ).status_code
        == 409
    )
    entry = _list(
        client, assessment_setup, auth_headers["analyst"], overdue=True, priority="high"
    ).json()["items"][0]
    assert entry["claim"]["owner_user_id"] is None
    assert entry["review_schedule"]["priority"] == "high" and entry["can_schedule"]
    assert not _list(
        client, assessment_setup, auth_headers["analyst"], priority="urgent"
    ).json()["items"]


def test_owner_and_manager_control_schedule_and_promoted_hunts_use_investigation(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    assert (
        _claim(client, assessment_setup, auth_headers["admin"], row, hunt).status_code
        == 200
    )
    assert (
        _schedule(
            client, assessment_setup, auth_headers["analyst"], row, hunt
        ).status_code
        == 409
    )
    assert (
        _schedule(
            client, assessment_setup, auth_headers["admin"], row, hunt
        ).status_code
        == 200
    )
    accepted = _review(
        client, assessment_setup, auth_headers["admin"], hunt, version=row.version
    )
    assert accepted.status_code == 200
    row.version = accepted.json()["assessment"]["version"]
    assert (
        _schedule(
            client,
            assessment_setup,
            auth_headers["admin"],
            row,
            hunt,
            expected_version=1,
        ).status_code
        == 409
    )
    assert not _list(
        client, assessment_setup, auth_headers["admin"], overdue=True
    ).json()["items"]


def test_reminders_are_durable_deduplicated_and_acknowledgements_idempotent(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    assert (
        _schedule(
            client, assessment_setup, auth_headers["analyst"], row, hunt
        ).status_code
        == 200
    )
    assert dispatch_hunt_review_reminders(db_session) == 1
    db_session.commit()
    assert dispatch_hunt_review_reminders(db_session) == 0
    db_session.commit()
    url = f"/teams/{assessment_setup[0]['id']}/hunts/{row.id}/{hunt}/reminder-acknowledgement"
    payload = {"expected_version": 1, "expected_assessment_version": row.version}
    ack = client.post(url, headers=auth_headers["analyst"], json=payload)
    assert ack.status_code == 200 and ack.json()["reminder_acknowledged_at"], ack.text
    again = client.post(url, headers=auth_headers["analyst"], json=payload)
    assert (
        again.json()["reminder_acknowledged_at"]
        == ack.json()["reminder_acknowledged_at"]
    )
    assert dispatch_hunt_review_reminders(db_session) == 0
    db_session.commit()
    assert (
        _schedule(
            client,
            assessment_setup,
            auth_headers["analyst"],
            row,
            hunt,
            expected_version=1,
        ).status_code
        == 200
    )
    assert dispatch_hunt_review_reminders(db_session) == 1


def test_oldest_and_due_cursor_scopes_and_invalid_dates(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    data = deepcopy(row.result_json)
    for key in ["aaa", "zzz"]:
        data["hunts"].append({**deepcopy(data["hunts"][0]), "id": key})
    row.result_json = data
    db_session.commit()
    ids = sorted(entry["id"] for entry in data["hunts"])
    first = _list(
        client, assessment_setup, auth_headers["analyst"], order="oldest", limit=1
    ).json()
    assert first["items"][0]["hunt"]["id"] == ids[0]
    second = _list(
        client,
        assessment_setup,
        auth_headers["analyst"],
        order="oldest",
        limit=1,
        cursor=first["next_cursor"],
    ).json()
    assert second["items"][0]["hunt"]["id"] == ids[1]
    assert (
        _list(
            client,
            assessment_setup,
            auth_headers["analyst"],
            order="newest",
            cursor=first["next_cursor"],
        ).status_code
        == 422
    )
    assert (
        _schedule(
            client, assessment_setup, auth_headers["analyst"], row, "zzz"
        ).status_code
        == 200
    )
    assert (
        _list(
            client, assessment_setup, auth_headers["analyst"], order="due", limit=1
        ).json()["items"][0]["hunt"]["id"]
        == "zzz"
    )
    assert (
        _schedule(
            client,
            assessment_setup,
            auth_headers["analyst"],
            row,
            hunt,
            due_at="2026-10-01T00:00:00",
        ).status_code
        == 422
    )


def test_reminder_scan_skips_deleted_hunt_without_starving_later_deadlines(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    for i in range(105):
        db_session.add(
            TeamHuntClaim(
                assessment_id=row.id,
                hunt_id=f"removed-{i}",
                review_due_at=datetime.now(timezone.utc) - timedelta(days=1),
                review_version=1,
            )
        )
    db_session.commit()
    assert (
        _schedule(
            client, assessment_setup, auth_headers["analyst"], row, hunt
        ).status_code
        == 200
    )
    assert dispatch_hunt_review_reminders(db_session, limit=1) == 1
    assert db_session.get(TeamHuntClaim, (row.id, hunt)).reminded_at is not None


def test_saved_filters_require_manager_preserve_versions_and_retry_ids(
    client, auth_headers, assessment_setup
):
    team = assessment_setup[0]["id"]
    url = f"/teams/{team}/hunts/views/{uuid.uuid4()}"
    payload = {
        "expected_version": 0,
        "name": "Urgent overdue",
        "filters": {"order": "oldest", "overdue": True, "priority": "urgent"},
    }
    assert (
        client.put(url, headers=auth_headers["analyst"], json=payload).status_code
        == 404
    )
    created = client.put(url, headers=auth_headers["admin"], json=payload)
    assert created.status_code == 200, created.text
    again = client.put(url, headers=auth_headers["admin"], json=payload)
    assert again.json()["version"] == 1
    page = client.get(f"/teams/{team}/hunts/views", headers=auth_headers["viewer"])
    assert page.status_code == 200 and not page.json()["can_manage"]
    updated = client.put(
        url,
        headers=auth_headers["admin"],
        json={**payload, "expected_version": 1, "name": "Updated"},
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        client.delete(
            url + "?expected_version=1", headers=auth_headers["admin"]
        ).status_code
        == 409
    )
    assert (
        client.delete(
            url + "?expected_version=2", headers=auth_headers["admin"]
        ).status_code
        == 204
    )
