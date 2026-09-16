from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.iam import IAMGroupMembership
from app.models.team import Team
from app.models.team_ai_context import TeamAIContext
from tests.integration.test_oidc_access_sync import _sync_fixture
from tests.integration.test_teams_api import _team


def _payload(**changes):
    return {
        "expected_version": 0,
        "technology_stack": ["Linux", "Kubernetes"],
        "priorities": ["Workload identities"],
        "available_telemetry": ["Kubernetes audit logs"],
        "relevance_criteria": "Prioritize authentication behavior affecting our deployed stack.",
        **changes,
    }


def _credential(db, user, scopes):
    value, prefix, digest = generate_api_token()
    db.add(
        ApiToken(
            user_id=user.id,
            name="Team context scope test",
            token_prefix=prefix,
            token_hash=digest,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()
    return {"Authorization": f"Bearer {value}"}


def test_unconfigured_team_context_is_an_empty_versioned_view_not_a_write(
    client, db_session, seed_users, auth_headers
):
    team, _, _ = _team(client, db_session, seed_users, auth_headers)
    url = f"/teams/{team['id']}/ai-context"
    response = client.get(url, headers=auth_headers["viewer"])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "team_id": team["id"],
        "technology_stack": [],
        "priorities": [],
        "available_telemetry": [],
        "relevance_criteria": "",
        "version": 0,
        "can_manage": False,
        "created_at": None,
        "updated_at": None,
    }
    assert db_session.get(TeamAIContext, uuid.UUID(team["id"])) is None
    assert client.get(url, headers=auth_headers["admin"]).json()["can_manage"] is True


def test_team_context_updates_have_independent_versions_and_private_audit_metadata(
    client, db_session, seed_users, auth_headers
):
    team, _, _ = _team(client, db_session, seed_users, auth_headers)
    url = f"/teams/{team['id']}/ai-context"
    created = client.patch(url, json=_payload(), headers=auth_headers["admin"])
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1
    assert created.json()["created_at"]
    assert created.json()["updated_at"]
    assert created.json()["can_manage"] is True
    member = client.get(url, headers=auth_headers["analyst"])
    assert member.status_code == 200
    assert member.json()["technology_stack"] == ["Linux", "Kubernetes"]
    assert member.json()["can_manage"] is False
    duplicate = client.patch(url, json=_payload(), headers=auth_headers["admin"])
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "team_ai_context_version_conflict"
    saved = client.patch(
        url,
        json=_payload(expected_version=1, priorities=["Cloud identity"]),
        headers=auth_headers["admin"],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    stale = client.patch(
        url,
        json=_payload(expected_version=1, priorities=["Old edits"]),
        headers=auth_headers["admin"],
    )
    assert stale.status_code == 409, stale.text
    assert client.get(url, headers=auth_headers["viewer"]).json()["priorities"] == [
        "Cloud identity"
    ]
    context = db_session.get(TeamAIContext, uuid.UUID(team["id"]))
    assert context.updated_by_user_id == seed_users["admin"].id
    assert db_session.get(Team, uuid.UUID(team["id"])).revision == 1
    events = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "teams.ai_context.update")
        .order_by(AuditLog.created_at, AuditLog.id)
    ).all()
    assert len(events) == 2
    assert {event.metadata_json["version"] for event in events} == {1, 2}
    assert all(
        set(event.metadata_json) == {"previous_version", "version"} for event in events
    )


def test_team_context_requires_both_current_membership_and_feature_scope(
    client, db_session, seed_users, auth_headers
):
    team, members, managers = _team(client, db_session, seed_users, auth_headers)
    url = f"/teams/{team['id']}/ai-context"
    denied_member = client.patch(url, json=_payload(), headers=auth_headers["analyst"])
    assert denied_member.status_code == 404, denied_member.text
    assert (
        client.patch(url, json=_payload(), headers=auth_headers["viewer"]).status_code
        == 403
    )
    unrelated_scope = _credential(db_session, seed_users["admin"], ["read:items"])
    assert client.get(url, headers=unrelated_scope).status_code == 403
    readonly = _credential(db_session, seed_users["admin"], ["read:teams"])
    response = client.get(url, headers=readonly)
    assert response.status_code == 200, response.text
    assert response.json()["can_manage"] is False
    assert client.patch(url, json=_payload(), headers=readonly).status_code == 403
    manager_membership = db_session.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == managers.id,
            IAMGroupMembership.user_id == seed_users["admin"].id,
        )
    )
    db_session.delete(manager_membership)
    db_session.commit()
    assert client.get(url, headers=auth_headers["admin"]).status_code == 404
    assert (
        client.patch(url, json=_payload(), headers=auth_headers["admin"]).status_code
        == 404
    )
    member_membership = db_session.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.group_id == members.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.delete(member_membership)
    db_session.commit()
    assert client.get(url, headers=auth_headers["analyst"]).status_code == 404


@pytest.mark.parametrize("method", ["get", "patch"])
def test_inactive_team_context_is_unavailable(
    client, db_session, seed_users, auth_headers, method
):
    team, _, _ = _team(client, db_session, seed_users, auth_headers)
    row = db_session.get(Team, uuid.UUID(team["id"]))
    row.active = False
    db_session.commit()
    kwargs = {"json": _payload()} if method == "patch" else {}
    response = getattr(client, method)(
        f"/teams/{team['id']}/ai-context", headers=auth_headers["admin"], **kwargs
    )
    assert response.status_code == 404, response.text


def test_team_context_membership_is_rechecked_after_context_lock_wait(
    client, db_session, seed_users, auth_headers, monkeypatch
):
    from app.services import team_ai_context

    team, _, _ = _team(client, db_session, seed_users, auth_headers)
    oidc = _sync_fixture(db_session, seed_users)
    mapping = oidc["group_mapping"]
    db_session.get(Team, uuid.UUID(team["id"])).manager_group_id = oidc["group"].id
    manager_membership = IAMGroupMembership(
        group_id=oidc["group"].id,
        user_id=seed_users["admin"].id,
        source="oidc",
        source_key=mapping.source_key,
        oidc_group_mapping_id=mapping.id,
        oidc_assertion_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(manager_membership)
    db_session.commit()
    require_access = team_ai_context.require_team_access

    def expire_after_team_lock(*args, **kwargs):
        result = require_access(*args, **kwargs)
        manager_membership.oidc_assertion_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
        db_session.flush()
        return result

    monkeypatch.setattr(team_ai_context, "require_team_access", expire_after_team_lock)
    response = client.patch(
        f"/teams/{team['id']}/ai-context",
        json=_payload(),
        headers=auth_headers["admin"],
    )
    assert response.status_code == 404, response.text
    assert db_session.get(TeamAIContext, uuid.UUID(team["id"])) is None
