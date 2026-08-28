import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.routes import investigations as investigation_routes
from app.api.routes.investigations import MAX_INVESTIGATION_PAGE
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
    Investigation,
    InvestigationActivity,
    InvestigationEvidence,
    InvestigationMember,
    InvestigationNote,
)
from app.models.item import Item
from app.models.report import Report
from app.models.user import User
from app.services.investigation_collections import INVESTIGATION_DETAIL_COLLECTION_LIMIT
from app.services.investigations import (
    InvestigationActorNotEligibleError,
    InvestigationNotFoundError,
    InvestigationReadAuthorizationChangedError,
    add_note,
    eligible_investigation_owner_ids_query,
    get_investigation_detail,
    list_activity,
    list_evidence,
    list_notes,
    remove_member,
    update_investigation,
)
from app.services.user_access import (
    lock_users_for_security_change,
    revoke_user_credentials_with_counts,
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


def test_inflight_write_revalidates_actor_after_access_reduction(database_engine):
    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"investigation-owner-{uuid.uuid4()}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        editor = User(
            id=editor_id,
            email=f"investigation-editor-{uuid.uuid4()}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        investigation = Investigation(
            id=investigation_id,
            title="Concurrent access reduction",
            description="Ensure stale authorization cannot write analyst notes.",
            severity="high",
            visibility="private",
            created_by_user_id=owner_id,
        )
        setup_db.add_all([owner, editor])
        setup_db.flush()
        setup_db.add(investigation)
        setup_db.flush()
        setup_db.add_all(
            [
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=owner_id,
                    role="owner",
                    added_by_user_id=owner_id,
                ),
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=editor_id,
                    role="editor",
                    added_by_user_id=owner_id,
                ),
            ]
        )
        setup_db.commit()

    writer_started = Event()
    access_db = Session(database_engine)

    def _write_with_stale_actor() -> str:
        with Session(database_engine) as writer_db:
            stale_actor = writer_db.get(User, editor_id)
            assert stale_actor is not None and stale_actor.is_active is True
            writer_started.set()
            try:
                add_note(
                    writer_db,
                    investigation_id=investigation_id,
                    user=stale_actor,
                    body="This note must not commit after deactivation.",
                    expected_version=1,
                )
                writer_db.commit()
                return "committed"
            except InvestigationActorNotEligibleError as exc:
                writer_db.rollback()
                return exc.code

    try:
        editor = access_db.scalar(
            select(User).where(User.id == editor_id).with_for_update()
        )
        assert editor is not None
        editor.is_active = False
        access_db.add(editor)
        access_db.flush()

        with ThreadPoolExecutor(max_workers=1) as executor:
            writer = executor.submit(_write_with_stale_actor)
            assert writer_started.wait(timeout=2)
            time.sleep(0.1)
            assert not writer.done()
            access_db.commit()
            assert writer.result(timeout=5) == "investigation_actor_not_eligible"

        with Session(database_engine) as verify_db:
            investigation = verify_db.get(Investigation, investigation_id)
            note_count = verify_db.scalar(
                select(func.count(InvestigationNote.id)).where(
                    InvestigationNote.investigation_id == investigation_id
                )
            )
            assert investigation is not None and investigation.version == 1
            assert note_count == 0
    finally:
        access_db.rollback()
        access_db.close()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(Investigation).where(Investigation.id == investigation_id)
            )
            cleanup_db.execute(delete(User).where(User.id.in_([owner_id, editor_id])))
            cleanup_db.commit()


def test_mutation_response_is_materialized_before_membership_revocation(
    database_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        setup_db.add_all(
            [
                User(
                    id=owner_id,
                    email=f"response-owner-{uuid.uuid4().hex}@example.com",
                    password_hash="x",
                    role="admin",
                    is_active=True,
                    is_approved=True,
                ),
                User(
                    id=editor_id,
                    email=f"response-editor-{uuid.uuid4().hex}@example.com",
                    password_hash="x",
                    role="analyst",
                    is_active=True,
                    is_approved=True,
                ),
            ]
        )
        setup_db.flush()
        setup_db.add(
            Investigation(
                id=investigation_id,
                title="Original response title",
                description="Materialize the response before releasing write locks.",
                severity="high",
                visibility="private",
                created_by_user_id=owner_id,
            )
        )
        setup_db.flush()
        setup_db.add_all(
            [
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=owner_id,
                    role="owner",
                    added_by_user_id=owner_id,
                ),
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=editor_id,
                    role="editor",
                    added_by_user_id=owner_id,
                ),
            ]
        )
        setup_db.commit()

    response_materialized = Event()
    allow_writer_commit = Event()
    revoker_started = Event()
    real_get_detail = get_investigation_detail

    def _blocking_get_detail(*args, **kwargs):
        response = real_get_detail(*args, **kwargs)
        response_materialized.set()
        assert allow_writer_commit.wait(timeout=5)
        return response

    monkeypatch.setattr(
        investigation_routes,
        "get_investigation_detail",
        _blocking_get_detail,
    )

    def _write_and_respond():
        with Session(database_engine) as writer_db:
            editor = writer_db.get(User, editor_id)
            assert editor is not None
            investigation, _changed = update_investigation(
                writer_db,
                investigation_id=investigation_id,
                user=editor,
                expected_version=1,
                changes={"title": "Committed response title"},
            )
            return investigation_routes._commit_investigation_detail(
                writer_db,
                investigation_id=investigation.id,
                user=editor,
            )

    def _revoke_editor() -> str:
        assert response_materialized.wait(timeout=5)
        revoker_started.set()
        with Session(database_engine) as revoke_db:
            owner = revoke_db.get(User, owner_id)
            assert owner is not None
            remove_member(
                revoke_db,
                investigation_id=investigation_id,
                user=owner,
                member_user_id=editor_id,
                expected_version=2,
            )
            revoke_db.commit()
        return "revoked"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(_write_and_respond)
            assert response_materialized.wait(timeout=5)
            revoker = executor.submit(_revoke_editor)
            assert revoker_started.wait(timeout=5)
            time.sleep(0.1)
            assert not revoker.done()
            allow_writer_commit.set()

            response = writer.result(timeout=5)
            assert response.title == "Committed response title"
            assert response.current_user_role == "editor"
            assert revoker.result(timeout=5) == "revoked"

        with Session(database_engine) as verify_db:
            investigation = verify_db.get(Investigation, investigation_id)
            editor_membership = verify_db.get(
                InvestigationMember,
                {"investigation_id": investigation_id, "user_id": editor_id},
            )
            assert investigation is not None
            assert investigation.title == "Committed response title"
            assert investigation.version == 3
            assert editor_membership is None
    finally:
        allow_writer_commit.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(Investigation).where(Investigation.id == investigation_id)
            )
            cleanup_db.execute(delete(User).where(User.id.in_([owner_id, editor_id])))
            cleanup_db.commit()


def _run_private_composed_read(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    read_kind: str,
) -> None:
    if read_kind == "detail":
        get_investigation_detail(db, investigation_id=investigation_id, user=user)
        return
    if read_kind == "evidence":
        list_evidence(
            db,
            investigation_id=investigation_id,
            user=user,
            page=1,
            page_size=50,
        )
        return
    if read_kind == "notes":
        list_notes(
            db,
            investigation_id=investigation_id,
            user=user,
            page=1,
            page_size=50,
        )
        return
    if read_kind == "activity":
        list_activity(
            db,
            investigation_id=investigation_id,
            user=user,
            page=1,
            page_size=50,
        )
        return
    raise AssertionError(f"Unsupported composed read kind: {read_kind}")


@pytest.mark.parametrize(
    ("read_kind", "revocation_kind", "expected_error_code"),
    [
        pytest.param(
            "detail",
            "membership",
            "investigation_not_found",
            id="detail-membership",
        ),
        pytest.param(
            "evidence",
            "membership",
            "investigation_not_found",
            id="evidence-membership",
        ),
        pytest.param(
            "notes",
            "membership",
            "investigation_not_found",
            id="notes-membership",
        ),
        pytest.param(
            "activity",
            "membership",
            "investigation_not_found",
            id="activity-membership",
        ),
        pytest.param(
            "evidence",
            "role",
            "investigation_read_authorization_changed",
            id="evidence-role",
        ),
        pytest.param(
            "evidence",
            "active",
            "investigation_read_authorization_changed",
            id="evidence-active",
        ),
    ],
)
def test_private_composed_reads_fence_concurrent_access_revocation(
    database_engine,
    read_kind,
    revocation_kind,
    expected_error_code,
):
    owner_id = uuid.uuid4()
    reader_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        owner = User(
            id=owner_id,
            email=f"private-read-owner-{uuid.uuid4()}@example.com",
            password_hash="x",
            role="admin",
            is_active=True,
            is_approved=True,
        )
        reader = User(
            id=reader_id,
            email=f"private-read-reader-{uuid.uuid4()}@example.com",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        setup_db.add_all([owner, reader])
        setup_db.flush()
        setup_db.add(
            Investigation(
                id=investigation_id,
                title="Private composed read fence",
                description="Access changes must win before private data is returned.",
                severity="high",
                visibility="private",
                created_by_user_id=owner_id,
            )
        )
        setup_db.flush()
        setup_db.add_all(
            [
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=owner_id,
                    role="owner",
                    added_by_user_id=owner_id,
                ),
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=reader_id,
                    role="viewer",
                    added_by_user_id=owner_id,
                ),
                InvestigationEvidence(
                    investigation_id=investigation_id,
                    source_type="item",
                    source_id=uuid.uuid4(),
                    title_snapshot="Private evidence",
                    metadata_snapshot_json={},
                    added_by_user_id=owner_id,
                ),
                InvestigationNote(
                    investigation_id=investigation_id,
                    author_user_id=owner_id,
                    body="Private note",
                ),
                InvestigationActivity(
                    investigation_id=investigation_id,
                    actor_user_id=owner_id,
                    action="investigation.private_test",
                    details_json={},
                ),
            ]
        )
        setup_db.commit()

    reader_loaded = Event()
    revocation_ready = Event()
    read_attempted = Event()
    allow_revocation_commit = Event()

    def _read_with_stale_authorization() -> str:
        with Session(database_engine) as read_db:
            stale_reader = read_db.get(User, reader_id)
            assert stale_reader is not None
            reader_loaded.set()
            assert revocation_ready.wait(timeout=5)
            read_attempted.set()
            try:
                _run_private_composed_read(
                    read_db,
                    investigation_id=investigation_id,
                    user=stale_reader,
                    read_kind=read_kind,
                )
            except (
                InvestigationNotFoundError,
                InvestigationReadAuthorizationChangedError,
            ) as exc:
                read_db.rollback()
                return exc.code
            read_db.rollback()
            return "private_data_returned"

    def _revoke_access() -> str:
        assert reader_loaded.wait(timeout=5)
        with Session(database_engine) as revoke_db:
            if revocation_kind == "membership":
                owner = revoke_db.get(User, owner_id)
                assert owner is not None
                remove_member(
                    revoke_db,
                    investigation_id=investigation_id,
                    user=owner,
                    member_user_id=reader_id,
                    expected_version=1,
                )
            else:
                locked_users = lock_users_for_security_change(
                    revoke_db,
                    [owner_id, reader_id],
                )
                target = locked_users.get(reader_id)
                assert target is not None
                if revocation_kind == "role":
                    target.role = "viewer"
                elif revocation_kind == "active":
                    target.is_active = False
                else:
                    raise AssertionError(
                        f"Unsupported revocation kind: {revocation_kind}"
                    )
                revoke_user_credentials_with_counts(revoke_db, target)
                revoke_db.flush()
            revocation_ready.set()
            assert allow_revocation_commit.wait(timeout=5)
            revoke_db.commit()
        return "committed"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(_read_with_stale_authorization)
            revoker = executor.submit(_revoke_access)
            assert revocation_ready.wait(timeout=5)
            assert read_attempted.wait(timeout=5)
            time.sleep(0.1)
            assert not reader.done()
            allow_revocation_commit.set()
            assert revoker.result(timeout=5) == "committed"
            assert reader.result(timeout=5) == expected_error_code
    finally:
        allow_revocation_commit.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(Investigation).where(Investigation.id == investigation_id)
            )
            cleanup_db.execute(delete(User).where(User.id.in_([owner_id, reader_id])))
            cleanup_db.commit()


def test_team_composed_reads_remain_lock_free(database_engine):
    reader_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=reader_id,
                email=f"team-read-reader-{uuid.uuid4()}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup_db.flush()
        setup_db.add(
            Investigation(
                id=investigation_id,
                title="Lock-free team read",
                visibility="team",
                created_by_user_id=reader_id,
            )
        )
        setup_db.flush()
        setup_db.add(
            InvestigationEvidence(
                investigation_id=investigation_id,
                source_type="item",
                source_id=uuid.uuid4(),
                title_snapshot="Team evidence",
                metadata_snapshot_json={},
                added_by_user_id=reader_id,
            )
        )
        setup_db.commit()

    blocker_db = Session(database_engine)
    executor = ThreadPoolExecutor(max_workers=1)

    def _read_team_evidence() -> int:
        with Session(database_engine) as read_db:
            reader = read_db.get(User, reader_id)
            assert reader is not None
            response = list_evidence(
                read_db,
                investigation_id=investigation_id,
                user=reader,
                page=1,
                page_size=50,
            )
            return len(response.evidence)

    try:
        blocker_db.scalar(select(User).where(User.id == reader_id).with_for_update())
        blocker_db.scalar(
            select(Investigation)
            .where(Investigation.id == investigation_id)
            .with_for_update()
        )
        future = executor.submit(_read_team_evidence)
        assert future.result(timeout=2) == 1
    finally:
        blocker_db.rollback()
        blocker_db.close()
        executor.shutdown(wait=True)
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(Investigation).where(Investigation.id == investigation_id)
            )
            cleanup_db.execute(delete(User).where(User.id == reader_id))
            cleanup_db.commit()


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
    assert [len(response.json()["evidence"]) for response in evidence_pages] == [
        100,
        100,
        5,
    ]
    assert {
        entry["id"] for response in note_pages for entry in response.json()["notes"]
    } == {str(note_id) for note_id in note_ids}
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


def test_investigation_page_limits_return_validation_errors_and_accept_boundary(
    client: TestClient,
    auth_headers,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    excessive_page = MAX_INVESTIGATION_PAGE + 1
    paths = (
        "/investigations",
        "/investigations/member-candidates",
        f"/investigations/{investigation['id']}/activity",
    )

    for path in paths:
        rejected = client.get(
            path,
            params={"page": excessive_page, "page_size": 1},
            headers=auth_headers["analyst"],
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "validation_error"
        assert "less than or equal" in rejected.json()["error"]["message"]

        accepted = client.get(
            path,
            params={"page": MAX_INVESTIGATION_PAGE, "page_size": 1},
            headers=auth_headers["analyst"],
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["page"] == MAX_INVESTIGATION_PAGE


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
