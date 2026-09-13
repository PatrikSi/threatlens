import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.iam import IAMGroup, IAMGroupMembership
from app.core.api_errors import ApiHTTPException
from app.models.team import Team
from app.services.authorization import authorization_context_for_user
from app.services.team_access import (
    assert_current_team_access,
    require_team_access,
    team_access_predicate,
    team_member_user_ids_query,
)
from tests.integration.test_oidc_access_sync import _sync_fixture


def test_team_membership_and_manager_access_do_not_grant_outsiders_or_admins(
    db_session, seed_users
):
    member_group = IAMGroup(key=f"members-{uuid.uuid4().hex}", name="Members")
    manager_group = IAMGroup(key=f"managers-{uuid.uuid4().hex}", name="Managers")
    db_session.add_all([member_group, manager_group])
    db_session.flush()
    team = Team(
        key=f"team-{uuid.uuid4().hex}",
        name="Operations",
        membership_group_id=member_group.id,
        manager_group_id=manager_group.id,
    )
    db_session.add(team)
    db_session.add_all(
        [
            IAMGroupMembership(
                group_id=member_group.id, user_id=seed_users["viewer"].id
            ),
            IAMGroupMembership(
                group_id=manager_group.id, user_id=seed_users["analyst"].id
            ),
        ]
    )
    db_session.flush()

    def visible(role, *, manage=False):
        return db_session.scalar(
            select(Team.id).where(
                team_access_predicate(Team.id, seed_users[role].id, manage=manage)
            )
        )

    assert visible("viewer") == team.id
    assert visible("analyst") == team.id
    assert visible("admin") is None
    assert visible("viewer", manage=True) is None
    assert visible("analyst", manage=True) == team.id
    assert set(db_session.scalars(team_member_user_ids_query(team.id))) == {
        seed_users["viewer"].id,
        seed_users["analyst"].id,
    }
    viewer_context = authorization_context_for_user(db_session, seed_users["viewer"])
    assert viewer_context.has("read:teams")
    assert not viewer_context.has("write:teams")
    assert (
        require_team_access(
            db_session,
            team_id=team.id,
            user=seed_users["viewer"],
            authorization=viewer_context,
        ).id
        == team.id
    )
    with pytest.raises(ApiHTTPException) as denial:
        require_team_access(
            db_session,
            team_id=team.id,
            user=seed_users["viewer"],
            authorization=viewer_context,
            manage=True,
        )
    assert denial.value.status_code == 404


def test_team_access_disappears_after_membership_removal_or_deactivation(
    db_session, seed_users
):
    user = seed_users["analyst"]
    group = IAMGroup(key=f"members-{uuid.uuid4().hex}", name="Members")
    db_session.add(group)
    db_session.flush()
    team = Team(
        key=f"team-{uuid.uuid4().hex}", name="Operations", membership_group_id=group.id
    )
    member = IAMGroupMembership(group_id=group.id, user_id=user.id)
    db_session.add_all([team, member])
    db_session.flush()
    query = select(Team.id).where(team_access_predicate(Team.id, user.id))
    assert db_session.scalar(query) == team.id
    team.active = False
    db_session.flush()
    assert db_session.scalar(query) is None

    team.active = True
    db_session.delete(member)
    db_session.flush()
    assert db_session.scalar(query) is None


def test_team_membership_rechecks_oidc_assertion_expiry_without_cleanup(
    db_session, seed_users
):
    fixture = _sync_fixture(db_session, seed_users)
    mapping = fixture["group_mapping"]
    team = Team(
        key=f"team-{uuid.uuid4().hex}",
        name="Federated SOC",
        membership_group_id=fixture["group"].id,
    )
    member = IAMGroupMembership(
        group_id=fixture["group"].id,
        user_id=seed_users["viewer"].id,
        source="oidc",
        source_key=mapping.source_key,
        oidc_group_mapping_id=mapping.id,
        oidc_assertion_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db_session.add_all([team, member])
    db_session.flush()
    query = select(Team.id).where(
        team_access_predicate(Team.id, seed_users["viewer"].id)
    )
    assert db_session.scalar(query) == team.id
    member.oidc_assertion_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.flush()
    assert db_session.scalar(query) is None
    assert list(db_session.scalars(team_member_user_ids_query(team.id))) == []
    with pytest.raises(ApiHTTPException) as expired:
        assert_current_team_access(
            db_session, team_id=team.id, user_id=seed_users["viewer"].id
        )
    assert expired.value.status_code == 404
