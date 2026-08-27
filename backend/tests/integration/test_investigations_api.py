import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import generate_api_token
from app.core.token_scopes import (
    SCOPE_READ_ALERTS,
    SCOPE_READ_ITEMS,
    SCOPE_READ_REPORTS,
    SCOPE_WRITE_INVESTIGATIONS,
)
from app.models.api_token import ApiToken
from app.models.alert_occurrence import AlertOccurrence
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.investigation import (
    InvestigationActivity,
    InvestigationEvidence,
    InvestigationNote,
)
from app.models.item import Item
from app.models.report import Report
from app.services.investigation_collections import INVESTIGATION_DETAIL_COLLECTION_LIMIT
from app.services.investigations import (
    eligible_investigation_owner_ids_query,
)


def _create_investigation(
    client: TestClient,
    headers: dict[str, str],
    *,
    title: str = "Credential theft campaign",
    visibility: str = "private",
):
    response = client.post(
        "/investigations",
        headers=headers,
        json={
            "title": title,
            "description": "Track related reporting and observables.",
            "severity": "high",
            "visibility": visibility,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_item(db_session) -> Item:
    unique = uuid.uuid4().hex
    feed = Feed(name="Investigation source", url=f"https://example.com/{unique}.xml")
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        source_guid=f"investigation-{unique}",
        url=f"https://example.com/articles/{unique}",
        canonical_url=f"https://example.com/articles/{unique}",
        title="Observed credential theft infrastructure",
        summary="A bounded source summary for the investigation snapshot.",
        published_at=datetime.now(timezone.utc),
        dedupe_key=f"investigation:{unique}",
        content_hash="a" * 64,
        status="content_fetched",
    )
    db_session.add(item)
    db_session.commit()
    return item


def _create_api_token(db_session, user, *, name: str, scopes: list[str]) -> str:
    token_value, prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=name,
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()
    return token_value


def _create_alert_occurrence(db_session, *, owner, item: Item) -> AlertOccurrence:
    occurrence = AlertOccurrence(
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=owner.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=3,
        item_content_hash=item.content_hash,
        alert_name_snapshot="Credential theft watch",
        alert_category_snapshot="identity",
        alert_keywords_snapshot=["credential", "token"],
        matched_keywords=["credential"],
        source_snapshot_json={
            "item": {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
                "canonical_url": item.canonical_url,
                "published_at": item.published_at.isoformat(),
                "first_seen_at": item.first_seen_at.isoformat(),
            },
            "feed": {"id": str(item.feed_id), "name": "Investigation source"},
            "classification": {"primary_category": "credential_access"},
        },
        severity_snapshot="high",
        lifecycle_state="investigating",
    )
    db_session.add(occurrence)
    db_session.commit()
    return occurrence


def _create_report(db_session, *, owner) -> Report:
    now = datetime.now(timezone.utc)
    report = Report(
        owner_user_id=owner.id,
        title="Private threat landscape report",
        report_type="custom",
        status="ready",
        trigger_source="manual",
        generation_stage="completed",
        period_start=now - timedelta(days=7),
        period_end=now,
        summary_text="Owner-scoped report summary.",
        generated_at=now,
    )
    db_session.add(report)
    db_session.commit()
    return report


def test_private_and_team_visibility_are_membership_aware(
    client: TestClient, auth_headers
):
    private = _create_investigation(client, auth_headers["analyst"])

    assert (
        client.get(
            f"/investigations/{private['id']}", headers=auth_headers["admin"]
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/investigations/{private['id']}", headers=auth_headers["viewer"]
        ).status_code
        == 404
    )

    viewer_list = client.get("/investigations", headers=auth_headers["viewer"])
    assert viewer_list.status_code == 200
    assert viewer_list.json()["total"] == 0

    team = _create_investigation(
        client,
        auth_headers["analyst"],
        title="Shared ransomware tracking",
        visibility="team",
    )
    viewer_detail = client.get(
        f"/investigations/{team['id']}", headers=auth_headers["viewer"]
    )
    assert viewer_detail.status_code == 200
    assert viewer_detail.json()["current_user_role"] is None

    denied_update = client.patch(
        f"/investigations/{team['id']}",
        headers=auth_headers["viewer"],
        json={"expected_version": team["version"], "title": "Viewer edit"},
    )
    assert denied_update.status_code == 403
    assert "analyst or administrator" in denied_update.json()["detail"]


def test_membership_roles_and_final_owner_invariant(
    client: TestClient, auth_headers, seed_users
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    investigation_id = investigation["id"]

    add_editor = client.post(
        f"/investigations/{investigation_id}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "editor",
            "expected_version": investigation["version"],
        },
    )
    assert add_editor.status_code == 200, add_editor.text
    detail = add_editor.json()
    assert detail["version"] == investigation["version"] + 1

    editor_update = client.patch(
        f"/investigations/{investigation_id}",
        headers=auth_headers["admin"],
        json={
            "expected_version": detail["version"],
            "description": "Updated by the assigned editor.",
        },
    )
    assert editor_update.status_code == 200
    detail = editor_update.json()

    editor_member_change = client.patch(
        f"/investigations/{investigation_id}/members/{seed_users['analyst'].id}",
        headers=auth_headers["admin"],
        json={"expected_version": detail["version"], "role": "viewer"},
    )
    assert editor_member_change.status_code == 403
    assert "Only an investigation owner" in editor_member_change.json()["detail"]

    final_owner_change = client.patch(
        f"/investigations/{investigation_id}/members/{seed_users['analyst'].id}",
        headers=auth_headers["analyst"],
        json={"expected_version": detail["version"], "role": "editor"},
    )
    assert final_owner_change.status_code == 409
    assert "at least one owner" in final_owner_change.json()["detail"]
    assert final_owner_change.json()["error"]["code"] == "investigation_owner_required"

    promote_admin = client.patch(
        f"/investigations/{investigation_id}/members/{seed_users['admin'].id}",
        headers=auth_headers["analyst"],
        json={"expected_version": detail["version"], "role": "owner"},
    )
    assert promote_admin.status_code == 200
    detail = promote_admin.json()

    downgrade_creator = client.patch(
        f"/investigations/{investigation_id}/members/{seed_users['analyst'].id}",
        headers=auth_headers["analyst"],
        json={"expected_version": detail["version"], "role": "editor"},
    )
    assert downgrade_creator.status_code == 200

    invalid_viewer_owner = client.post(
        f"/investigations/{investigation_id}/members",
        headers=auth_headers["admin"],
        json={
            "user_id": str(seed_users["viewer"].id),
            "role": "owner",
            "expected_version": downgrade_creator.json()["version"],
        },
    )
    assert invalid_viewer_owner.status_code == 422
    assert "analyst or administrator account" in invalid_viewer_owner.json()["detail"]


def test_stale_versions_preserve_the_first_update(client: TestClient, auth_headers):
    investigation = _create_investigation(client, auth_headers["analyst"])
    first = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={
            "expected_version": investigation["version"],
            "title": "First accepted title",
        },
    )
    assert first.status_code == 200

    stale = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"expected_version": investigation["version"], "title": "Stale overwrite"},
    )
    assert stale.status_code == 409
    assert "changed after you loaded it" in stale.json()["detail"]
    assert stale.json()["error"]["code"] == "investigation_version_conflict"
    assert stale.headers["x-error-code"] == "investigation_version_conflict"

    current = client.get(
        f"/investigations/{investigation['id']}", headers=auth_headers["analyst"]
    )
    assert current.json()["title"] == "First accepted title"


def test_evidence_uses_bounded_snapshot_that_survives_source_removal(
    client: TestClient,
    auth_headers,
    db_session,
):
    item = _create_item(db_session)
    investigation = _create_investigation(client, auth_headers["analyst"])

    add_response = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "note": "Supports the infrastructure hypothesis.",
            "expected_version": investigation["version"],
        },
    )
    assert add_response.status_code == 200, add_response.text
    evidence = add_response.json()["evidence"][0]
    assert evidence["title_snapshot"] == "Observed credential theft infrastructure"
    assert evidence["description_snapshot"].startswith("A bounded source summary")
    assert evidence["metadata_snapshot"]["content_hash"] == "a" * 64
    assert "full_text" not in evidence["metadata_snapshot"]
    assert "private_note" not in evidence["metadata_snapshot"]

    duplicate = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": add_response.json()["version"],
        },
    )
    assert duplicate.status_code == 409
    assert "already included" in duplicate.json()["detail"]
    assert duplicate.json()["error"]["code"] == "investigation_evidence_exists"

    db_session.delete(item)
    db_session.commit()
    detail = client.get(
        f"/investigations/{investigation['id']}", headers=auth_headers["analyst"]
    )
    assert detail.status_code == 200
    assert (
        detail.json()["evidence"][0]["title_snapshot"]
        == "Observed credential theft infrastructure"
    )


def test_detail_collections_are_bounded_and_complete_pages_remain_authorized(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    investigation_id = uuid.UUID(investigation["id"])
    note_ids: list[uuid.UUID] = []
    evidence_ids: list[uuid.UUID] = []
    for index in range(INVESTIGATION_DETAIL_COLLECTION_LIMIT + 5):
        note_id = uuid.uuid4()
        evidence_id = uuid.uuid4()
        note_ids.append(note_id)
        evidence_ids.append(evidence_id)
        db_session.add(
            InvestigationNote(
                id=note_id,
                investigation_id=investigation_id,
                author_user_id=seed_users["analyst"].id,
                body=f"Investigation note {index:03d}",
            )
        )
        db_session.add(
            InvestigationEvidence(
                id=evidence_id,
                investigation_id=investigation_id,
                source_type="item",
                source_id=uuid.uuid4(),
                title_snapshot=f"Evidence {index:03d}",
                description_snapshot=None,
                url_snapshot=None,
                metadata_snapshot_json={"sequence": index},
                note=None,
                added_by_user_id=seed_users["analyst"].id,
            )
        )
    db_session.commit()

    detail = client.get(
        f"/investigations/{investigation['id']}", headers=auth_headers["analyst"]
    )
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["note_count"] == INVESTIGATION_DETAIL_COLLECTION_LIMIT + 5
    assert payload["evidence_count"] == INVESTIGATION_DETAIL_COLLECTION_LIMIT + 5
    assert len(payload["notes"]) == INVESTIGATION_DETAIL_COLLECTION_LIMIT
    assert len(payload["evidence"]) == INVESTIGATION_DETAIL_COLLECTION_LIMIT
    assert payload["notes_truncated"] is True
    assert payload["evidence_truncated"] is True

    note_pages = [
        client.get(
            f"/investigations/{investigation['id']}/notes",
            params={"page": page, "page_size": 100},
            headers=auth_headers["analyst"],
        )
        for page in (1, 2, 3)
    ]
    evidence_pages = [
        client.get(
            f"/investigations/{investigation['id']}/evidence",
            params={"page": page, "page_size": 100},
            headers=auth_headers["analyst"],
        )
        for page in (1, 2, 3)
    ]
    assert all(response.status_code == 200 for response in note_pages)
    assert all(response.status_code == 200 for response in evidence_pages)
    assert [len(response.json()["notes"]) for response in note_pages] == [100, 100, 5]
    assert [len(response.json()["evidence"]) for response in evidence_pages] == [100, 100, 5]
    assert {entry["id"] for response in note_pages for entry in response.json()["notes"]} == {
        str(note_id) for note_id in note_ids
    }
    assert {
        entry["id"]
        for response in evidence_pages
        for entry in response.json()["evidence"]
    } == {str(evidence_id) for evidence_id in evidence_ids}

    for suffix in ("notes", "evidence"):
        hidden = client.get(
            f"/investigations/{investigation['id']}/{suffix}",
            headers=auth_headers["viewer"],
        )
        assert hidden.status_code == 404
        oversized = client.get(
            f"/investigations/{investigation['id']}/{suffix}",
            params={"page_size": 101},
            headers=auth_headers["analyst"],
        )
        assert oversized.status_code == 422
        excessive_page = client.get(
            f"/investigations/{investigation['id']}/{suffix}",
            params={"page": 1_000_001},
            headers=auth_headers["analyst"],
        )
        assert excessive_page.status_code == 422


def test_alert_occurrence_evidence_is_owner_authorized_and_immutable(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    item = _create_item(db_session)
    occurrence = _create_alert_occurrence(
        db_session,
        owner=seed_users["analyst"],
        item=item,
    )
    investigation = _create_investigation(client, auth_headers["analyst"])
    alert_only_token = _create_api_token(
        db_session,
        seed_users["analyst"],
        name="alert-evidence-without-item-read",
        scopes=[SCOPE_WRITE_INVESTIGATIONS, SCOPE_READ_ALERTS],
    )
    missing_item_scope = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers={"Authorization": f"Bearer {alert_only_token}"},
        json={
            "source_type": "alert_occurrence",
            "source_id": str(occurrence.id),
            "expected_version": investigation["version"],
        },
    )
    assert missing_item_scope.status_code == 403
    assert SCOPE_READ_ITEMS in missing_item_scope.json()["detail"]

    attached = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "alert_occurrence",
            "source_id": str(occurrence.id),
            "expected_version": investigation["version"],
        },
    )
    assert attached.status_code == 200, attached.text
    snapshot = attached.json()["evidence"][0]
    assert snapshot["title_snapshot"].startswith("Credential theft watch:")
    assert snapshot["metadata_snapshot"]["rule_revision"] == 3
    assert snapshot["metadata_snapshot"]["lifecycle_state_at_attachment"] == (
        "investigating"
    )
    assert snapshot["metadata_snapshot"]["item_content_hash"] == item.content_hash

    occurrence.alert_name_snapshot = "Changed after attachment"
    occurrence.lifecycle_state = "closed"
    occurrence.closure_disposition = "true_positive"
    db_session.commit()
    db_session.delete(occurrence)
    db_session.commit()

    evidence_page = client.get(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
    )
    assert evidence_page.status_code == 200, evidence_page.text
    assert evidence_page.json()["evidence"][0] == snapshot

    other_occurrence = _create_alert_occurrence(
        db_session,
        owner=seed_users["admin"],
        item=item,
    )
    denied = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "alert_occurrence",
            "source_id": str(other_occurrence.id),
            "expected_version": attached.json()["version"],
        },
    )
    assert denied.status_code == 422
    assert "does not exist or is not available" in denied.json()["detail"]


def test_report_evidence_enforces_report_owner_or_admin_access(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    report = _create_report(db_session, owner=seed_users["viewer"])
    investigation = _create_investigation(client, auth_headers["analyst"])

    denied = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "report",
            "source_id": str(report.id),
            "expected_version": investigation["version"],
        },
    )
    assert denied.status_code == 422
    assert "does not exist or is not available" in denied.json()["detail"]

    add_admin = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "owner",
            "expected_version": investigation["version"],
        },
    )
    assert add_admin.status_code == 200, add_admin.text

    attached = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["admin"],
        json={
            "source_type": "report",
            "source_id": str(report.id),
            "expected_version": add_admin.json()["version"],
        },
    )
    assert attached.status_code == 200, attached.text
    snapshot = attached.json()["evidence"][0]
    assert snapshot["title_snapshot"] == report.title
    assert snapshot["description_snapshot"] == report.summary_text


def test_notes_are_versioned_and_activity_does_not_duplicate_note_body(
    client: TestClient,
    auth_headers,
    seed_users,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    secret_marker = "analyst-only-context-marker"
    note_response = client.post(
        f"/investigations/{investigation['id']}/notes",
        headers=auth_headers["analyst"],
        json={"body": secret_marker, "expected_version": investigation["version"]},
    )
    assert note_response.status_code == 200
    detail = note_response.json()
    note = detail["notes"][0]

    update_response = client.patch(
        f"/investigations/{investigation['id']}/notes/{note['id']}",
        headers=auth_headers["analyst"],
        json={
            "body": "Corrected analyst context.",
            "expected_note_version": note["version"],
            "expected_investigation_version": detail["version"],
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["notes"][0]["version"] == note["version"] + 1

    stale_note = client.patch(
        f"/investigations/{investigation['id']}/notes/{note['id']}",
        headers=auth_headers["analyst"],
        json={
            "body": "Stale note",
            "expected_note_version": note["version"],
            "expected_investigation_version": update_response.json()["version"],
        },
    )
    assert stale_note.status_code == 409
    assert stale_note.json()["error"]["code"] == "investigation_note_version_conflict"

    activity = client.get(
        f"/investigations/{investigation['id']}/activity",
        headers=auth_headers["analyst"],
    )
    assert activity.status_code == 200
    assert activity.json()["total"] >= 3
    assert secret_marker not in activity.text
    assert {entry["action"] for entry in activity.json()["activities"]} >= {
        "investigation.created",
        "investigation.note_added",
        "investigation.note_updated",
    }

    delete_response = client.delete(
        f"/investigations/{investigation['id']}/notes/{note['id']}",
        headers=auth_headers["analyst"],
        params={
            "expected_note_version": update_response.json()["notes"][0]["version"],
            "expected_investigation_version": update_response.json()["version"],
        },
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["notes"] == []
    assert delete_response.json()["note_count"] == 0


def test_archive_is_owner_only_read_only_and_reopenable(
    client: TestClient, auth_headers, seed_users
):
    investigation = _create_investigation(
        client, auth_headers["analyst"], visibility="team"
    )
    add_editor = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "editor",
            "expected_version": investigation["version"],
        },
    )
    detail = add_editor.json()
    editor_archive = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["admin"],
        json={"expected_version": detail["version"], "status": "archived"},
    )
    assert editor_archive.status_code == 403

    archived = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"expected_version": detail["version"], "status": "archived"},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    archived_note = client.post(
        f"/investigations/{investigation['id']}/notes",
        headers=auth_headers["analyst"],
        json={
            "body": "Must not be accepted",
            "expected_version": archived.json()["version"],
        },
    )
    assert archived_note.status_code == 409
    assert "read-only" in archived_note.json()["detail"]
    assert archived_note.json()["error"]["code"] == "investigation_archived"

    archived_member_add = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["viewer"].id),
            "role": "viewer",
            "expected_version": archived.json()["version"],
        },
    )
    archived_member_update = client.patch(
        f"/investigations/{investigation['id']}/members/{seed_users['admin'].id}",
        headers=auth_headers["analyst"],
        json={"role": "editor", "expected_version": archived.json()["version"]},
    )
    archived_member_remove = client.delete(
        f"/investigations/{investigation['id']}/members/{seed_users['admin'].id}",
        headers=auth_headers["analyst"],
        params={"expected_version": archived.json()["version"]},
    )
    for response in (
        archived_member_add,
        archived_member_update,
        archived_member_remove,
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "investigation_archived"

    reopened = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"expected_version": archived.json()["version"], "status": "open"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["archived_at"] is None


def test_member_candidates_are_narrow_and_writer_only(
    client: TestClient, auth_headers, seed_users
):
    analyst_response = client.get(
        "/investigations/member-candidates",
        params={"q": "admin@"},
        headers=auth_headers["analyst"],
    )
    assert analyst_response.status_code == 200
    assert analyst_response.json()["users"] == [
        {
            "id": str(seed_users["admin"].id),
            "email": seed_users["admin"].email,
            "account_role": "admin",
        }
    ]

    viewer_response = client.get(
        "/investigations/member-candidates",
        headers=auth_headers["viewer"],
    )
    assert viewer_response.status_code == 403


def test_api_tokens_require_new_explicit_scope(
    client: TestClient, db_session, seed_users, auth_headers
):
    investigation = _create_investigation(
        client, auth_headers["analyst"], visibility="team"
    )
    token_value, prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=seed_users["analyst"].id,
            name="items-only",
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=[SCOPE_READ_ITEMS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()

    response = client.get(
        f"/investigations/{investigation['id']}",
        headers={"Authorization": f"Bearer {token_value}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient token scope"


def test_evidence_attachment_requires_the_source_read_scope_for_api_tokens(
    client: TestClient,
    db_session,
    seed_users,
    auth_headers,
):
    investigation = _create_investigation(
        client, auth_headers["analyst"], visibility="team"
    )
    item = _create_item(db_session)
    token_value = _create_api_token(
        db_session,
        seed_users["analyst"],
        name="investigations-without-source-read",
        scopes=[SCOPE_WRITE_INVESTIGATIONS],
    )
    token_headers = {"Authorization": f"Bearer {token_value}"}

    for source_type, required_scope in (
        ("item", SCOPE_READ_ITEMS),
        ("ioc", SCOPE_READ_ITEMS),
        ("report", SCOPE_READ_REPORTS),
        ("alert_occurrence", SCOPE_READ_ALERTS),
    ):
        denied = client.post(
            f"/investigations/{investigation['id']}/evidence",
            headers=token_headers,
            json={
                "source_type": source_type,
                "source_id": str(item.id),
                "expected_version": investigation["version"],
            },
        )
        assert denied.status_code == 403
        assert required_scope in denied.json()["detail"]

    allowed_token = _create_api_token(
        db_session,
        seed_users["analyst"],
        name="investigations-with-item-read",
        scopes=[SCOPE_WRITE_INVESTIGATIONS, SCOPE_READ_ITEMS],
    )
    allowed = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers={"Authorization": f"Bearer {allowed_token}"},
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": investigation["version"],
        },
    )
    assert allowed.status_code == 200, allowed.text


def test_cookie_session_evidence_attachment_remains_rbac_governed(
    client: TestClient,
    db_session,
    seed_users,
    auth_headers,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    item = _create_item(db_session)
    login = client.post(
        "/auth/login",
        json={"email": seed_users["analyst"].email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200, login.text

    response = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers={"x-csrf-token": login.json()["csrf_token"]},
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": investigation["version"],
        },
    )
    assert response.status_code == 200, response.text


def test_legacy_unscoped_token_compatibility_applies_to_evidence_sources(
    client: TestClient,
    db_session,
    seed_users,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
):
    investigation = _create_investigation(
        client, auth_headers["analyst"], visibility="team"
    )
    item = _create_item(db_session)
    token_value = _create_api_token(
        db_session,
        seed_users["analyst"],
        name="legacy-unscoped-investigation",
        scopes=[],
    )
    monkeypatch.setenv("ALLOW_LEGACY_UNSCOPED_TOKENS", "true")
    get_settings.cache_clear()

    response = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers={"Authorization": f"Bearer {token_value}"},
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": investigation["version"],
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("attribute", "ineligible_value"),
    (("is_active", False), ("is_approved", False), ("role", "viewer")),
)
def test_last_owner_checks_ignore_ineligible_owner_memberships(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    attribute: str,
    ineligible_value,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    add_owner = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "owner",
            "expected_version": investigation["version"],
        },
    )
    assert add_owner.status_code == 200, add_owner.text
    setattr(seed_users["admin"], attribute, ineligible_value)
    db_session.commit()

    eligible_owner_ids = set(
        db_session.scalars(
            eligible_investigation_owner_ids_query(uuid.UUID(investigation["id"]))
        ).all()
    )
    assert eligible_owner_ids == {seed_users["analyst"].id}

    downgrade = client.patch(
        f"/investigations/{investigation['id']}/members/{seed_users['analyst'].id}",
        headers=auth_headers["analyst"],
        json={"role": "editor", "expected_version": add_owner.json()["version"]},
    )
    assert downgrade.status_code == 409
    assert downgrade.json()["error"]["code"] == "investigation_owner_required"
    assert downgrade.json()["detail"].startswith(
        "An investigation must retain at least one owner"
    )


def test_private_owner_cannot_remove_self_and_lose_response_access(
    client: TestClient,
    auth_headers,
    seed_users,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    add_owner = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "owner",
            "expected_version": investigation["version"],
        },
    )
    assert add_owner.status_code == 200, add_owner.text

    removal = client.delete(
        f"/investigations/{investigation['id']}/members/{seed_users['analyst'].id}",
        headers=auth_headers["analyst"],
        params={"expected_version": add_owner.json()["version"]},
    )
    assert removal.status_code == 409
    assert removal.json()["error"]["code"] == "investigation_private_self_removal"
    assert "Ask another owner" in removal.json()["detail"]

    unchanged = client.get(
        f"/investigations/{investigation['id']}", headers=auth_headers["analyst"]
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["version"] == add_owner.json()["version"]
    assert unchanged.json()["current_user_role"] == "owner"


def test_noop_member_and_note_updates_do_not_advance_history(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    add_member = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "editor",
            "expected_version": investigation["version"],
        },
    )
    note_added = client.post(
        f"/investigations/{investigation['id']}/notes",
        headers=auth_headers["analyst"],
        json={
            "body": "Stable note body",
            "expected_version": add_member.json()["version"],
        },
    )
    assert note_added.status_code == 200, note_added.text
    detail = note_added.json()
    note = detail["notes"][0]
    investigation_id = uuid.UUID(investigation["id"])

    activity_before = db_session.scalar(
        select(func.count(InvestigationActivity.id)).where(
            InvestigationActivity.investigation_id == investigation_id
        )
    )
    audit_before = db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource_id == investigation["id"],
            AuditLog.action.in_(
                ("investigations.member.update", "investigations.note.update")
            ),
        )
    )

    member_noop = client.patch(
        f"/investigations/{investigation['id']}/members/{seed_users['admin'].id}",
        headers=auth_headers["analyst"],
        json={"role": "editor", "expected_version": detail["version"]},
    )
    assert member_noop.status_code == 200, member_noop.text
    assert member_noop.json()["version"] == detail["version"]

    note_noop = client.patch(
        f"/investigations/{investigation['id']}/notes/{note['id']}",
        headers=auth_headers["analyst"],
        json={
            "body": note["body"],
            "expected_note_version": note["version"],
            "expected_investigation_version": detail["version"],
        },
    )
    assert note_noop.status_code == 200, note_noop.text
    assert note_noop.json()["version"] == detail["version"]
    assert note_noop.json()["notes"][0]["version"] == note["version"]

    activity_after = db_session.scalar(
        select(func.count(InvestigationActivity.id)).where(
            InvestigationActivity.investigation_id == investigation_id
        )
    )
    audit_after = db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource_id == investigation["id"],
            AuditLog.action.in_(
                ("investigations.member.update", "investigations.note.update")
            ),
        )
    )
    assert activity_after == activity_before
    assert audit_after == audit_before


def test_archiving_preserves_closed_at_and_records_status_transitions(
    client: TestClient, auth_headers
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    closed = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"status": "closed", "expected_version": investigation["version"]},
    )
    assert closed.status_code == 200, closed.text
    archived = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"status": "archived", "expected_version": closed.json()["version"]},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["closed_at"] == closed.json()["closed_at"]

    activity = client.get(
        f"/investigations/{investigation['id']}/activity",
        headers=auth_headers["analyst"],
    )
    transitions = [
        entry["details"]["status_transition"]
        for entry in activity.json()["activities"]
        if "status_transition" in entry["details"]
    ]
    assert {(transition["from"], transition["to"]) for transition in transitions} >= {
        ("open", "closed"),
        ("closed", "archived"),
    }


def test_missing_investigation_children_return_specific_safe_problem_codes(
    client: TestClient, auth_headers
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    missing_id = uuid.uuid4()
    missing_investigation = client.get(
        f"/investigations/{missing_id}", headers=auth_headers["analyst"]
    )
    assert missing_investigation.status_code == 404
    assert missing_investigation.json()["detail"] == "Investigation not found."
    assert missing_investigation.json()["error"]["code"] == "investigation_not_found"

    requests = (
        client.patch(
            f"/investigations/{investigation['id']}/members/{missing_id}",
            headers=auth_headers["analyst"],
            json={"role": "viewer", "expected_version": investigation["version"]},
        ),
        client.delete(
            f"/investigations/{investigation['id']}/evidence/{missing_id}",
            headers=auth_headers["analyst"],
            params={"expected_version": investigation["version"]},
        ),
        client.patch(
            f"/investigations/{investigation['id']}/notes/{missing_id}",
            headers=auth_headers["analyst"],
            json={
                "body": "Missing note",
                "expected_note_version": 1,
                "expected_investigation_version": investigation["version"],
            },
        ),
    )
    expectations = (
        ("Investigation member not found.", "investigation_member_not_found"),
        ("Investigation evidence not found.", "investigation_evidence_not_found"),
        ("Investigation note not found.", "investigation_note_not_found"),
    )
    for response, (detail, code) in zip(requests, expectations, strict=True):
        assert response.status_code == 404
        assert response.json()["detail"] == detail
        assert response.json()["error"]["code"] == code
        assert response.headers["x-error-code"] == code


def test_mutations_emit_global_audit_records(
    client: TestClient, auth_headers, db_session
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    actions = set(
        db_session.scalars(
            select(AuditLog.action).where(
                AuditLog.resource_type == "investigation",
                AuditLog.resource_id == investigation["id"],
            )
        ).all()
    )
    assert "investigations.create" in actions
