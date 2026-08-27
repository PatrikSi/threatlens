import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import generate_api_token
from app.core.token_scopes import SCOPE_READ_ITEMS
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.feed import Feed
from app.models.item import Item


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


def test_private_and_team_visibility_are_membership_aware(client: TestClient, auth_headers):
    private = _create_investigation(client, auth_headers["analyst"])

    assert client.get(f"/investigations/{private['id']}", headers=auth_headers["admin"]).status_code == 404
    assert client.get(f"/investigations/{private['id']}", headers=auth_headers["viewer"]).status_code == 404

    viewer_list = client.get("/investigations", headers=auth_headers["viewer"])
    assert viewer_list.status_code == 200
    assert viewer_list.json()["total"] == 0

    team = _create_investigation(
        client,
        auth_headers["analyst"],
        title="Shared ransomware tracking",
        visibility="team",
    )
    viewer_detail = client.get(f"/investigations/{team['id']}", headers=auth_headers["viewer"])
    assert viewer_detail.status_code == 200
    assert viewer_detail.json()["current_user_role"] is None

    denied_update = client.patch(
        f"/investigations/{team['id']}",
        headers=auth_headers["viewer"],
        json={"expected_version": team["version"], "title": "Viewer edit"},
    )
    assert denied_update.status_code == 403
    assert "analyst or administrator" in denied_update.json()["detail"]


def test_membership_roles_and_final_owner_invariant(client: TestClient, auth_headers, seed_users):
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
        json={"expected_version": detail["version"], "description": "Updated by the assigned editor."},
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
        json={"expected_version": investigation["version"], "title": "First accepted title"},
    )
    assert first.status_code == 200

    stale = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"expected_version": investigation["version"], "title": "Stale overwrite"},
    )
    assert stale.status_code == 409
    assert "changed after you loaded it" in stale.json()["detail"]

    current = client.get(f"/investigations/{investigation['id']}", headers=auth_headers["analyst"])
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

    db_session.delete(item)
    db_session.commit()
    detail = client.get(f"/investigations/{investigation['id']}", headers=auth_headers["analyst"])
    assert detail.status_code == 200
    assert detail.json()["evidence"][0]["title_snapshot"] == "Observed credential theft infrastructure"


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


def test_archive_is_owner_only_read_only_and_reopenable(client: TestClient, auth_headers, seed_users):
    investigation = _create_investigation(client, auth_headers["analyst"], visibility="team")
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
        json={"body": "Must not be accepted", "expected_version": archived.json()["version"]},
    )
    assert archived_note.status_code == 409
    assert "read-only" in archived_note.json()["detail"]

    reopened = client.patch(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["analyst"],
        json={"expected_version": archived.json()["version"], "status": "open"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["archived_at"] is None


def test_member_candidates_are_narrow_and_writer_only(client: TestClient, auth_headers, seed_users):
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


def test_api_tokens_require_new_explicit_scope(client: TestClient, db_session, seed_users, auth_headers):
    investigation = _create_investigation(client, auth_headers["analyst"], visibility="team")
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


def test_mutations_emit_global_audit_records(client: TestClient, auth_headers, db_session):
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
