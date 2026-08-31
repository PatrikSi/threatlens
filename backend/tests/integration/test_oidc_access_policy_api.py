from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMPolicyState,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.oidc import OIDCProvider
from app.models.investigation import Investigation, InvestigationMember


def _configured_provider(db_session) -> OIDCProvider:
    provider = OIDCProvider(
        system_key="primary",
        name="Access mapping SSO",
        enabled=True,
        issuer_url="https://idp.example.test/application/o/threatlens/",
        client_id="threatlens-access-test",
        client_auth_method="none",
        public_base_url="http://testserver",
        scopes=["openid", "profile", "email", "groups"],
        role_claim="groups",
        role_mappings_json=[],
        default_role="viewer",
        jit_provisioning_enabled=False,
        auto_approve_users=False,
        sync_roles_on_login=False,
    )
    db_session.add(provider)
    db_session.commit()
    return provider


def _custom_targets(db_session, admin_id):
    suffix = uuid.uuid4().hex[:8]
    role = IAMRole(
        key=f"oidc-access-role-{suffix}",
        name="OIDC access role",
        description="Mapped through an exact OIDC claim.",
        is_system=False,
        revision=1,
        created_by_user_id=admin_id,
    )
    group = IAMGroup(
        key=f"oidc-access-group-{suffix}",
        name="OIDC access group",
        description="Mapped through an exact OIDC claim.",
        source="local",
        external_key=None,
        is_system=False,
        revision=1,
        created_by_user_id=admin_id,
    )
    db_session.add_all([role, group])
    db_session.flush()
    db_session.add(IAMRolePermission(role_id=role.id, permission="write:reports"))
    db_session.commit()
    return role, group


def _mapping_payload(role_id, group_id, **overrides):
    payload = {
        "key": "security-teams",
        "name": "Security teams",
        "claim_path": "realm_access.groups",
        "missing_claim_behavior": "deny",
        "enabled": True,
        "role_mappings": [{"claim_value": "soc", "role_id": str(role_id)}],
        "group_mappings": [
            {"claim_value": "incident-response", "group_id": str(group_id)}
        ],
    }
    payload.update(overrides)
    return payload


def test_oidc_access_policy_management_is_revisioned_audited_and_source_safe(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    empty = client.get("/auth/oidc/access-policy", headers=auth_headers["admin"])
    assert empty.status_code == 200
    assert empty.json() == {"configured": False, "provider_id": None, "policy": None}

    provider = _configured_provider(db_session)
    role, group = _custom_targets(db_session, seed_users["admin"].id)
    created = client.post(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        json={"enabled": True},
    )
    assert created.status_code == 201, created.text
    assert created.json()["policy"]["revision"] == 1
    assert created.json()["policy"]["generation"] == 1
    assert created.json()["policy"]["mapping_sets"] == []

    mapping = client.post(
        "/auth/oidc/access-policy/mapping-sets",
        headers=auth_headers["admin"],
        json=_mapping_payload(role.id, group.id),
    )
    assert mapping.status_code == 201, mapping.text
    policy = mapping.json()["policy"]
    assert policy["provider_id"] == str(provider.id)
    assert policy["revision"] == 2
    assert policy["generation"] == 2
    mapping_set = policy["mapping_sets"][0]
    assert mapping_set["revision"] == 1
    assert mapping_set["role_mappings"][0]["source_key"].startswith("oidc:role:")
    assert mapping_set["group_mappings"][0]["source_key"].startswith("oidc:group:")

    role_delete = client.delete(
        f"/iam/roles/{role.id}?expected_revision={role.revision}",
        headers=auth_headers["admin"],
    )
    assert role_delete.status_code == 409
    assert "OIDC claim mapping" in role_delete.json()["detail"]
    group_delete = client.delete(
        f"/iam/groups/{group.id}?expected_revision={group.revision}",
        headers=auth_headers["admin"],
    )
    assert group_delete.status_code == 409
    assert "OIDC claim mapping" in group_delete.json()["detail"]

    system_role = db_session.scalar(
        select(IAMRole).where(IAMRole.is_system.is_(True)).order_by(IAMRole.key)
    )
    assert system_role is not None
    invalid = client.post(
        "/auth/oidc/access-policy/mapping-sets",
        headers=auth_headers["admin"],
        json=_mapping_payload(
            system_role.id,
            group.id,
            key="invalid-system-target",
            name="Invalid system target",
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "oidc_mapping_target_invalid"

    viewer = seed_users["viewer"]
    role_source_key = mapping_set["role_mappings"][0]["source_key"]
    group_source_key = mapping_set["group_mappings"][0]["source_key"]
    local_role_assignment = IAMUserRoleAssignment(
        user_id=viewer.id,
        role_id=role.id,
        source="local",
        source_key="",
        assigned_by_user_id=seed_users["admin"].id,
    )
    local_group_membership = IAMGroupMembership(
        user_id=viewer.id,
        group_id=group.id,
        source="local",
        source_key="",
        assigned_by_user_id=seed_users["admin"].id,
    )
    db_session.add_all(
        [
            local_role_assignment,
            local_group_membership,
            IAMUserRoleAssignment(
                user_id=viewer.id,
                role_id=role.id,
                source="oidc",
                source_key=role_source_key,
                oidc_role_mapping_id=uuid.UUID(mapping_set["role_mappings"][0]["id"]),
                oidc_assertion_expires_at=datetime.now(timezone.utc)
                + timedelta(hours=1),
            ),
            IAMGroupMembership(
                user_id=viewer.id,
                group_id=group.id,
                source="oidc",
                source_key=group_source_key,
                oidc_group_mapping_id=uuid.UUID(mapping_set["group_mappings"][0]["id"]),
                oidc_assertion_expires_at=datetime.now(timezone.utc)
                + timedelta(hours=1),
            ),
        ]
    )
    db_session.commit()
    iam_revision_before = db_session.get(IAMPolicyState, 1).revision

    changed = client.put(
        f"/auth/oidc/access-policy/mapping-sets/{mapping_set['id']}",
        headers=auth_headers["admin"],
        json={
            "expected_revision": mapping_set["revision"],
            "claim_path": "groups",
            "role_mappings": [
                {
                    "claim_value": mapping_set["role_mappings"][0]["claim_value"],
                    "role_id": mapping_set["role_mappings"][0]["role_id"],
                }
            ],
            "group_mappings": [
                {
                    "claim_value": mapping_set["group_mappings"][0]["claim_value"],
                    "group_id": mapping_set["group_mappings"][0]["group_id"],
                }
            ],
        },
    )
    assert changed.status_code == 200, changed.text
    changed_set = changed.json()["policy"]["mapping_sets"][0]
    assert changed_set["revision"] == 2
    assert changed.json()["policy"]["revision"] == 3
    assert changed.json()["policy"]["generation"] == 3
    assert changed_set["role_mappings"][0]["source_key"] == role_source_key
    assert changed_set["group_mappings"][0]["source_key"] == group_source_key

    db_session.expire_all()
    assert db_session.get(IAMPolicyState, 1).revision == iam_revision_before + 1
    assert db_session.get(IAMUserRoleAssignment, local_role_assignment.id) is not None
    assert db_session.get(IAMGroupMembership, local_group_membership.id) is not None
    assert (
        db_session.scalar(
            select(IAMUserRoleAssignment).where(
                IAMUserRoleAssignment.source == "oidc",
                IAMUserRoleAssignment.source_key == role_source_key,
            )
        )
        is None
    )
    assert (
        db_session.scalar(
            select(IAMGroupMembership).where(
                IAMGroupMembership.source == "oidc",
                IAMGroupMembership.source_key == group_source_key,
            )
        )
        is None
    )

    stale = client.put(
        f"/auth/oidc/access-policy/mapping-sets/{mapping_set['id']}",
        headers=auth_headers["admin"],
        json={"expected_revision": 1, "name": "Stale update"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "oidc_mapping_set_revision_conflict"
    assert stale.headers["x-current-version"] == "2"
    current = client.get(
        "/auth/oidc/access-policy", headers=auth_headers["admin"]
    ).json()["policy"]
    assert current["generation"] == 3

    audit_rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action.like("oidc.%"))
    ).all()
    successful_update = next(
        row
        for row in audit_rows
        if row.action == "oidc.mapping_set.update" and row.success
    )
    assert successful_update.metadata_json["purged_role_assignments"] == 1
    assert successful_update.metadata_json["purged_group_memberships"] == 1
    assert "soc" not in json.dumps(
        [row.metadata_json for row in audit_rows], sort_keys=True
    )
    assert any(
        row.action == "oidc.mapping_set.update"
        and not row.success
        and row.metadata_json["reason"] == "oidc_mapping_set_revision_conflict"
        for row in audit_rows
    )

    deleted_mapping_set = client.delete(
        f"/auth/oidc/access-policy/mapping-sets/{mapping_set['id']}",
        headers=auth_headers["admin"],
        params={"expected_revision": changed_set["revision"]},
    )
    assert deleted_mapping_set.status_code == 200, deleted_mapping_set.text
    assert deleted_mapping_set.json()["policy"]["generation"] == 4
    assert deleted_mapping_set.json()["policy"]["mapping_sets"] == []
    deletion_audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.mapping_set.delete")
        .order_by(AuditLog.created_at.desc())
    )
    assert deletion_audit is not None
    assert deletion_audit.resource_id == mapping_set["id"]
    assert deletion_audit.metadata_json["policy_generation"] == 4


def test_disabling_oidc_access_policy_purges_materialized_access(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    _configured_provider(db_session)
    role, group = _custom_targets(db_session, seed_users["admin"].id)
    policy = client.post(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        json={"enabled": True},
    ).json()["policy"]
    policy = client.post(
        "/auth/oidc/access-policy/mapping-sets",
        headers=auth_headers["admin"],
        json=_mapping_payload(role.id, group.id),
    ).json()["policy"]
    mapping_set = policy["mapping_sets"][0]
    db_session.add_all(
        [
            IAMUserRoleAssignment(
                user_id=seed_users["viewer"].id,
                role_id=role.id,
                source="oidc",
                source_key=mapping_set["role_mappings"][0]["source_key"],
                oidc_role_mapping_id=uuid.UUID(mapping_set["role_mappings"][0]["id"]),
                oidc_assertion_expires_at=datetime.now(timezone.utc)
                + timedelta(hours=1),
            ),
            IAMGroupMembership(
                user_id=seed_users["viewer"].id,
                group_id=group.id,
                source="oidc",
                source_key=mapping_set["group_mappings"][0]["source_key"],
                oidc_group_mapping_id=uuid.UUID(mapping_set["group_mappings"][0]["id"]),
                oidc_assertion_expires_at=datetime.now(timezone.utc)
                + timedelta(hours=1),
            ),
        ]
    )
    db_session.commit()

    disabled = client.put(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        json={"expected_revision": policy["revision"], "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["policy"]["enabled"] is False
    assert disabled.json()["policy"]["generation"] == 3
    assert (
        db_session.scalar(
            select(IAMUserRoleAssignment).where(IAMUserRoleAssignment.source == "oidc")
        )
        is None
    )
    assert (
        db_session.scalar(
            select(IAMGroupMembership).where(IAMGroupMembership.source == "oidc")
        )
        is None
    )

    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.access_policy.update")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["purged_role_assignments"] == 1
    assert audit.metadata_json["purged_group_memberships"] == 1
    assert audit.metadata_json["access_reduced_user_count"] == 1
    assert audit.metadata_json["iam_policy_revision"] is not None


def test_deleting_oidc_access_policy_audits_exact_policy_incarnation(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    _configured_provider(db_session)
    policy = client.post(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        json={"enabled": False},
    ).json()["policy"]

    deleted = client.delete(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        params={"expected_revision": policy["revision"]},
    )

    assert deleted.status_code == 200, deleted.text
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.access_policy.delete")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.resource_id == policy["id"]
    assert audit.metadata_json["affected_policy_id"] == policy["id"]
    assert audit.metadata_json["previous_policy_revision"] == policy["revision"]
    assert audit.metadata_json["policy_generation"] == 2


def test_disabling_oidc_access_policy_rolls_back_when_it_would_orphan_investigation(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    _configured_provider(db_session)
    role, group = _custom_targets(db_session, seed_users["admin"].id)
    db_session.add(
        IAMRolePermission(role_id=role.id, permission="write:investigations")
    )
    db_session.commit()
    policy = client.post(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        json={"enabled": True},
    ).json()["policy"]
    policy = client.post(
        "/auth/oidc/access-policy/mapping-sets",
        headers=auth_headers["admin"],
        json=_mapping_payload(role.id, group.id),
    ).json()["policy"]
    mapping = policy["mapping_sets"][0]["role_mappings"][0]
    viewer = seed_users["viewer"]
    assignment = IAMUserRoleAssignment(
        user_id=viewer.id,
        role_id=role.id,
        source="oidc",
        source_key=mapping["source_key"],
        oidc_role_mapping_id=uuid.UUID(mapping["id"]),
        oidc_assertion_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    investigation = Investigation(
        title="Sole OIDC owner",
        description="Ownership must survive failed access-policy mutation.",
        severity="high",
        visibility="private",
        assignee_user_id=viewer.id,
        created_by_user_id=viewer.id,
    )
    db_session.add_all([assignment, investigation])
    db_session.flush()
    db_session.add(
        InvestigationMember(
            investigation_id=investigation.id,
            user_id=viewer.id,
            role="owner",
            added_by_user_id=viewer.id,
        )
    )
    db_session.commit()

    blocked = client.put(
        "/auth/oidc/access-policy",
        headers=auth_headers["admin"],
        json={"expected_revision": policy["revision"], "enabled": False},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == (
        "oidc_access_investigation_owner_reassignment_required"
    )
    db_session.expire_all()
    assert db_session.get(IAMUserRoleAssignment, assignment.id) is not None
    current = client.get(
        "/auth/oidc/access-policy", headers=auth_headers["admin"]
    ).json()["policy"]
    assert current["enabled"] is True
    assert current["revision"] == policy["revision"]
    rejection = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "oidc.access_policy.update",
            AuditLog.success.is_(False),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert rejection is not None
    assert rejection.metadata_json["reason"] == (
        "oidc_access_investigation_owner_reassignment_required"
    )
