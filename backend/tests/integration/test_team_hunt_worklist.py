from copy import deepcopy
import uuid

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.iam import IAMGroupMembership
from tests.integration.test_data_policy_read_coverage import _enable_enforcement
from tests.integration.test_team_assessments_api import (
    assessment_setup as setup_fixture,
    _queue,
    _ready,
    _review,
)

assessment_setup = setup_fixture


def _list(client, setup, headers, **params):
    return client.get(f"/teams/{setup[0]['id']}/hunts", headers=headers, params=params)


def _claim(client, setup, headers, row, hunt_id, *, version=0, action="claim"):
    return client.post(
        f"/teams/{setup[0]['id']}/hunts/{row.id}/{hunt_id}/claim",
        headers=headers,
        json={
            "action": action,
            "expected_version": version,
            "expected_assessment_version": row.version,
        },
    )


def test_queue_lists_existing_hunts_and_claim_conflicts(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt_id = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    response = _list(client, assessment_setup, auth_headers["analyst"])
    assert response.status_code == 200, response.text
    entry = response.json()["items"][0]
    assert entry["hunt"]["id"] == hunt_id and entry["status"] == "pending"
    assert entry["claim"]["version"] == 0 and entry["can_claim"]
    accepted = _claim(client, assessment_setup, auth_headers["analyst"], row, hunt_id)
    assert accepted.status_code == 200, accepted.text
    assert (
        _claim(
            client, assessment_setup, auth_headers["analyst"], row, hunt_id
        ).status_code
        == 409
    )
    assert (
        _list(
            client, assessment_setup, auth_headers["analyst"], ownership="mine"
        ).json()["items"][0]["claim"]["version"]
        == 1
    )
    assert not _list(
        client, assessment_setup, auth_headers["analyst"], ownership="unclaimed"
    ).json()["items"]
    assert (
        _claim(
            client,
            assessment_setup,
            auth_headers["analyst"],
            row,
            hunt_id,
            version=1,
            action="unclaim",
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert db_session.get(TeamHuntClaim, (row.id, hunt_id)).version == 2


def test_claim_blocks_other_reviewers_until_manager_release(
    client,
    db_session,
    auth_headers,
    assessment_setup,
):
    row, hunt_id = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    assert (
        _claim(
            client, assessment_setup, auth_headers["admin"], row, hunt_id
        ).status_code
        == 200
    )
    blocked_review = _review(
        client, assessment_setup, auth_headers["analyst"], hunt_id, version=row.version
    )
    assert blocked_review.status_code == 409, blocked_review.text
    assert blocked_review.json()["error"]["code"] == "hunt_claimed_elsewhere"
    blocked_release = _claim(
        client,
        assessment_setup,
        auth_headers["analyst"],
        row,
        hunt_id,
        version=1,
        action="unclaim",
    )
    assert blocked_release.status_code == 409
    assert (
        _claim(
            client,
            assessment_setup,
            auth_headers["admin"],
            row,
            hunt_id,
            version=1,
            action="unclaim",
        ).status_code
        == 200
    )
    assert (
        _review(
            client,
            assessment_setup,
            auth_headers["analyst"],
            hunt_id,
            version=row.version,
        ).status_code
        == 200
    )


def test_keyset_page_filters_and_reviewer_metadata(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt_id = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    result = deepcopy(row.result_json)
    result["hunts"].extend(
        {**result["hunts"][0], "id": uuid.uuid4().hex} for _ in range(4)
    )
    row.result_json = result
    db_session.commit()
    first = _list(client, assessment_setup, auth_headers["analyst"], limit=2).json()
    assert len(first["items"]) == 2 and first["has_more"]
    second = _list(
        client,
        assessment_setup,
        auth_headers["analyst"],
        limit=2,
        cursor=first["next_cursor"],
    ).json()
    assert {entry["hunt"]["id"] for entry in first["items"]}.isdisjoint(
        entry["hunt"]["id"] for entry in second["items"]
    )
    bad_scope = _list(
        client,
        assessment_setup,
        auth_headers["analyst"],
        status="accepted",
        cursor=first["next_cursor"],
    )
    assert bad_scope.status_code == 422
    reviewed = _review(
        client,
        assessment_setup,
        auth_headers["analyst"],
        hunt_id,
        version=row.version,
        status="rejected",
    )
    assert reviewed.status_code == 200, reviewed.text
    accepted = _list(
        client, assessment_setup, auth_headers["analyst"], status="rejected"
    ).json()["items"]
    assert (
        len(accepted) == 1
        and accepted[0]["reviewer_name"]
        and accepted[0]["reviewed_at"]
    )


def test_stale_evidence_is_filterable_and_not_claimable(
    client, db_session, auth_headers, assessment_setup
):
    row, hunt_id = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    item = assessment_setup[1]
    item.classification_required_version += 1
    db_session.commit()
    response = _list(client, assessment_setup, auth_headers["analyst"], status="stale")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["can_claim"] is False
    assert (
        _claim(
            client, assessment_setup, auth_headers["analyst"], row, hunt_id
        ).status_code
        == 409
    )


def test_lost_team_membership_hides_queue_and_rejects_claim(
    client, db_session, auth_headers, seed_users, assessment_setup
):
    row, hunt_id = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    db_session.query(IAMGroupMembership).filter(
        IAMGroupMembership.user_id == seed_users["analyst"].id
    ).delete()
    db_session.commit()
    assert _list(client, assessment_setup, auth_headers["analyst"]).status_code == 404
    assert (
        _claim(
            client, assessment_setup, auth_headers["analyst"], row, hunt_id
        ).status_code
        == 404
    )


def test_malformed_hunt_ids_cannot_poison_scan_cursor(
    client, db_session, auth_headers, assessment_setup
):
    row, _ = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    valid = row.result_json["hunts"][0]
    row.result_json = {
        **row.result_json,
        "hunts": [
            {**valid, "id": "a-valid"},
            {**valid, "id": "b-valid"},
            *(
                {**valid, "id": value}
                for value in (999, "z" * 81, "", None, "z/bad-id", "z bad id")
            ),
            *(
                {"id": f"z-malformed-{index}", "review_status": "suggested"}
                for index in range(5)
            ),
        ],
    }
    db_session.commit()
    first = _list(client, assessment_setup, auth_headers["analyst"], limit=1)
    assert first.status_code == 200, first.text
    assert first.json()["items"] == [] and first.json()["has_more"]
    cursor = first.json()["next_cursor"]
    second = _list(
        client, assessment_setup, auth_headers["analyst"], limit=1, cursor=cursor
    )
    assert second.status_code == 200, second.text
    assert second.json()["items"][0]["hunt"]["id"] == "b-valid"
    third = _list(
        client,
        assessment_setup,
        auth_headers["analyst"],
        limit=1,
        cursor=second.json()["next_cursor"],
    )
    assert (
        third.status_code == 200 and third.json()["items"][0]["hunt"]["id"] == "a-valid"
    )


def test_historical_evidence_labels_protect_queue_and_claim_audit(
    client,
    db_session,
    auth_headers,
    seed_users,
    assessment_setup,
    monkeypatch,
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    feed = db_session.get(Feed, assessment_setup[1].feed_id)
    feed.handling_label_id = restricted.id
    db_session.commit()
    row, hunt_id = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["admin"])
    )
    feed.handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.commit()
    hidden = _list(client, assessment_setup, auth_headers["analyst"])
    assert hidden.status_code == 200 and hidden.json()["items"] == []
    assert (
        _claim(
            client, assessment_setup, auth_headers["analyst"], row, hunt_id
        ).status_code
        == 404
    )
    accepted = _claim(client, assessment_setup, auth_headers["admin"], row, hunt_id)
    assert accepted.status_code == 200, accepted.text
    reviewed = _review(
        client, assessment_setup, auth_headers["admin"], hunt_id, version=row.version
    )
    assert reviewed.status_code == 200, reviewed.text
    records = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action.in_(["ai.hunt.claim", "ai.team_assessment.hunt_accepted"]),
            AuditLog.resource_id == str(row.item_id),
        )
    ).all()
    assert len(records) == 2
    for audit in records:
        assert audit.data_access_governed
        assert set(audit.data_access_label_ids) == {
            str(restricted.id),
            str(UNRESTRICTED_HANDLING_LABEL_ID),
        }
