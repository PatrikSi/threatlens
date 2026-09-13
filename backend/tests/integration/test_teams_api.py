from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.investigation import InvestigationMember
from app.models.saved_view import SavedView


def _team(client, db, users, headers):
    members = IAMGroup(key=f"team-members-{uuid.uuid4().hex}", name="Team members")
    managers = IAMGroup(key=f"team-managers-{uuid.uuid4().hex}", name="Team managers")
    db.add_all([members, managers])
    db.flush()
    db.add_all(
        [
            IAMGroupMembership(group_id=members.id, user_id=users["analyst"].id),
            IAMGroupMembership(group_id=members.id, user_id=users["viewer"].id),
            IAMGroupMembership(group_id=managers.id, user_id=users["admin"].id),
        ]
    )
    db.commit()
    response = client.post(
        "/teams",
        json={
            "key": f"team-{uuid.uuid4().hex}",
            "name": "SOC",
            "membership_group_id": str(members.id),
            "manager_group_id": str(managers.id),
        },
        headers=headers["admin"],
    )
    assert response.status_code == 201, response.text
    return response.json(), members, managers


def _view_payload(team_id):
    return {
        "name": "Shared monitoring",
        "team_id": team_id,
        "query_json": {
            "windows": [
                {
                    "id": "rss-1",
                    "type": "rss",
                    "title": "Headlines",
                    "rect": {"x": 0, "y": 0, "width": 800, "height": 500},
                    "scratch_note": "Private notebook",
                    "selected_daily_brief_id": str(uuid.uuid4()),
                }
            ]
        },
    }


def test_team_metadata_delegation_conflicts_and_group_binding_protection(
    client, db_session, seed_users, auth_headers
):
    team, members, managers = _team(client, db_session, seed_users, auth_headers)
    member_list = client.get("/teams", headers=auth_headers["analyst"])
    assert member_list.status_code == 200, member_list.text
    assert member_list.json()["items"][0]["id"] == team["id"]
    assert member_list.json()["items"][0]["can_manage"] is False
    payload = {
        "expected_revision": 1,
        "name": "Updated SOC",
        "description": "Team metadata",
    }
    assert (
        client.patch(
            f"/teams/{team['id']}", json=payload, headers=auth_headers["analyst"]
        ).status_code
        == 404
    )
    saved = client.patch(
        f"/teams/{team['id']}", json=payload, headers=auth_headers["admin"]
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert (
        client.patch(
            f"/teams/{team['id']}", json=payload, headers=auth_headers["admin"]
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/teams/{team['id']}",
            json={
                **payload,
                "expected_revision": 2,
                "membership_group_id": str(managers.id),
            },
            headers=auth_headers["admin"],
        ).status_code
        == 422
    )
    deleted_group = client.delete(
        f"/iam/groups/{members.id}?expected_revision={members.revision}",
        headers=auth_headers["admin"],
    )
    assert deleted_group.status_code == 409, deleted_group.text
    assert "Rebind" in deleted_group.json()["detail"]
    member_page = client.get(
        f"/teams/{team['id']}/members?page_size=2", headers=auth_headers["viewer"]
    )
    assert member_page.status_code == 200, member_page.text
    assert len(member_page.json()["items"]) == 2
    assert member_page.json()["total"] == 3
    assert (
        client.get("/teams?page=1000001", headers=auth_headers["analyst"]).status_code
        == 422
    )
    assert (
        client.get(
            f"/teams/{team['id']}/members?page=1000001", headers=auth_headers["analyst"]
        ).status_code
        == 422
    )


def test_shared_views_omit_private_state_and_require_current_membership_and_revision(
    client, db_session, seed_users, auth_headers
):
    team, members, _ = _team(client, db_session, seed_users, auth_headers)
    created = client.post(
        "/views", json=_view_payload(team["id"]), headers=auth_headers["analyst"]
    )
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["user_id"] is None
    assert view["revision"] == 1
    assert view["can_edit"] is True
    assert view["can_delete"] is True
    readonly = client.get("/views", headers=auth_headers["viewer"]).json()[0]
    assert readonly["can_edit"] is False
    assert readonly["can_delete"] is False
    assert view["query_json"]["windows"][0]["scratch_note"] == ""
    assert view["query_json"]["windows"][0]["selected_daily_brief_id"] is None
    assert (
        client.post(
            "/views", json=_view_payload(team["id"]), headers=auth_headers["viewer"]
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/views/{view['id']}",
            json={"name": "Changed"},
            headers=auth_headers["analyst"],
        ).status_code
        == 428
    )
    assert (
        client.patch(
            f"/views/{view['id']}",
            json={"name": "Changed", "expected_revision": 1},
            headers=auth_headers["analyst"],
        ).status_code
        == 200
    )
    stale = client.patch(
        f"/views/{view['id']}",
        json={"name": "Stale", "expected_revision": 1},
        headers=auth_headers["admin"],
    )
    assert stale.status_code == 409, stale.text
    member = db_session.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == members.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.delete(member)
    db_session.commit()
    assert client.get("/views", headers=auth_headers["analyst"]).json() == []
    assert (
        client.patch(
            f"/views/{view['id']}",
            json={"name": "Removed member", "expected_revision": 2},
            headers=auth_headers["analyst"],
        ).status_code
        == 404
    )
    assert db_session.get(SavedView, uuid.UUID(view["id"])).name == "Changed"


def test_team_investigations_use_current_groups_and_separate_feature_scopes(
    client, db_session, seed_users, auth_headers
):
    team, members, _ = _team(client, db_session, seed_users, auth_headers)
    created = client.post(
        "/investigations",
        json={"title": "Team incident", "team_id": team["id"]},
        headers=auth_headers["admin"],
    )
    assert created.status_code == 201, created.text
    investigation = created.json()
    id_ = investigation["id"]
    assert investigation["team_id"] == team["id"]
    assert investigation["member_count"] == 3
    assert (
        client.get(f"/investigations/{id_}", headers=auth_headers["analyst"]).json()[
            "current_user_role"
        ]
        == "editor"
    )
    note = client.post(
        f"/investigations/{id_}/notes",
        json={"body": "Current evidence", "expected_version": 1},
        headers=auth_headers["analyst"],
    )
    assert note.status_code == 200, note.text
    value, prefix, digest = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=seed_users["analyst"].id,
            name="Feature-only",
            token_prefix=prefix,
            token_hash=digest,
            scopes=["write:investigations"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db_session.commit()
    restricted = client.post(
        f"/investigations/{id_}/notes",
        json={"body": "No team scope", "expected_version": 2},
        headers={"Authorization": f"Bearer {value}"},
    )
    assert restricted.status_code == 403, restricted.text
    assert restricted.json()["error"]["code"] == "team_write_permission_required"
    # Historical explicit ownership must never outlive current named-team access.
    db_session.add(
        InvestigationMember(
            investigation_id=uuid.UUID(id_),
            user_id=seed_users["analyst"].id,
            role="owner",
        )
    )
    member = db_session.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == members.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.delete(member)
    db_session.commit()
    assert (
        client.get(
            f"/investigations/{id_}", headers=auth_headers["analyst"]
        ).status_code
        == 404
    )
    assert (
        client.get("/investigations", headers=auth_headers["analyst"]).json()[
            "investigations"
        ]
        == []
    )
    denied = client.post(
        f"/investigations/{id_}/notes",
        json={"body": "Former owner", "expected_version": 2},
        headers=auth_headers["analyst"],
    )
    assert denied.status_code == 404, denied.text


def test_admin_team_detail_is_independent_of_list_pagination_and_content_access(
    client, db_session, seed_users, auth_headers
):
    team, _, managers = _team(client, db_session, seed_users, auth_headers)
    membership = db_session.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == managers.id,
            IAMGroupMembership.user_id == seed_users["admin"].id,
        )
    )
    db_session.delete(membership)
    db_session.commit()
    assert (
        client.get(f"/teams/{team['id']}", headers=auth_headers["admin"]).status_code
        == 404
    )
    response = client.get(f"/teams/admin/{team['id']}", headers=auth_headers["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["can_manage"] is False
    assert (
        client.get(
            f"/teams/admin/{team['id']}", headers=auth_headers["analyst"]
        ).status_code
        == 403
    )


def test_independent_investigation_writers_do_not_upgrade_shared_iam_fences(
    database_engine,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.models.investigation import Investigation
    from app.models.user import User
    from app.services.authorization import (
        authorization_context_for_user,
        fence_authorization_context,
    )
    from app.services.investigations import add_note
    from tests.integration.test_investigations_api import _disabled_data_access

    user_ids = [uuid.uuid4(), uuid.uuid4()]
    investigation_ids = [uuid.uuid4(), uuid.uuid4()]
    with Session(database_engine) as setup:
        for user_id, investigation_id in zip(user_ids, investigation_ids, strict=True):
            setup.add(
                User(
                    id=user_id,
                    email=f"independent-writer-{user_id}@example.test",
                    password_hash="x",
                    role="analyst",
                    is_active=True,
                    is_approved=True,
                )
            )
            setup.flush()
            setup.add(
                Investigation(
                    id=investigation_id,
                    title="Independent workspace",
                    created_by_user_id=user_id,
                )
            )
            setup.flush()
            setup.add(
                InvestigationMember(
                    investigation_id=investigation_id,
                    user_id=user_id,
                    role="owner",
                    added_by_user_id=user_id,
                )
            )
        setup.commit()
    barrier = Barrier(2)

    def write(index):
        with Session(database_engine) as db:
            actor = db.get(User, user_ids[index])
            fence_authorization_context(db, authorization_context_for_user(db, actor))
            barrier.wait(timeout=5)
            add_note(
                db,
                investigation_id=investigation_ids[index],
                user=actor,
                data_access=_disabled_data_access(actor),
                body="Concurrent independent note",
                expected_version=1,
            )
            db.commit()
            return True

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, index) for index in range(2)]
            assert all(future.result(timeout=10) for future in futures)
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(Investigation).where(Investigation.id.in_(investigation_ids))
            )
            cleanup.execute(delete(User).where(User.id.in_(user_ids)))
            cleanup.commit()
