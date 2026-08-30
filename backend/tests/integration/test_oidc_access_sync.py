from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.orm import object_session

from app.core.security import generate_api_token
from app.core.config import get_settings
from app.models.api_token import ApiToken
from app.models.auth_session import AuthSession
from app.models.audit_log import AuditLog
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
    IAMPolicyState,
)
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.services.auth_sessions import create_auth_session
from app.services.authorization import authorization_context_for_user
from app.services.oidc_access import (
    MAX_OIDC_CLAIM_VALUES,
    oidc_access_sync_audit_metadata,
    sync_oidc_access,
)
from app.services.oidc_identity import OIDCIdentityError
from app.services.oidc_identity import authenticate_oidc_identity
from app.services.investigation_owner_eligibility import (
    has_durable_investigation_write_access,
)
from app.services.oidc_client import OIDCClaims, OIDCMetadata


def _sync_fixture(db_session, seed_users, *, missing_behavior="remove"):
    admin = seed_users["admin"]
    provider = OIDCProvider(
        system_key="primary",
        name="OIDC access sync",
        enabled=True,
        issuer_url="https://idp.example.test/application/o/threatlens/",
        client_id="oidc-sync",
        client_auth_method="none",
        public_base_url="http://testserver",
        scopes=["openid", "groups"],
        role_claim="groups",
        role_mappings_json=[],
        default_role="viewer",
        jit_provisioning_enabled=False,
        auto_approve_users=False,
        sync_roles_on_login=False,
    )
    mapped_role = IAMRole(
        key=f"oidc-sync-role-{uuid.uuid4().hex[:8]}",
        name="OIDC sync role",
        description="Granted only by exact OIDC mapping.",
        is_system=False,
        revision=1,
        created_by_user_id=admin.id,
    )
    local_role = IAMRole(
        key=f"local-role-{uuid.uuid4().hex[:8]}",
        name="Local role",
        description="Locally managed and never touched by OIDC.",
        is_system=False,
        revision=1,
        created_by_user_id=admin.id,
    )
    group = IAMGroup(
        key=f"oidc-sync-group-{uuid.uuid4().hex[:8]}",
        name="OIDC sync group",
        description="Membership is mapped from OIDC.",
        source="local",
        external_key=None,
        is_system=False,
        revision=1,
        created_by_user_id=admin.id,
    )
    db_session.add_all([provider, mapped_role, local_role, group])
    db_session.flush()
    db_session.add_all(
        [
            IAMRolePermission(role_id=mapped_role.id, permission="write:reports"),
            IAMRolePermission(role_id=local_role.id, permission="read:ai"),
        ]
    )
    policy = OIDCAccessPolicy(
        provider_id=provider.id,
        enabled=True,
        revision=1,
        updated_by_user_id=admin.id,
    )
    db_session.add(policy)
    db_session.flush()
    mapping_set = OIDCClaimMappingSet(
        access_policy_id=policy.id,
        key="security-groups",
        name="Security groups",
        claim_path="realm.groups",
        missing_claim_behavior=missing_behavior,
        enabled=True,
        revision=1,
        updated_by_user_id=admin.id,
    )
    db_session.add(mapping_set)
    db_session.flush()
    role_mapping = OIDCRoleClaimMapping(
        mapping_set_id=mapping_set.id,
        claim_value="soc",
        role_id=mapped_role.id,
        role_is_system=False,
    )
    group_mapping = OIDCGroupClaimMapping(
        mapping_set_id=mapping_set.id,
        claim_value="incident-response",
        group_id=group.id,
        group_is_system=False,
    )
    local_assignment = IAMUserRoleAssignment(
        user_id=seed_users["viewer"].id,
        role_id=local_role.id,
        source="local",
        source_key="",
        assigned_by_user_id=admin.id,
    )
    db_session.add_all([role_mapping, group_mapping, local_assignment])
    db_session.commit()
    return {
        "provider": provider,
        "policy": policy,
        "mapping_set": mapping_set,
        "mapped_role": mapped_role,
        "local_assignment": local_assignment,
        "role_mapping": role_mapping,
        "group_mapping": group_mapping,
        "group": group,
    }


def _mock_oidc_flow(monkeypatch, claims: dict[str, object], exchange_calls: list[str]):
    claims = {"auth_time": int(time.time()), **claims}
    metadata = OIDCMetadata(
        issuer="https://idp.example.test/application/o/threatlens/",
        authorization_endpoint="https://idp.example.test/authorize",
        token_endpoint="https://idp.example.test/token",
        jwks_uri="https://idp.example.test/jwks",
        userinfo_endpoint="https://idp.example.test/userinfo",
        token_endpoint_auth_methods_supported=("none",),
        id_token_signing_alg_values_supported=("RS256",),
    )

    def load_metadata(provider):
        assert object_session(provider) is None
        return metadata

    def exchange_code(_provider, _metadata, *, code, code_verifier):
        exchange_calls.append(code)
        return {"id_token": "id-token", "access_token": "access-token"}

    monkeypatch.setattr("app.api.routes.oidc.load_oidc_metadata", load_metadata)
    monkeypatch.setattr(
        "app.api.routes.oidc.build_oidc_authorization_url",
        lambda _provider, _metadata, *, state, **_kwargs: (
            f"https://idp.example.test/authorize?state={state}"
        ),
    )
    monkeypatch.setattr("app.api.routes.oidc.exchange_oidc_code", exchange_code)
    monkeypatch.setattr(
        "app.api.routes.oidc.validate_oidc_token_claims",
        lambda _provider, _metadata, _token, *, nonce: OIDCClaims(
            issuer=metadata.issuer,
            subject=str(claims["sub"]),
            claims=claims,
        ),
    )


def _start_oidc_login(client):
    start = client.get("/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 302, start.text
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return state


def test_oidc_access_sync_is_exact_idempotent_and_revokes_on_permission_loss(
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    _token, token_prefix, token_hash = generate_api_token()
    api_token = ApiToken(
        user_id=viewer.id,
        name="OIDC access sync token",
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes=["write:reports"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(api_token)
    previous_session = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_token_version=viewer.auth_token_version,
        auth_method="oidc",
        mfa_method=None,
        client_ip="127.0.0.1",
        user_agent="pytest",
    ).session
    db_session.commit()

    granted = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=data["policy"].revision,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert granted.changed is True
    assert granted.added_role_assignments == 1
    assert granted.added_group_memberships == 1
    assert granted.permissions_reduced is False
    metadata = oidc_access_sync_audit_metadata(granted)
    assert metadata["mapping_sets"][0]["claim_value_count"] == 2
    assert metadata["mapping_sets"][0]["claim_value_fingerprint"]
    assert "soc" not in json.dumps(metadata, sort_keys=True)

    repeated = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=data["policy"].revision,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    assert repeated.changed is False

    reduced = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["SOC", "Incident-Response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=data["policy"].revision,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert reduced.removed_role_assignments == 1
    assert reduced.removed_group_memberships == 1
    assert reduced.permissions_reduced is True
    assert reduced.revoked_api_tokens == 1
    assert reduced.revoked_auth_sessions == 1

    db_session.expire_all()
    assert db_session.get(ApiToken, api_token.id).revoked_at is not None
    assert db_session.get(AuthSession, previous_session.id).revoked_at is not None
    assert (
        db_session.get(IAMUserRoleAssignment, data["local_assignment"].id) is not None
    )
    assert (
        db_session.scalar(
            select(IAMUserRoleAssignment).where(
                IAMUserRoleAssignment.source == "oidc",
                IAMUserRoleAssignment.source_key == data["role_mapping"].source_key,
            )
        )
        is None
    )
    assert (
        db_session.scalar(
            select(IAMGroupMembership).where(
                IAMGroupMembership.source == "oidc",
                IAMGroupMembership.source_key == data["group_mapping"].source_key,
            )
        )
        is None
    )


def test_oidc_access_sync_missing_claim_behaviors_and_invalid_claims(
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users, missing_behavior="preserve")
    viewer = seed_users["viewer"]
    sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()

    preserved = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    assert preserved.changed is False
    assert preserved.diagnostics[0].preserved is True

    data["mapping_set"].missing_claim_behavior = "remove"
    data["mapping_set"].revision += 1
    data["policy"].revision += 1
    db_session.add_all([data["mapping_set"], data["policy"]])
    db_session.commit()
    removed = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=2,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert removed.removed_role_assignments == 1

    data["mapping_set"].missing_claim_behavior = "deny"
    data["mapping_set"].revision += 1
    data["policy"].revision += 1
    db_session.add_all([data["mapping_set"], data["policy"]])
    db_session.commit()
    with pytest.raises(OIDCIdentityError) as missing:
        sync_oidc_access(
            db_session,
            provider_id=data["provider"].id,
            user=viewer,
            claims={"realm": {}},
            expected_policy_id=data["policy"].id,
            expected_policy_revision=3,
            expected_policy_generation=data["provider"].oidc_access_policy_generation,
        )
    assert missing.value.code == "access_claim_required"

    for invalid_value in (
        {"unexpected": "object"},
        ["group"] * (MAX_OIDC_CLAIM_VALUES + 1),
    ):
        with pytest.raises(OIDCIdentityError) as invalid:
            sync_oidc_access(
                db_session,
                provider_id=data["provider"].id,
                user=viewer,
                claims={"realm": {"groups": invalid_value}},
                expected_policy_id=data["policy"].id,
                expected_policy_revision=3,
                expected_policy_generation=(
                    data["provider"].oidc_access_policy_generation
                ),
            )
        assert invalid.value.code == "access_claim_invalid"


def test_oidc_access_sync_rejects_policy_revision_race(
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users)
    with pytest.raises(OIDCIdentityError) as changed:
        sync_oidc_access(
            db_session,
            provider_id=data["provider"].id,
            user=seed_users["viewer"],
            claims={"realm": {"groups": ["soc"]}},
            expected_policy_id=data["policy"].id,
            expected_policy_revision=99,
            expected_policy_generation=data["provider"].oidc_access_policy_generation,
        )
    assert changed.value.code == "provider_configuration_changed"
    assert changed.value.details == {"configuration_component": "access_policy"}


def test_oidc_access_sync_uses_bounded_leases_and_ignores_expired_grants(
    db_session,
    seed_users,
    monkeypatch,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    granted = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert granted.renewed_role_assignments == 0
    assert granted.renewed_group_memberships == 0

    role_assignment = db_session.scalar(
        select(IAMUserRoleAssignment).where(
            IAMUserRoleAssignment.user_id == viewer.id,
            IAMUserRoleAssignment.source == "oidc",
        )
    )
    group_membership = db_session.scalar(
        select(IAMGroupMembership).where(
            IAMGroupMembership.user_id == viewer.id,
            IAMGroupMembership.source == "oidc",
        )
    )
    assert role_assignment is not None
    assert group_membership is not None
    long_expiry = datetime.now(timezone.utc) + timedelta(days=30)
    role_assignment.oidc_assertion_expires_at = long_expiry
    group_membership.oidc_assertion_expires_at = long_expiry
    db_session.add_all([role_assignment, group_membership])
    db_session.commit()
    monkeypatch.setattr(
        get_settings(),
        "oidc_access_grant_ttl_seconds",
        60,
    )

    shortened = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert shortened.changed is False
    assert shortened.renewed_role_assignments == 1
    assert shortened.renewed_group_memberships == 1
    assert role_assignment.oidc_assertion_expires_at < long_expiry

    role_assignment.oidc_assertion_expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    group_membership.oidc_assertion_expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db_session.add_all([role_assignment, group_membership])
    db_session.commit()
    context = authorization_context_for_user(db_session, viewer)
    assert "write:reports" not in context.permissions
    assert data["group"].key not in context.groups

    revision_before = db_session.get(IAMPolicyState, 1).revision
    reactivated = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert reactivated.changed is True
    assert reactivated.reactivated_role_assignments == 1
    assert reactivated.reactivated_group_memberships == 1
    assert db_session.get(IAMPolicyState, 1).revision == revision_before + 1
    metadata = oidc_access_sync_audit_metadata(reactivated)
    assert metadata["provider_id"] == str(data["provider"].id)
    assert metadata["policy_id"] == str(data["policy"].id)
    assert metadata["policy_generation"] == 0


def test_oidc_lease_cannot_be_the_basis_for_investigation_ownership(
    client,
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    db_session.add(
        IAMRolePermission(
            role_id=data["mapped_role"].id,
            permission="write:investigations",
        )
    )
    db_session.commit()

    sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()

    assert authorization_context_for_user(db_session, viewer).has(
        "write:investigations"
    )
    assert has_durable_investigation_write_access(db_session, viewer) is False
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=viewer.id,
            name="Lease-only investigation token",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=["write:investigations"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()

    creation = client.post(
        "/investigations",
        headers={"Authorization": f"Bearer {token_value}"},
        json={"title": "Lease-only owner must be rejected", "visibility": "private"},
    )
    assert creation.status_code == 422, creation.text
    assert "durable" in creation.json()["detail"]


def test_unused_malformed_fixed_role_claim_does_not_block_existing_login(
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    db_session.add(
        ExternalIdentity(
            provider_id=data["provider"].id,
            user_id=viewer.id,
            issuer=data["provider"].issuer_url,
            subject="malformed-unused-role-claim",
            email_at_link=viewer.email,
        )
    )
    db_session.commit()

    result = authenticate_oidc_identity(
        db_session,
        data["provider"],
        OIDCClaims(
            issuer=data["provider"].issuer_url,
            subject="malformed-unused-role-claim",
            claims={"sub": "malformed-unused-role-claim", "groups": ["soc", 7]},
        ),
    )
    assert result.user.id == viewer.id


def test_oidc_access_sync_removes_but_never_adds_for_ineligible_account(
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    viewer.is_approved = False
    db_session.add(viewer)
    db_session.commit()

    reconciled = sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc", "incident-response"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()
    assert reconciled.added_role_assignments == 0
    assert reconciled.added_group_memberships == 0
    assert reconciled.removed_role_assignments == 1
    assert reconciled.removed_group_memberships == 1


def test_oidc_callback_rejects_delete_recreate_aba_before_exchange(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    data = _sync_fixture(db_session, seed_users)
    exchange_calls: list[str] = []
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "aba-subject", "realm": {"groups": ["soc"]}},
        exchange_calls,
    )
    state = _start_oidc_login(client)
    original_policy_id = data["policy"].id
    db_session.delete(data["policy"])
    db_session.flush()
    replacement = OIDCAccessPolicy(
        provider_id=data["provider"].id,
        enabled=True,
        revision=1,
        updated_by_user_id=seed_users["admin"].id,
    )
    data["provider"].oidc_access_policy_generation += 1
    db_session.add_all([replacement, data["provider"]])
    db_session.commit()
    assert replacement.id != original_policy_id

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "provider_configuration_changed"
    ]
    assert exchange_calls == []


def test_oidc_callback_generation_fences_no_policy_create_delete_aba(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    data = _sync_fixture(db_session, seed_users)
    db_session.delete(data["policy"])
    data["provider"].oidc_access_policy_generation += 1
    db_session.add(data["provider"])
    db_session.commit()
    exchange_calls: list[str] = []
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "generation-aba-subject", "realm": {"groups": []}},
        exchange_calls,
    )
    state = _start_oidc_login(client)

    transient = OIDCAccessPolicy(
        provider_id=data["provider"].id,
        enabled=True,
        revision=1,
        updated_by_user_id=seed_users["admin"].id,
    )
    db_session.add(transient)
    db_session.flush()
    db_session.delete(transient)
    data["provider"].oidc_access_policy_generation += 2
    db_session.add(data["provider"])
    db_session.commit()

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "provider_configuration_changed"
    ]
    assert exchange_calls == []


def test_oidc_callback_redirect_survives_failure_audit_outage(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    _sync_fixture(db_session, seed_users)
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "audit-outage", "realm": {"groups": []}},
        [],
    )
    state = _start_oidc_login(client)

    def audit_unavailable(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.api.routes.oidc.record_audit", audit_unavailable)
    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "missing_code"
    ]


def test_oidc_unlink_purges_custom_access_and_issues_fresh_local_session(
    client,
    db_session,
    seed_users,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    db_session.add(
        ExternalIdentity(
            provider_id=data["provider"].id,
            user_id=viewer.id,
            issuer=data["provider"].issuer_url,
            subject="unlink-custom-access",
            email_at_link=viewer.email,
        )
    )
    _token, token_prefix, token_hash = generate_api_token()
    api_token = ApiToken(
        user_id=viewer.id,
        name="Unlink custom access token",
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes=["write:reports"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(api_token)
    sync_oidc_access(
        db_session,
        provider_id=data["provider"].id,
        user=viewer,
        claims={"realm": {"groups": ["soc"]}},
        expected_policy_id=data["policy"].id,
        expected_policy_revision=1,
        expected_policy_generation=data["provider"].oidc_access_policy_generation,
    )
    db_session.commit()

    login = client.post(
        "/auth/login",
        json={"email": viewer.email, "password": "ViewerPass123!"},
    )
    assert login.status_code == 200, login.text
    previous_session_token = client.cookies.get("threatlens_session")
    csrf = client.cookies.get("threatlens_csrf")
    assert previous_session_token and csrf
    unlinked = client.request(
        "DELETE",
        "/auth/oidc/account",
        json={"current_password": "ViewerPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert unlinked.status_code == 204, unlinked.text
    replacement_session_token = client.cookies.get("threatlens_session")
    assert replacement_session_token
    assert replacement_session_token != previous_session_token
    assert client.get("/auth/me").status_code == 200

    db_session.expire_all()
    assert db_session.get(ApiToken, api_token.id).revoked_at is not None
    assert (
        db_session.scalar(
            select(IAMUserRoleAssignment).where(
                IAMUserRoleAssignment.user_id == viewer.id,
                IAMUserRoleAssignment.source == "oidc",
            )
        )
        is None
    )
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider_id == data["provider"].id,
                ExternalIdentity.user_id == viewer.id,
            )
        )
        is None
    )
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.identity.unlink")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["purged_role_assignments"] == 1
    assert audit.metadata_json["access_reduced"] is True
    assert audit.metadata_json["revoked_api_tokens"] >= 1


def test_oidc_callback_materializes_custom_access_and_audits_without_claims(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    db_session.add(
        ExternalIdentity(
            provider_id=data["provider"].id,
            user_id=viewer.id,
            issuer=data["provider"].issuer_url,
            subject="mapped-viewer",
            email_at_link=viewer.email,
        )
    )
    db_session.commit()
    exchange_calls: list[str] = []
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "mapped-viewer",
            "realm": {"groups": ["soc", "incident-response"]},
        },
        exchange_calls,
    )

    state = _start_oidc_login(client)
    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "http://testserver/"
    assert exchange_calls == ["authorization-code"]
    assert client.get("/auth/me").status_code == 200
    assert (
        db_session.scalar(
            select(IAMUserRoleAssignment).where(
                IAMUserRoleAssignment.user_id == viewer.id,
                IAMUserRoleAssignment.source == "oidc",
                IAMUserRoleAssignment.source_key == data["role_mapping"].source_key,
            )
        )
        is not None
    )
    assert (
        db_session.scalar(
            select(IAMGroupMembership).where(
                IAMGroupMembership.user_id == viewer.id,
                IAMGroupMembership.source == "oidc",
                IAMGroupMembership.source_key == data["group_mapping"].source_key,
            )
        )
        is not None
    )
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.access.sync")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.actor_user_id == viewer.id
    assert audit.metadata_json["policy_revision"] == 1
    assert "soc" not in json.dumps(audit.metadata_json, sort_keys=True)
    assert "incident-response" not in json.dumps(audit.metadata_json, sort_keys=True)


def test_oidc_callback_rejects_access_policy_revision_race_before_exchange(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    data = _sync_fixture(db_session, seed_users)
    viewer = seed_users["viewer"]
    db_session.add(
        ExternalIdentity(
            provider_id=data["provider"].id,
            user_id=viewer.id,
            issuer=data["provider"].issuer_url,
            subject="revision-race-viewer",
            email_at_link=viewer.email,
        )
    )
    db_session.commit()
    exchange_calls: list[str] = []
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "revision-race-viewer", "realm": {"groups": ["soc"]}},
        exchange_calls,
    )
    state = _start_oidc_login(client)
    data["policy"].revision += 1
    db_session.add(data["policy"])
    db_session.commit()

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "provider_configuration_changed"
    ]
    assert exchange_calls == []
    assert client.get("/auth/me").status_code == 401
