from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, update

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.data_policy import (
    DataPolicyRoleGrant,
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.schemas.data_policy import (
    HandlingLabelCreateRequest,
    HandlingLabelRoleGrantsRequest,
    HandlingLabelStatusRequest,
    HandlingLabelUpdateRequest,
)
from app.schemas.iam import RoleWriteRequest
from app.services import data_access_policy as policy_service
from app.services.authorization import EffectiveRole, authorization_context_for_user
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyActivationBlocked,
    DataPolicyConflict,
    DataPolicyRevisionConflict,
    DataPolicyUnavailable,
    HandlingLabelRevisionConflict,
    assign_feed_handling_label,
    create_handling_label,
    data_access_context_for_authorization,
    data_policy_overview,
    fence_data_access_context,
    replace_handling_label_role_grants,
    set_handling_label_status,
    update_data_policy_mode,
    update_handling_label,
)
from app.services.iam_roles import IAMRoleConflict, create_role, delete_role


def _feed(name: str, url: str) -> Feed:
    feed = Feed(name=name)
    feed.url = url
    return feed


def test_foundation_is_backward_compatible_and_activation_is_blocked(db_session):
    overview = data_policy_overview(db_session)

    assert overview.state.mode == "disabled"
    assert overview.state.revision == 1
    assert overview.state.coverage_version == 0
    assert overview.labels[0].id == UNRESTRICTED_HANDLING_LABEL_ID
    assert overview.labels[0].is_unrestricted is True
    assert overview.preflight.ready_for_audit is False
    assert overview.preflight.ready_for_enforcement is False
    assert {blocker.code for blocker in overview.preflight.blockers} == {
        "coverage_incomplete"
    }

    with pytest.raises(DataPolicyActivationBlocked, match="cannot be enabled"):
        update_data_policy_mode(
            db_session,
            mode="enforced",
            expected_revision=overview.state.revision,
            actor_user_id=SYSTEM_ROLE_IDS["admin"],
        )


def test_data_access_context_fence_rejects_a_stale_policy_snapshot(
    db_session,
    seed_users,
):
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    context = DataAccessContext(
        mode="disabled",
        policy_revision=state.revision,
        coverage_version=state.coverage_version,
        principal_type="user",
        principal_id=seed_users["viewer"].id,
        principal_eligible=True,
        allowed_label_ids=frozenset(),
    )

    fence_data_access_context(db_session, context)
    state.revision += 1
    db_session.flush()

    with pytest.raises(DataPolicyRevisionConflict) as raised:
        fence_data_access_context(db_session, context)

    assert raised.value.current_revision == context.policy_revision + 1


def test_label_grants_feed_assignment_and_archive_invariants(db_session, seed_users):
    feed = _feed("Restricted source", "https://example.com/restricted.xml")
    db_session.add(feed)
    db_session.flush()
    assert feed.handling_label_id == QUARANTINE_HANDLING_LABEL_ID

    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="tlp-red",
            name="TLP Red",
            description="Named handling policy for a restricted source.",
            color="#D92D20",
            role_ids=[SYSTEM_ROLE_IDS["analyst"]],
        ),
        actor_user_id=seed_users["admin"].id,
    )
    assert created.changed is True
    assert created.policy_revision == 2
    assert set(created.label.role_ids) == {
        SYSTEM_ROLE_IDS["admin"],
        SYSTEM_ROLE_IDS["analyst"],
    }

    with pytest.raises(DataPolicyRevisionConflict):
        assign_feed_handling_label(
            db_session,
            feed_id=feed.id,
            handling_label_id=created.label.id,
            expected_policy_revision=1,
            actor_user_id=seed_users["admin"].id,
        )

    assigned = assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=created.label.id,
        expected_policy_revision=2,
        actor_user_id=seed_users["admin"].id,
    )
    assert assigned.changed is True
    assert assigned.policy_revision == 3

    with pytest.raises(DataPolicyConflict, match="Reassign every feed"):
        set_handling_label_status(
            db_session,
            label_id=created.label.id,
            payload=HandlingLabelStatusRequest(
                expected_revision=created.label.revision,
                active=False,
            ),
            actor_user_id=seed_users["admin"].id,
        )

    with pytest.raises(
        policy_service.DataPolicyValidationError,
        match="administrator",
    ):
        replace_handling_label_role_grants(
            db_session,
            label_id=created.label.id,
            payload=HandlingLabelRoleGrantsRequest(
                expected_revision=created.label.revision,
                role_ids=[SYSTEM_ROLE_IDS["analyst"]],
            ),
            actor_user_id=seed_users["admin"].id,
        )

    unchanged = replace_handling_label_role_grants(
        db_session,
        label_id=created.label.id,
        payload=HandlingLabelRoleGrantsRequest(
            expected_revision=created.label.revision,
            role_ids=list(created.label.role_ids),
        ),
        actor_user_id=seed_users["admin"].id,
    )
    assert unchanged.changed is False
    assert unchanged.policy_revision == 3

    reassigned = assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        expected_policy_revision=3,
        actor_user_id=seed_users["admin"].id,
    )
    archived = set_handling_label_status(
        db_session,
        label_id=created.label.id,
        payload=HandlingLabelStatusRequest(
            expected_revision=created.label.revision,
            active=False,
        ),
        actor_user_id=seed_users["admin"].id,
    )
    assert reassigned.policy_revision == 4
    assert archived.policy_revision == 5
    assert archived.label.is_active is False


def test_effective_label_access_uses_canonical_roles_and_fails_closed(
    db_session, seed_users, monkeypatch
):
    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="restricted-research",
            name="Restricted research",
            role_ids=[SYSTEM_ROLE_IDS["analyst"]],
        ),
        actor_user_id=seed_users["admin"].id,
    )
    analyst_authorization = authorization_context_for_user(
        db_session, seed_users["analyst"]
    )
    viewer_authorization = authorization_context_for_user(
        db_session, seed_users["viewer"]
    )
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = "enforced"
    state.coverage_version = 1
    state.enforced_at = datetime.now(timezone.utc)
    state.enforced_by_user_id = seed_users["admin"].id
    db_session.flush()

    with pytest.raises(DataPolicyUnavailable, match="incompatible"):
        data_access_context_for_authorization(db_session, analyst_authorization)

    monkeypatch.setattr(
        policy_service,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )
    analyst_context = data_access_context_for_authorization(
        db_session, analyst_authorization
    )
    viewer_context = data_access_context_for_authorization(
        db_session, viewer_authorization
    )
    assert analyst_context.allows(created.label.id) is True
    assert viewer_context.allows(created.label.id) is False
    assert viewer_context.allows(UNRESTRICTED_HANDLING_LABEL_ID) is True

    inactive_context = data_access_context_for_authorization(
        db_session,
        replace(viewer_authorization, account_eligible=False),
    )
    assert inactive_context.allows(UNRESTRICTED_HANDLING_LABEL_ID) is False

    db_session.execute(
        delete(DataPolicyRoleGrant).where(
            DataPolicyRoleGrant.label_id == created.label.id,
            DataPolicyRoleGrant.role_id == SYSTEM_ROLE_IDS["admin"],
        )
    )
    db_session.flush()
    with pytest.raises(DataPolicyUnavailable, match="invariants are invalid"):
        data_access_context_for_authorization(db_session, analyst_authorization)


def test_temporary_elevation_role_does_not_expand_label_clearance(
    db_session, seed_users, monkeypatch
):
    role = create_role(
        db_session,
        payload=RoleWriteRequest(
            key="temporary-clearance-role",
            name="Temporary clearance role",
            description="Permission role used to test clearance provenance.",
            permissions=["read:feeds", "read:items"],
        ),
        actor_user_id=seed_users["admin"].id,
    )
    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="elevation-restricted",
            name="Elevation restricted",
            role_ids=[role.id],
        ),
        actor_user_id=seed_users["admin"].id,
    )
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = "enforced"
    state.coverage_version = 1
    state.enforced_at = datetime.now(timezone.utc)
    state.enforced_by_user_id = seed_users["admin"].id
    db_session.flush()
    monkeypatch.setattr(
        policy_service,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )
    base = authorization_context_for_user(db_session, seed_users["viewer"])
    elevated = replace(
        base,
        roles=(
            EffectiveRole(
                id=role.id,
                key=role.key,
                name=role.name,
                source="elevation:00000000-0000-4000-8000-000000000999",
            ),
        ),
    )
    durable = replace(
        elevated,
        roles=(replace(elevated.roles[0], source="local"),),
    )

    assert (
        data_access_context_for_authorization(db_session, elevated).allows(
            created.label.id
        )
        is False
    )
    assert (
        data_access_context_for_authorization(db_session, durable).allows(
            created.label.id
        )
        is True
    )


def test_role_deletion_reports_handling_label_reference(db_session, seed_users):
    role = create_role(
        db_session,
        payload=RoleWriteRequest(
            key="restricted-research-reader",
            name="Restricted research reader",
            description="May read explicitly granted handling labels.",
            permissions=["read:feeds", "read:items"],
        ),
        actor_user_id=seed_users["admin"].id,
    )
    create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="restricted-research-role",
            name="Restricted research role",
            role_ids=[role.id],
        ),
        actor_user_id=seed_users["admin"].id,
    )

    with pytest.raises(IAMRoleConflict, match="handling-label access policy"):
        delete_role(db_session, role_id=role.id)


def test_locking_mutation_refreshes_stale_identity_map_state(db_session, seed_users):
    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="stale-lock-test",
            name="Stale lock test",
        ),
        actor_user_id=seed_users["admin"].id,
    )
    db_session.execute(
        update(HandlingLabel)
        .where(HandlingLabel.id == created.label.id)
        .values(name="Concurrent label value", revision=2)
        .execution_options(synchronize_session=False)
    )
    db_session.execute(
        update(DataPolicyState)
        .where(DataPolicyState.id == 1)
        .values(revision=created.policy_revision + 1)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(HandlingLabelRevisionConflict) as raised:
        update_handling_label(
            db_session,
            label_id=created.label.id,
            payload=HandlingLabelUpdateRequest(
                expected_revision=created.label.revision,
                name="Stale overwrite",
            ),
            actor_user_id=seed_users["admin"].id,
        )

    assert raised.value.current_revision == created.policy_revision + 1
    assert raised.value.context == {
        "expected_revision": 1,
        "current_label_revision": 2,
        "current_policy_revision": created.policy_revision + 1,
    }
