"""Investigation creation rechecks membership after PostgreSQL team row waits."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
import uuid

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.models.data_policy import DataAccessEnvelope
from app.models.iam import IAMGroup, IAMGroupMembership, IAMRole
from app.models.investigation import Investigation
from app.models.oidc import OIDCProvider
from app.models.team import Team
from app.models.user import User
from app.services.investigations import (
    InvestigationNotFoundError,
    create_investigation,
)
from app.services.team_access import team_access_predicate
from tests.integration.test_oidc_access_sync import _sync_fixture


def test_investigation_creation_rejects_oidc_membership_expired_during_team_lock_wait(
    database_engine,
):
    user_id, team_id = uuid.uuid4(), uuid.uuid4()
    application_name = f"investigation-expiry-{user_id.hex}"
    with Session(database_engine) as setup:
        user = User(
            id=user_id,
            email=f"investigation-expiry-{user_id}@example.test",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        setup.add(user)
        setup.flush()
        identity = _sync_fixture(setup, {"admin": user, "viewer": user})
        group_id, provider_id = identity["group"].id, identity["provider"].id
        role_ids = [identity["mapped_role"].id, identity["local_assignment"].role_id]
        mapping = identity["group_mapping"]
        membership = IAMGroupMembership(
            group_id=group_id,
            user_id=user_id,
            source="oidc",
            source_key=mapping.source_key,
            oidc_group_mapping_id=mapping.id,
            oidc_assertion_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        setup.add_all([
            membership,
            Team(
                id=team_id,
                key=f"expiry-{team_id.hex}",
                name="Federated investigations",
                membership_group_id=group_id,
            ),
        ])
        setup.flush()
        membership_id = membership.id
        setup.commit()

    def create():
        with Session(database_engine) as db:
            db.scalar(select(func.set_config("application_name", application_name, True)))
            actor = db.get(User, user_id)
            try:
                create_investigation(
                    db,
                    user=actor,
                    title="Expired assertion must not create an investigation",
                    description="",
                    severity="medium",
                    visibility="team",
                    assignee_user_id=None,
                    team_id=team_id,
                )
                db.commit()
                return "created"
            except InvestigationNotFoundError:
                db.rollback()
                return "not_found"

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            # Release the blocker before waiting for executor shutdown, even if
            # a failed assertion interrupts the concurrency probe.
            with Session(database_engine) as blocker:
                blocker.scalar(select(Team).where(Team.id == team_id).with_for_update())
                blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
                with Session(database_engine) as setup:
                    membership = setup.get(IAMGroupMembership, membership_id)
                    expires_at = setup.scalar(select(func.clock_timestamp())) + timedelta(seconds=5)
                    membership.oidc_assertion_expires_at = expires_at
                    setup.commit()
                future = executor.submit(create)
                with Session(database_engine) as monitor:
                    waiting = False
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline:
                        waiting = bool(monitor.scalar(text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = :name "
                            "AND :blocker = ANY(pg_blocking_pids(pid)))"
                        ), {"name": application_name, "blocker": blocker_pid}))
                        monitor.rollback()
                        if waiting:
                            break
                        time.sleep(0.02)
                    assert waiting, "Creation must block on the team row while membership is valid."
                    assert not future.done()
                    while monitor.scalar(select(func.clock_timestamp())) <= expires_at:
                        monitor.rollback()
                        time.sleep(0.05)
                    assert not monitor.scalar(select(team_access_predicate(team_id, user_id)))
                blocker.rollback()
                assert future.result(timeout=5) == "not_found"
        with Session(database_engine) as verify:
            assert verify.scalar(select(Investigation.id).where(Investigation.team_id == team_id)) is None
    finally:
        with Session(database_engine) as cleanup:
            investigation_ids = select(Investigation.id).where(Investigation.team_id == team_id)
            cleanup.execute(delete(DataAccessEnvelope).where(
                DataAccessEnvelope.resource_type == "investigation",
                DataAccessEnvelope.resource_id.in_(investigation_ids),
            ))
            cleanup.execute(delete(Investigation).where(Investigation.team_id == team_id))
            cleanup.execute(delete(Team).where(Team.id == team_id))
            cleanup.execute(delete(IAMGroupMembership).where(IAMGroupMembership.user_id == user_id))
            cleanup.execute(delete(OIDCProvider).where(OIDCProvider.id == provider_id))
            cleanup.execute(delete(IAMGroup).where(IAMGroup.id == group_id))
            cleanup.execute(delete(IAMRole).where(IAMRole.id.in_(role_ids)))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()
