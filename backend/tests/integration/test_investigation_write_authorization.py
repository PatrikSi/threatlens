"""Accepted credentials and expiring grants remain boundaries through commit."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.core.security import generate_api_token
from app.db.session import get_db
from app.main import app
from app.models.api_token import ApiToken
from app.models.data_policy import DataAccessEnvelope
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.investigation import Investigation, InvestigationNote
from app.models.oidc import OIDCProvider
from app.models.team import Team
from app.models.user import User
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.investigation_contracts import InvestigationActorNotEligibleError
from app.services.investigation_membership import assert_current_investigation_write
from app.services.investigations import add_note, create_investigation
from tests.integration.test_oidc_access_sync import _sync_fixture


@pytest.fixture
def write_access_fixture(database_engine, request):
    """Committed fixtures allow independent request and blocker transactions."""
    expiring_permission = request.param
    user_id, team_id = uuid.uuid4(), uuid.uuid4()
    with Session(database_engine) as db:
        user = User(
            id=user_id,
            email=f"investigation-write-expiry-{user_id}@example.test",
            password_hash="x",
            role="viewer",
            is_active=True,
            is_approved=True,
        )
        db.add(user)
        db.flush()
        identity = _sync_fixture(db, {"admin": user, "viewer": user})
        group_id, provider_id = identity["group"].id, identity["provider"].id
        role_ids = [identity["mapped_role"].id, identity["local_assignment"].role_id]
        for permission in ("write:investigations", "write:teams"):
            db.add(IAMRolePermission(role_id=role_ids[0], permission=permission))
            if permission != expiring_permission:
                db.add(IAMRolePermission(role_id=role_ids[1], permission=permission))
        role_mapping = identity["role_mapping"]
        grant = IAMUserRoleAssignment(
            user_id=user_id,
            role_id=role_mapping.role_id,
            source="oidc",
            source_key=role_mapping.source_key,
            oidc_role_mapping_id=role_mapping.id,
            oidc_assertion_expires_at=db.scalar(select(func.clock_timestamp()))
            + timedelta(minutes=1),
        )
        token, prefix, token_hash = generate_api_token()
        db.add_all(
            [
                grant,
                IAMGroupMembership(group_id=group_id, user_id=user_id),
                Team(
                    id=team_id,
                    key=f"write-expiry-{team_id.hex}",
                    name="Investigation writers",
                    membership_group_id=group_id,
                ),
                ApiToken(
                    user_id=user_id,
                    name="Investigation writer",
                    token_prefix=prefix,
                    token_hash=token_hash,
                    scopes=["write:investigations", "write:teams"],
                    last_used_at=db.scalar(select(func.clock_timestamp())),
                ),
            ]
        )
        db.flush()
        investigation = create_investigation(
            db,
            user=user,
            title="Existing investigation",
            description="",
            severity="medium",
            visibility="team",
            team_id=team_id,
            assignee_user_id=None,
        )
        authorization = authorization_context_for_user(db, user)
        note = add_note(
            db,
            investigation_id=investigation.id,
            user=user,
            data_access=data_access_context_for_authorization(db, authorization),
            body="Original note",
            expected_version=investigation.version,
        )
        fixture = {
            "user_id": user_id,
            "team_id": team_id,
            "grant_id": grant.id,
            "token": token,
            "investigation_id": investigation.id,
            "version": investigation.version,
            "note_id": note.id,
            "expires": expiring_permission is not None,
        }
        db.commit()
    try:
        yield fixture
    finally:
        with Session(database_engine) as db:
            investigation_ids = select(Investigation.id).where(
                Investigation.team_id == team_id
            )
            db.execute(
                delete(DataAccessEnvelope).where(
                    DataAccessEnvelope.resource_type == "investigation",
                    DataAccessEnvelope.resource_id.in_(investigation_ids),
                )
            )
            db.execute(delete(Investigation).where(Investigation.team_id == team_id))
            db.execute(delete(Team).where(Team.id == team_id))
            db.execute(
                delete(IAMGroupMembership).where(IAMGroupMembership.user_id == user_id)
            )
            db.execute(delete(OIDCProvider).where(OIDCProvider.id == provider_id))
            db.execute(delete(IAMGroup).where(IAMGroup.id == group_id))
            db.execute(delete(IAMRole).where(IAMRole.id.in_(role_ids)))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()


@pytest.mark.parametrize("operation", ["create", "add_note", "edit_note"])
@pytest.mark.parametrize(
    "write_access_fixture", ["write:investigations", "write:teams", None], indirect=True
)
def test_http_mutation_rechecks_feature_grants_after_resource_wait(
    database_engine,
    write_access_fixture,
    operation,
    _install_test_redis_backend,
):
    fixture = write_access_fixture
    expires = fixture["expires"]
    application_name = f"investigation-write-{fixture['user_id'].hex}"
    request_engine = create_engine(
        database_engine.url, connect_args={"application_name": application_name}
    )

    def request_db():
        with Session(request_engine) as db:
            yield db

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = request_db
    path = f"/investigations/{fixture['investigation_id']}/notes"
    if operation == "create":
        model, row_id = Team, fixture["team_id"]
        method, path = "POST", "/investigations"
        body = {
            "title": "New investigation",
            "visibility": "team",
            "team_id": str(row_id),
        }
    elif operation == "add_note":
        model, row_id = Investigation, fixture["investigation_id"]
        method = "POST"
        body = {"body": "New note", "expected_version": fixture["version"]}
    else:
        model, row_id = InvestigationNote, fixture["note_id"]
        method, path = "PATCH", f"{path}/{row_id}"
        body = {
            "body": "Edited note",
            "expected_note_version": 1,
            "expected_investigation_version": fixture["version"],
        }
    try:
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
            # Close the blocker before joining the worker on assertion failures.
            with Session(database_engine) as blocker:
                blocker.scalar(
                    select(model).where(model.id == row_id).with_for_update()
                )
                blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
                with Session(database_engine) as setup:
                    grant = setup.get(IAMUserRoleAssignment, fixture["grant_id"])
                    expires_at = setup.scalar(
                        select(func.clock_timestamp())
                    ) + timedelta(seconds=5)
                    grant.oidc_assertion_expires_at = expires_at
                    setup.commit()
                future = executor.submit(
                    client.request,
                    method,
                    path,
                    json=body,
                    headers={"Authorization": f"Bearer {fixture['token']}"},
                )
                with Session(database_engine) as monitor:
                    waiting = False
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline:
                        waiting = bool(
                            monitor.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE application_name = :name "
                                    "AND :blocker = ANY(pg_blocking_pids(pid)))"
                                ),
                                {"name": application_name, "blocker": blocker_pid},
                            )
                        )
                        monitor.rollback()
                        if waiting:
                            break
                        if future.done():
                            result = future.result()
                            pytest.fail(
                                f"Request finished before the lock wait: {result.status_code} {result.text}"
                            )
                        time.sleep(0.02)
                    assert waiting, (
                        "Mutation must reach its resource lock while the grant is valid."
                    )
                    if expires:
                        while (
                            monitor.scalar(select(func.clock_timestamp())) <= expires_at
                        ):
                            monitor.rollback()
                            time.sleep(0.05)
                blocker.rollback()
                response = future.result(timeout=5)
            if expires:
                assert response.status_code == 403, response.text
                assert (
                    response.headers["x-error-code"]
                    == "investigation_actor_not_eligible"
                )
                assert "expired or changed" in response.text
            else:
                assert response.status_code == (
                    201 if operation == "create" else 200
                ), response.text
        with Session(database_engine) as verify:
            assert verify.scalar(
                select(func.count())
                .select_from(Investigation)
                .where(Investigation.team_id == fixture["team_id"])
            ) == (2 if operation == "create" and not expires else 1)
            assert verify.scalar(
                select(func.count())
                .select_from(InvestigationNote)
                .where(
                    InvestigationNote.investigation_id == fixture["investigation_id"]
                )
            ) == (2 if operation == "add_note" and not expires else 1)
            note = verify.get(InvestigationNote, fixture["note_id"])
            assert note.body == (
                "Edited note"
                if operation == "edit_note" and not expires
                else "Original note"
            )
            investigation = verify.get(Investigation, fixture["investigation_id"])
            assert investigation.version == fixture["version"] + int(
                operation != "create" and not expires
            )
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        request_engine.dispose()


@pytest.mark.parametrize(
    ("scopes", "team", "accepted"),
    [
        (["read:investigations"], False, False),
        (["write:investigations"], False, True),
        (["write:investigations"], True, False),
        (["write:investigations", "write:teams"], True, True),
    ],
)
def test_commit_checkpoint_preserves_accepted_credential_cap(
    db_session, seed_users, scopes, team, accepted
):
    user = seed_users["admin"]
    team_id = None
    if team:
        group = IAMGroup(key=f"write-cap-{uuid.uuid4().hex}", name="Scoped writers")
        db_session.add(group)
        db_session.flush()
        named_team = Team(
            key=f"write-cap-{uuid.uuid4().hex}",
            name="Scoped team",
            membership_group_id=group.id,
        )
        db_session.add_all(
            [named_team, IAMGroupMembership(group_id=group.id, user_id=user.id)]
        )
        db_session.flush()
        team_id = named_team.id
    authorization = authorization_context_for_user(
        db_session, user, credential_scopes=scopes
    )
    if accepted:
        assert_current_investigation_write(
            db_session, user=user, authorization=authorization, team_id=team_id
        )
    else:
        with pytest.raises(
            InvestigationActorNotEligibleError, match="accepting credential"
        ):
            assert_current_investigation_write(
                db_session, user=user, authorization=authorization, team_id=team_id
            )


@pytest.mark.parametrize("write_access_fixture", [None], indirect=True)
def test_commit_checkpoint_rechecks_current_team_membership(
    database_engine, write_access_fixture
):
    fixture = write_access_fixture
    with Session(database_engine) as db:
        user = db.get(User, fixture["user_id"])
        authorization = authorization_context_for_user(db, user)
        assert_current_investigation_write(
            db, user=user, authorization=authorization, team_id=fixture["team_id"]
        )
        db.execute(
            delete(IAMGroupMembership).where(IAMGroupMembership.user_id == user.id)
        )
        with pytest.raises(ApiHTTPException) as exc:
            assert_current_investigation_write(
                db, user=user, authorization=authorization, team_id=fixture["team_id"]
            )
        assert exc.value.status_code == 404
