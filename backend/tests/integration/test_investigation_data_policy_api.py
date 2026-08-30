from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.alert_occurrence import AlertOccurrence
from app.models.audit_log import AuditLog
from app.models.data_policy import (
    DataPolicyRoleGrant,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.investigation import (
    Investigation,
    InvestigationActivity,
    InvestigationEvidence,
    InvestigationMember,
    InvestigationNote,
)
from app.models.item import Item
from app.models.ioc import IOC, ItemIOC
from app.models.report import Report
from app.models.report_source_item import ReportSourceItem
from app.services import data_access_policy, investigation_evidence
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    get_data_access_envelope,
)
from app.services.data_access_runtime import (
    ensure_alert_occurrence_data_access_envelope,
    ensure_report_data_access_envelope,
)


def _create_investigation(client, headers, *, title: str) -> dict:
    response = client.post(
        "/investigations",
        headers=headers,
        json={
            "title": title,
            "description": "Investigation data-policy integration coverage.",
            "severity": "high",
            "visibility": "private",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _add_admin_owner(client, auth_headers, seed_users, investigation: dict) -> dict:
    response = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "owner",
            "expected_version": investigation["version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _problem_without_request_id(response) -> dict:
    payload = response.json()
    payload["error"].pop("request_id", None)
    return payload


def _create_restricted_label(db_session, seed_users) -> HandlingLabel:
    label = HandlingLabel(
        key=f"investigation-restricted-{uuid.uuid4().hex[:10]}",
        name="Restricted investigation evidence",
        description="Restricted integration-test material.",
        color="#B91C1C",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
        created_by_user_id=seed_users["admin"].id,
        updated_by_user_id=seed_users["admin"].id,
    )
    db_session.add(label)
    db_session.flush()
    db_session.add(
        DataPolicyRoleGrant(
            label_id=label.id,
            role_id=SYSTEM_ROLE_IDS["admin"],
            granted_by_user_id=seed_users["admin"].id,
        )
    )
    db_session.flush()
    return label


def _set_policy_mode(
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
) -> None:
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = mode
    state.coverage_version = 1
    state.revision += 1
    state.enforced_at = datetime.now(timezone.utc) if mode == "enforced" else None
    state.enforced_by_user_id = seed_users["admin"].id if mode == "enforced" else None
    state.updated_by_user_id = seed_users["admin"].id
    db_session.add(state)
    db_session.commit()
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )


def _create_item(db_session, label: HandlingLabel, *, title: str) -> tuple[Feed, Item]:
    unique = uuid.uuid4().hex
    feed = Feed(
        name=f"{title} feed",
        url=f"https://example.com/{unique}.xml",
        handling_label_id=label.id,
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        source_guid=f"investigation-policy-{unique}",
        url=f"https://example.com/articles/{unique}",
        canonical_url=f"https://example.com/articles/{unique}",
        title=title,
        summary="Restricted source summary that must not be snapshotted.",
        published_at=datetime.now(timezone.utc),
        dedupe_key=f"investigation-policy:{unique}",
        content_hash=unique * 2,
        status="content_fetched",
    )
    db_session.add(item)
    db_session.commit()
    return feed, item


def _restrict_investigation_with_item(
    client,
    auth_headers,
    investigation: dict,
    item: Item,
) -> dict:
    response = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["admin"],
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": investigation["version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_report_source(db_session, *, owner, feed: Feed, item: Item) -> Report:
    now = datetime.now(timezone.utc)
    report = Report(
        owner_user_id=owner.id,
        title="Restricted source report",
        report_type="custom",
        status="ready",
        trigger_source="manual",
        generation_stage="completed",
        period_start=now - timedelta(days=1),
        period_end=now,
        summary_text="Restricted report summary.",
        generated_at=now,
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
            feed_name_snapshot=feed.name,
            url_snapshot=item.url,
            first_seen_at_snapshot=item.first_seen_at,
            evidence_text=item.summary or "",
        )
    )
    db_session.flush()
    ensure_report_data_access_envelope(db_session, report_id=report.id)
    db_session.commit()
    return report


def _create_alert_source(db_session, *, owner, item: Item) -> AlertOccurrence:
    occurrence = AlertOccurrence(
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=owner.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=1,
        item_content_hash=item.content_hash,
        alert_name_snapshot="Restricted alert source",
        alert_category_snapshot="identity",
        alert_keywords_snapshot=["credential"],
        matched_keywords=["credential"],
        source_snapshot_json={
            "item": {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
            },
            "feed": {"id": str(item.feed_id), "name": "Restricted alert feed"},
        },
        severity_snapshot="high",
        lifecycle_state="new",
    )
    db_session.add(occurrence)
    db_session.flush()
    ensure_alert_occurrence_data_access_envelope(
        db_session,
        occurrence_id=occurrence.id,
    )
    db_session.commit()
    return occurrence


def test_restricted_investigations_are_filtered_from_all_read_surfaces(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    visible = _create_investigation(
        client,
        auth_headers["analyst"],
        title="Visible investigation",
    )
    restricted = _create_investigation(
        client,
        auth_headers["analyst"],
        title="Restricted investigation",
    )
    restricted = _add_admin_owner(client, auth_headers, seed_users, restricted)
    label = _create_restricted_label(db_session, seed_users)
    _feed, item = _create_item(db_session, label, title="Restricted investigation item")
    _set_policy_mode(
        db_session,
        seed_users,
        monkeypatch,
        mode="enforced",
    )
    restricted = _restrict_investigation_with_item(
        client,
        auth_headers,
        restricted,
        item,
    )

    envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
        resource_id=uuid.UUID(restricted["id"]),
    )
    assert envelope is not None
    assert label.id in envelope.label_ids

    analyst_list = client.get("/investigations", headers=auth_headers["analyst"])
    assert analyst_list.status_code == 200, analyst_list.text
    assert {row["id"] for row in analyst_list.json()["investigations"]} == {
        visible["id"]
    }

    admin_list = client.get("/investigations", headers=auth_headers["admin"])
    assert admin_list.status_code == 200, admin_list.text
    assert restricted["id"] in {
        row["id"] for row in admin_list.json()["investigations"]
    }

    missing_id = uuid.uuid4()
    for suffix in ("", "/evidence", "/notes", "/activity"):
        hidden = client.get(
            f"/investigations/{restricted['id']}{suffix}",
            headers=auth_headers["analyst"],
        )
        missing = client.get(
            f"/investigations/{missing_id}{suffix}",
            headers=auth_headers["analyst"],
        )
        assert hidden.status_code == missing.status_code == 404
        assert _problem_without_request_id(hidden) == _problem_without_request_id(
            missing
        )

        allowed = client.get(
            f"/investigations/{restricted['id']}{suffix}",
            headers=auth_headers["admin"],
        )
        assert allowed.status_code == 200, allowed.text


def test_restricted_investigation_writes_are_404_and_have_no_side_effects(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    investigation = _create_investigation(
        client,
        auth_headers["analyst"],
        title="Immutable restricted investigation",
    )
    investigation = _add_admin_owner(
        client,
        auth_headers,
        seed_users,
        investigation,
    )
    note_response = client.post(
        f"/investigations/{investigation['id']}/notes",
        headers=auth_headers["analyst"],
        json={
            "body": "Original restricted note",
            "expected_version": investigation["version"],
        },
    )
    assert note_response.status_code == 200, note_response.text
    investigation = note_response.json()
    note = investigation["notes"][0]

    label = _create_restricted_label(db_session, seed_users)
    _feed, restricted_item = _create_item(
        db_session,
        label,
        title="Write-lock restriction source",
    )
    _other_feed, other_item = _create_item(
        db_session,
        label,
        title="Denied evidence mutation source",
    )
    _set_policy_mode(
        db_session,
        seed_users,
        monkeypatch,
        mode="enforced",
    )
    investigation = _restrict_investigation_with_item(
        client,
        auth_headers,
        investigation,
        restricted_item,
    )
    evidence = investigation["evidence"][0]
    investigation_id = uuid.UUID(investigation["id"])
    version = investigation["version"]

    activity_before = db_session.scalar(
        select(func.count(InvestigationActivity.id)).where(
            InvestigationActivity.investigation_id == investigation_id
        )
    )
    audit_before = db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource_type == "investigation",
            AuditLog.resource_id == investigation["id"],
        )
    )

    responses = [
        client.patch(
            f"/investigations/{investigation['id']}",
            headers=auth_headers["analyst"],
            json={"title": "Denied title", "expected_version": version},
        ),
        client.post(
            f"/investigations/{investigation['id']}/members",
            headers=auth_headers["analyst"],
            json={
                "user_id": str(seed_users["viewer"].id),
                "role": "viewer",
                "expected_version": version,
            },
        ),
        client.patch(
            f"/investigations/{investigation['id']}/members/{seed_users['admin'].id}",
            headers=auth_headers["analyst"],
            json={"role": "editor", "expected_version": version},
        ),
        client.delete(
            f"/investigations/{investigation['id']}/members/{seed_users['admin'].id}",
            headers=auth_headers["analyst"],
            params={"expected_version": version},
        ),
        client.post(
            f"/investigations/{investigation['id']}/evidence",
            headers=auth_headers["analyst"],
            json={
                "source_type": "item",
                "source_id": str(other_item.id),
                "expected_version": version,
            },
        ),
        client.delete(
            f"/investigations/{investigation['id']}/evidence/{evidence['id']}",
            headers=auth_headers["analyst"],
            params={"expected_version": version},
        ),
        client.post(
            f"/investigations/{investigation['id']}/notes",
            headers=auth_headers["analyst"],
            json={"body": "Denied note", "expected_version": version},
        ),
        client.patch(
            f"/investigations/{investigation['id']}/notes/{note['id']}",
            headers=auth_headers["analyst"],
            json={
                "body": "Denied note update",
                "expected_note_version": note["version"],
                "expected_investigation_version": version,
            },
        ),
        client.delete(
            f"/investigations/{investigation['id']}/notes/{note['id']}",
            headers=auth_headers["analyst"],
            params={
                "expected_note_version": note["version"],
                "expected_investigation_version": version,
            },
        ),
    ]

    missing = client.patch(
        f"/investigations/{uuid.uuid4()}",
        headers=auth_headers["analyst"],
        json={"title": "Missing", "expected_version": version},
    )
    assert missing.status_code == 404
    for response in responses:
        assert response.status_code == 404, response.text
        assert _problem_without_request_id(response) == _problem_without_request_id(
            missing
        )

    db_session.expire_all()
    stored = db_session.get(Investigation, investigation_id)
    stored_note = db_session.get(InvestigationNote, uuid.UUID(note["id"]))
    assert stored is not None
    assert stored.version == version
    assert stored.title == "Immutable restricted investigation"
    assert stored_note is not None
    assert stored_note.body == "Original restricted note"
    assert stored_note.version == note["version"]
    assert stored_note.deleted_at is None
    assert (
        db_session.scalar(
            select(func.count(InvestigationEvidence.id)).where(
                InvestigationEvidence.investigation_id == investigation_id
            )
        )
        == 1
    )
    assert set(
        db_session.execute(
            select(InvestigationMember.user_id, InvestigationMember.role).where(
                InvestigationMember.investigation_id == investigation_id
            )
        ).all()
    ) == {
        (seed_users["analyst"].id, "owner"),
        (seed_users["admin"].id, "owner"),
    }
    assert (
        db_session.scalar(
            select(func.count(InvestigationActivity.id)).where(
                InvestigationActivity.investigation_id == investigation_id
            )
        )
        == activity_before
    )
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.resource_type == "investigation",
                AuditLog.resource_id == investigation["id"],
            )
        )
        == audit_before
    )


def test_restricted_item_report_and_alert_sources_are_404_before_snapshotting(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    investigation = _create_investigation(
        client,
        auth_headers["analyst"],
        title="Accessible source authorization target",
    )
    label = _create_restricted_label(db_session, seed_users)
    feed, item = _create_item(db_session, label, title="Hidden evidence source")
    report = _create_report_source(
        db_session,
        owner=seed_users["analyst"],
        feed=feed,
        item=item,
    )
    alert = _create_alert_source(
        db_session,
        owner=seed_users["analyst"],
        item=item,
    )
    _set_policy_mode(
        db_session,
        seed_users,
        monkeypatch,
        mode="enforced",
    )

    def _unexpected_snapshot(*_args, **_kwargs):
        raise AssertionError("restricted source data must not be assembled")

    monkeypatch.setattr(investigation_evidence, "_bounded_text", _unexpected_snapshot)
    investigation_id = uuid.UUID(investigation["id"])
    activity_before = db_session.scalar(
        select(func.count(InvestigationActivity.id)).where(
            InvestigationActivity.investigation_id == investigation_id
        )
    )
    audit_before = db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource_type == "investigation",
            AuditLog.resource_id == investigation["id"],
        )
    )

    for source_type, source_id in (
        ("item", item.id),
        ("report", report.id),
        ("alert_occurrence", alert.id),
    ):
        hidden = client.post(
            f"/investigations/{investigation['id']}/evidence",
            headers=auth_headers["analyst"],
            json={
                "source_type": source_type,
                "source_id": str(source_id),
                "expected_version": investigation["version"],
            },
        )
        missing = client.post(
            f"/investigations/{investigation['id']}/evidence",
            headers=auth_headers["analyst"],
            json={
                "source_type": source_type,
                "source_id": str(uuid.uuid4()),
                "expected_version": investigation["version"],
            },
        )
        assert hidden.status_code == missing.status_code == 404
        assert _problem_without_request_id(hidden) == _problem_without_request_id(
            missing
        )
        assert hidden.json()["error"]["code"] == (
            "investigation_evidence_source_not_found"
        )

    db_session.expire_all()
    stored = db_session.get(Investigation, investigation_id)
    assert stored is not None and stored.version == investigation["version"]
    assert (
        db_session.scalar(
            select(func.count(InvestigationEvidence.id)).where(
                InvestigationEvidence.investigation_id == investigation_id
            )
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count(InvestigationActivity.id)).where(
                InvestigationActivity.investigation_id == investigation_id
            )
        )
        == activity_before
    )
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.resource_type == "investigation",
                AuditLog.resource_id == investigation["id"],
            )
        )
        == audit_before
    )


def test_ioc_evidence_uses_only_accessible_observations(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
):
    investigation = _create_investigation(
        client,
        auth_headers["analyst"],
        title="IOC observation policy target",
    )
    unrestricted = db_session.get(
        HandlingLabel,
        UNRESTRICTED_HANDLING_LABEL_ID,
    )
    assert unrestricted is not None
    restricted = _create_restricted_label(db_session, seed_users)
    _visible_feed, visible_item = _create_item(
        db_session,
        unrestricted,
        title="Visible IOC observation",
    )
    _restricted_feed, restricted_item = _create_item(
        db_session,
        restricted,
        title="Restricted IOC observation",
    )
    shared_ioc = IOC(
        type="domain",
        value_raw="shared-investigation.example",
        value_norm="shared-investigation.example",
        first_seen_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        last_seen_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    restricted_ioc = IOC(
        type="domain",
        value_raw="restricted-investigation.example",
        value_norm="restricted-investigation.example",
    )
    db_session.add_all([shared_ioc, restricted_ioc])
    db_session.flush()
    db_session.add_all(
        [
            ItemIOC(item_id=visible_item.id, ioc_id=shared_ioc.id),
            ItemIOC(item_id=restricted_item.id, ioc_id=shared_ioc.id),
            ItemIOC(item_id=restricted_item.id, ioc_id=restricted_ioc.id),
        ]
    )
    db_session.commit()
    _set_policy_mode(
        db_session,
        seed_users,
        monkeypatch,
        mode="enforced",
    )

    attached = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "ioc",
            "source_id": str(shared_ioc.id),
            "expected_version": investigation["version"],
        },
    )
    assert attached.status_code == 200, attached.text
    evidence = attached.json()["evidence"][0]
    assert (
        datetime.fromisoformat(
            evidence["metadata_snapshot"]["first_seen_at"].replace("Z", "+00:00")
        )
        == visible_item.first_seen_at
    )
    assert (
        datetime.fromisoformat(
            evidence["metadata_snapshot"]["last_seen_at"].replace("Z", "+00:00")
        )
        == visible_item.first_seen_at
    )
    envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INVESTIGATION,
        resource_id=uuid.UUID(investigation["id"]),
    )
    assert envelope is not None
    assert envelope.label_ids == frozenset({UNRESTRICTED_HANDLING_LABEL_ID})

    denied = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "ioc",
            "source_id": str(restricted_ioc.id),
            "expected_version": attached.json()["version"],
        },
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "investigation_evidence_source_not_found"


@pytest.mark.parametrize("mode", ["disabled", "audit"])
def test_restricted_investigation_access_remains_allowed_outside_enforcement(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    investigation = _create_investigation(
        client,
        auth_headers["analyst"],
        title=f"{mode.title()} policy investigation",
    )
    label = _create_restricted_label(db_session, seed_users)
    _feed, item = _create_item(db_session, label, title=f"{mode} restricted source")
    orphan_ioc = IOC(
        type="domain",
        value_raw=f"{mode}-orphan.example",
        value_norm=f"{mode}-orphan.example",
    )
    db_session.add(orphan_ioc)
    db_session.commit()
    _set_policy_mode(
        db_session,
        seed_users,
        monkeypatch,
        mode=mode,
    )

    attached = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": investigation["version"],
        },
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["evidence_count"] == 1

    orphan_attached = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "ioc",
            "source_id": str(orphan_ioc.id),
            "expected_version": attached.json()["version"],
        },
    )
    assert orphan_attached.status_code == 200, orphan_attached.text
    assert orphan_attached.json()["evidence_count"] == 2

    listed = client.get("/investigations", headers=auth_headers["analyst"])
    assert listed.status_code == 200, listed.text
    assert investigation["id"] in {row["id"] for row in listed.json()["investigations"]}
    for suffix in ("", "/evidence", "/notes", "/activity"):
        response = client.get(
            f"/investigations/{investigation['id']}{suffix}",
            headers=auth_headers["analyst"],
        )
        assert response.status_code == 200, response.text

    note = client.post(
        f"/investigations/{investigation['id']}/notes",
        headers=auth_headers["analyst"],
        json={
            "body": f"{mode} mode write remains available",
            "expected_version": orphan_attached.json()["version"],
        },
    )
    assert note.status_code == 200, note.text
    assert note.json()["note_count"] == 1
