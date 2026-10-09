"""Reviewed export approval, immutable identities and subsequent withdrawals."""

import base64
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest
from sqlalchemy import delete, select
from stix2 import parse

from app.models.indicator_publication import IndicatorPublication
from app.models.audit_log import AuditLog
from app.models.iam import IAMGroupMembership
from app.models.intel_assessment import IndicatorAssessment
from tests.integration.test_indicator_intelligence import _command, _page, intel_setup  # noqa: F401


@pytest.fixture()
def reviewed(client, intel_setup, auth_headers):  # noqa: F811
    team, item, *_ = intel_setup
    page = _page(client, intel_setup, auth_headers["analyst"])
    ioc = next(row for row in page["items"] if row["value"] == "evil.net")
    response = client.patch(f"/items/{item.id}/indicators/{ioc['id']}/assessment?team_id={team['id']}",
                            json=_command(page), headers=auth_headers["analyst"])
    assert response.status_code == 200, response.text
    return team, item, ioc, page


def preview(client, reviewed, auth_headers):
    team, item, *_ = reviewed
    filters = {"feed_ids": [str(item.feed_id)]}
    path = f"/teams/{team['id']}/indicator-publications"
    response = client.post(path + "/preview", json={"filters": filters}, headers=auth_headers["analyst"])
    assert response.status_code == 200, response.text
    return path, filters, response.json()


def publish(client, reviewed, auth_headers, format="stix"):
    path, filters, value = preview(client, reviewed, auth_headers)
    body = {"filters": filters, "format": format, "idempotency_key": str(uuid.uuid4()),
            "preview_fingerprint": value["fingerprint"]}
    response = client.post(path, json=body, headers=auth_headers["analyst"])
    assert response.status_code == 201, response.text
    return path, body, response.json()


@pytest.mark.parametrize("format", ["stix", "misp"])
def test_reviewed_publication_has_stable_ids_and_explicit_detection_approval(client, reviewed, auth_headers, format):
    path, body, value = publish(client, reviewed, auth_headers, format)
    assert value["indicator_count"] == 1
    assert client.post(path, json=body, headers=auth_headers["analyst"]).json()["id"] == value["id"]
    download = client.get(f"{path}/{value['id']}/download", headers=auth_headers["analyst"])
    assert download.status_code == 200, download.text
    assert download.headers["cache-control"] == "no-store"
    first = download.content
    assert client.get(f"{path}/{value['id']}/download", headers=auth_headers["analyst"]).content == first
    if format == "stix":
        bundle = parse(download.text, allow_custom=True)
        indicators = [entry for entry in bundle.objects if entry.type == "indicator"]
        assert len(indicators) == 1 and "evil.net" in indicators[0].pattern
        assert not indicators[0].revoked
        assert indicators[0].x_threatlens_review["evidence"]
    else:
        attributes = download.json()["response"][0]["Event"]["Attribute"]
        assert len(attributes) == 1 and attributes[0]["to_ids"] is True
        assert attributes[0]["value"] == "evil.net"
    conflict = client.post(path, json={**body, "marking": "TLP:RED"}, headers=auth_headers["analyst"])
    assert conflict.status_code == 409


def test_changed_review_requires_new_preview(client, reviewed, auth_headers):
    path, filters, value = preview(client, reviewed, auth_headers)
    team, item, ioc, page = reviewed
    response = client.patch(f"/items/{item.id}/indicators/{ioc['id']}/assessment?team_id={team['id']}",
                            json=_command(page, version=1, verdict="reference"), headers=auth_headers["analyst"])
    assert response.status_code == 200
    saved = client.post(path, json={"filters": filters, "format": "stix", "idempotency_key": str(uuid.uuid4()),
                                    "preview_fingerprint": value["fingerprint"]}, headers=auth_headers["analyst"])
    assert saved.status_code == 409 and saved.json()["error"]["code"] == "publication_preview_changed"


@pytest.mark.parametrize("change", ["verdict", "expiry", "suppression", "source"])
def test_published_indicator_withdraws_monotonically(client, reviewed, auth_headers, db_session, change):
    path, _, published = publish(client, reviewed, auth_headers)
    team, item, ioc, page = reviewed
    if change == "verdict":
        response = client.patch(f"/items/{item.id}/indicators/{ioc['id']}/assessment?team_id={team['id']}",
                                json=_command(page, version=1, verdict="retracted"), headers=auth_headers["analyst"])
        assert response.status_code == 200
    elif change == "expiry":
        review = db_session.scalar(select(IndicatorAssessment).where(IndicatorAssessment.item_id == item.id))
        review.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
    elif change == "source":
        item.classification_required_version += 1
        db_session.commit()
    else:
        response = client.post(f"/teams/{team['id']}/indicator-suppressions", headers=auth_headers["admin"],
                               json={"ioc_type": "domain", "value": "evil.net", "reason": "Approved reference", "active": True})
        assert response.status_code == 201, response.text
    result = client.get(f"{path}/{published['id']}/download", headers=auth_headers["analyst"])
    assert result.status_code == 200, result.text
    indicators = [entry for entry in result.json()["objects"] if entry["type"] == "indicator"]
    assert indicators[0]["revoked"] is True
    db_session.expire_all()
    row = db_session.get(IndicatorPublication, uuid.UUID(published["id"]))
    assert row.status == "withdrawn" and row.revision == 2
    before = row.snapshot_json
    client.get(f"{path}/{published['id']}/download", headers=auth_headers["analyst"])
    db_session.refresh(row)
    assert row.snapshot_json == before and row.revision == 2


def test_publication_lists_are_paged_and_team_scoped(client, reviewed, auth_headers, db_session, seed_users):
    path, _, first = publish(client, reviewed, auth_headers)
    _, _, second = publish(client, reviewed, auth_headers)
    response = client.get(path + "?limit=1", headers=auth_headers["analyst"])
    assert response.status_code == 200, response.text
    assert response.json()["has_more"] and response.json()["items"][0]["id"] == second["id"]
    cursor = response.json()["next_cursor"]
    following = client.get(path, params={"limit": 1, "cursor": cursor}, headers=auth_headers["analyst"])
    assert following.json()["items"][0]["id"] == first["id"]
    assert not following.json()["has_more"]
    assert client.get(path + "?cursor=not-valid", headers=auth_headers["analyst"]).status_code == 400
    for identity in ({}, [], 123, True, None):
        malformed = base64.urlsafe_b64encode(json.dumps({
            "team": reviewed[0]["id"],
            "at": datetime.now(timezone.utc).isoformat(),
            "id": identity,
        }).encode()).decode().rstrip("=")
        response = client.get(path, params={"cursor": malformed}, headers=auth_headers["analyst"])
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "publication_cursor_invalid"
    # The shared fixture includes the viewer in this team. Revoke that membership.
    db_session.execute(delete(IAMGroupMembership).where(IAMGroupMembership.user_id == seed_users["viewer"].id))
    db_session.commit()
    assert client.get(path, headers=auth_headers["viewer"]).status_code in {403, 404}
    assert client.get(f"{path}/{first["id"]}/download", headers=auth_headers["viewer"]).status_code in {403, 404}


def test_operator_withdrawal_is_versioned_idempotent_and_governed(client, reviewed, auth_headers, db_session):
    path, _, published = publish(client, reviewed, auth_headers, "misp")
    url = f"{path}/{published['id']}/withdraw"
    stale = client.post(url, json={"expected_revision": 9}, headers=auth_headers["analyst"])
    assert stale.status_code == 409
    for _ in range(2):
        response = client.post(url, json={"expected_revision": 1}, headers=auth_headers["analyst"])
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 2 and response.json()["status"] == "withdrawn"
    artifact = client.get(f"{path}/{published['id']}/download", headers=auth_headers["analyst"])
    attribute = artifact.json()["response"][0]["Event"]["Attribute"][0]
    assert attribute["deleted"] is True and attribute["to_ids"] is False
    audits = db_session.scalars(select(AuditLog).where(AuditLog.resource_type == "indicator_publication")).all()
    assert {row.action for row in audits} == {"intelligence.publication.create", "intelligence.publication.withdraw"}
    assert len(audits) == 2
    assert all(row.data_access_governed and row.data_access_label_ids for row in audits)


def test_manual_withdrawal_does_not_conflict_with_undiscovered_partial_expiry(
    client, intel_setup, auth_headers, db_session,  # noqa: F811
):
    from tests.integration.test_indicator_intelligence import _extract

    team, item, *_ = intel_setup
    _extract(db_session, item, "Threat infrastructure: evil[.]net and malicious[.]net.")
    db_session.commit()
    page = _page(client, intel_setup, auth_headers["analyst"])
    for indicator in page["items"]:
        reviewed = client.patch(
            f"/items/{item.id}/indicators/{indicator['id']}/assessment?team_id={team['id']}",
            json=_command(page), headers=auth_headers["analyst"],
        )
        assert reviewed.status_code == 200, reviewed.text
    path, _, published = publish(client, (team, item), auth_headers)
    assert published["indicator_count"] == 2
    review = db_session.scalars(select(IndicatorAssessment).where(IndicatorAssessment.item_id == item.id)).first()
    review.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    # History has not reconciled this newly expired entry yet. The visible
    # revision still identifies the exact publication the user can withdraw.
    listed = client.get(path, headers=auth_headers["analyst"])
    assert listed.json()["items"][0]["revision"] == 1
    withdrawn = client.post(
        f"{path}/{published['id']}/withdraw", json={"expected_revision": 1},
        headers=auth_headers["analyst"],
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert withdrawn.json()["revision"] == 2
    assert withdrawn.json()["withdrawn_count"] == 2


def test_withdrawal_audit_retains_current_and_historical_handling_labels(
    client, reviewed, auth_headers, db_session, seed_users, monkeypatch,
):
    from app.models.feed import Feed
    from tests.integration.test_data_policy_read_coverage import _enable_enforcement

    path, _, published = publish(client, reviewed, auth_headers)
    publication = db_session.get(IndicatorPublication, uuid.UUID(published["id"]))
    captured_labels = {label for entry in publication.snapshot_json["indicators"] for label in entry["label_ids"]}
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    feed = db_session.get(Feed, reviewed[1].feed_id)
    feed.handling_label_id = restricted.id
    db_session.commit()
    withdrawn = client.post(
        f"{path}/{published['id']}/withdraw", json={"expected_revision": 1},
        headers=auth_headers["admin"],
    )
    assert withdrawn.status_code == 200, withdrawn.text
    audit = db_session.scalar(select(AuditLog).where(
        AuditLog.action == "intelligence.publication.withdraw",
        AuditLog.resource_id == published["id"],
    ))
    assert audit.data_access_governed
    assert str(restricted.id) not in captured_labels
    assert set(audit.data_access_label_ids) == captured_labels | {str(restricted.id)}


def test_publication_survives_source_retention_but_withholds_evidence(client, reviewed, auth_headers, db_session):
    from app.models.item import Item

    path, _, published = publish(client, reviewed, auth_headers)
    db_session.execute(delete(Item).where(Item.id == reviewed[1].id))
    db_session.commit()
    assert client.get(path, headers=auth_headers["analyst"]).json()["items"] == []
    assert client.get(f"{path}/{published['id']}/download", headers=auth_headers["analyst"]).status_code == 404
    assert db_session.get(IndicatorPublication, uuid.UUID(published["id"])) is not None


def test_retained_publication_blocks_archiving_its_historical_handling_label(
    client, intel_setup, auth_headers, db_session, seed_users, monkeypatch,  # noqa: F811
):
    from app.models.audit_log import AuditLogDataAccessLabel
    from app.models.data_policy import (
        DataAccessEnvelope,
        DataAccessEnvelopeLabel,
        UNRESTRICTED_HANDLING_LABEL_ID,
    )
    from app.models.feed import Feed
    from app.models.item import Item
    from app.schemas.data_policy import HandlingLabelStatusRequest
    from app.services.data_access_policy import DataPolicyConflict, set_handling_label_status
    from tests.integration.test_data_policy_read_coverage import _enable_enforcement
    from tests.integration.test_indicator_intelligence import _extract

    team, item, *_ = intel_setup
    label = _enable_enforcement(db_session, seed_users, monkeypatch)
    feed = db_session.get(Feed, item.feed_id)
    feed.handling_label_id = label.id
    item.classification_required_version += 1
    db_session.flush()
    _extract(db_session, item)
    db_session.commit()
    page = _page(client, intel_setup, auth_headers["admin"])
    indicator = next(entry for entry in page["items"] if entry["value"] == "evil.net")
    approved = client.patch(
        f"/items/{item.id}/indicators/{indicator['id']}/assessment?team_id={team['id']}",
        json=_command(page), headers=auth_headers["admin"],
    )
    assert approved.status_code == 200, approved.text
    path, _, publication = publish(
        client, (team, item), {"analyst": auth_headers["admin"]},
    )
    withdrawn = client.post(
        f"{path}/{publication['id']}/withdraw", json={"expected_revision": 1},
        headers=auth_headers["admin"],
    )
    assert withdrawn.status_code == 200, withdrawn.text

    # Source retention and shorter-lived derived/audit history can leave only
    # the publication's immutable historical access boundary behind.
    feed.handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.execute(delete(Item).where(Item.id == item.id))
    envelope_ids = select(DataAccessEnvelopeLabel.envelope_id).where(
        DataAccessEnvelopeLabel.label_id == label.id,
    )
    db_session.execute(delete(DataAccessEnvelope).where(DataAccessEnvelope.id.in_(envelope_ids)))
    audit_ids = select(AuditLogDataAccessLabel.audit_log_id).where(
        AuditLogDataAccessLabel.label_id == label.id,
    )
    db_session.execute(delete(AuditLog).where(AuditLog.id.in_(audit_ids)))
    db_session.commit()
    assert db_session.get(IndicatorPublication, uuid.UUID(publication["id"])) is not None

    with pytest.raises(DataPolicyConflict, match="retained by derived intelligence"):
        set_handling_label_status(
            db_session, label_id=label.id,
            payload=HandlingLabelStatusRequest(expected_revision=label.revision, active=False),
            actor_user_id=seed_users["admin"].id,
        )
    assert label.is_active is True

    db_session.execute(delete(IndicatorPublication).where(
        IndicatorPublication.id == uuid.UUID(publication["id"]),
    ))
    archived = set_handling_label_status(
        db_session, label_id=label.id,
        payload=HandlingLabelStatusRequest(expected_revision=label.revision, active=False),
        actor_user_id=seed_users["admin"].id,
    )
    assert archived.changed is True and archived.label.is_active is False


def test_reconciliation_prunes_only_old_withdrawn_history(client, reviewed, auth_headers, db_session):
    from app.services.indicator_publication_refresh import reconcile_publications

    path, _, active = publish(client, reviewed, auth_headers)
    _, _, withdrawn = publish(client, reviewed, auth_headers)
    client.post(f"{path}/{withdrawn['id']}/withdraw", json={"expected_revision": 1}, headers=auth_headers["analyst"])
    for identifier in (active["id"], withdrawn["id"]):
        row = db_session.get(IndicatorPublication, uuid.UUID(identifier))
        db_session.refresh(row)
        row.updated_at = datetime.now(timezone.utc) - timedelta(days=181)
    db_session.commit()
    reconcile_publications(db_session)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(IndicatorPublication, uuid.UUID(active["id"])) is not None
    assert db_session.get(IndicatorPublication, uuid.UUID(withdrawn["id"])) is None


def test_relabeling_never_removes_publication_historical_access_boundary(
    client, intel_setup, db_session, seed_users, auth_headers, monkeypatch,  # noqa: F811
):
    from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
    from app.models.feed import Feed
    from tests.integration.test_data_policy_read_coverage import _enable_enforcement
    from tests.integration.test_indicator_intelligence import _extract

    team, item, *_ = intel_setup
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    feed = db_session.get(Feed, item.feed_id)
    feed.handling_label_id = restricted.id
    item.classification_required_version += 1
    db_session.flush()
    _extract(db_session, item)
    db_session.commit()
    page = _page(client, intel_setup, auth_headers["admin"])
    ioc = next(row for row in page["items"] if row["value"] == "evil.net")
    reviewed = client.patch(f"/items/{item.id}/indicators/{ioc['id']}/assessment?team_id={team['id']}",
                            json=_command(page), headers=auth_headers["admin"])
    assert reviewed.status_code == 200, reviewed.text
    path, _, published = publish(client, (team, item, ioc, page), {"analyst": auth_headers["admin"]})
    feed.handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    item.classification_required_version += 1
    db_session.flush()
    _extract(db_session, item)
    db_session.commit()
    assert client.get(path, headers=auth_headers["analyst"]).json()["items"] == []
    assert client.get(f"{path}/{published['id']}/download", headers=auth_headers["analyst"]).status_code == 404
    admin = client.get(f"{path}/{published['id']}/download", headers=auth_headers["admin"])
    assert admin.status_code == 200, admin.text
    assert next(entry for entry in admin.json()["objects"] if entry["type"] == "indicator")["revoked"] is True


def test_publication_evidence_lookup_filters_both_records_and_count(client, reviewed, intel_setup, auth_headers):  # noqa: F811
    team, item, indicator, original = reviewed
    assert original["total"] > 1
    path = f"/items/{item.id}/indicators"
    params = {"team_id": team["id"], "ioc_id": indicator["id"], "page_size": 1}
    response = client.get(path, params=params, headers=auth_headers["analyst"])
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    assert [row["id"] for row in page["items"]] == [indicator["id"]]
    assert page["items"][0]["evidence"]
    assert page["items"][0]["assessment"]["current"] is True
    assert page["source_revision"] == original["source_revision"]
    assert page["extraction_revision"] == original["extraction_revision"]
    second = client.get(path, params={**params, "page": 2}, headers=auth_headers["analyst"]).json()
    assert second["total"] == 1 and second["items"] == []
    missing = client.get(path, params={**params, "ioc_id": str(uuid.uuid4())}, headers=auth_headers["analyst"]).json()
    assert missing["total"] == 0 and missing["items"] == []
    assert _page(client, intel_setup, auth_headers["analyst"])["total"] == original["total"]
    assert client.get(path, params={**params, "ioc_id": "not-a-uuid"}, headers=auth_headers["analyst"]).status_code == 422


def test_filtered_evidence_still_requires_current_team_membership(client, reviewed, auth_headers, db_session, seed_users):
    team, item, indicator, _ = reviewed
    path = f"/items/{item.id}/indicators"
    params = {"team_id": team["id"], "ioc_id": indicator["id"], "page_size": 1}
    assert client.get(path, params=params, headers=auth_headers["viewer"]).status_code == 200
    db_session.execute(delete(IAMGroupMembership).where(IAMGroupMembership.user_id == seed_users["viewer"].id))
    db_session.commit()
    response = client.get(path, params=params, headers=auth_headers["viewer"])
    assert response.status_code in {403, 404}
    assert "evil.net" not in response.text
