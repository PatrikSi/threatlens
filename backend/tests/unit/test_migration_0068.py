from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.access_review import (
    ACCESS_REVIEW_APPLY_OUTCOMES,
    ACCESS_REVIEW_ITEM_TYPES,
    ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES,
    AccessReviewCampaign,
    AccessReviewItem,
    access_review_item_from_snapshot,
    build_access_review_assignment_snapshot,
)
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMUserRoleAssignment,
)
from app.models.oidc import OIDCProvider
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.models.service_account import ServiceAccount, ServiceAccountRoleAssignment
from app.models.temporary_elevation import (
    TemporaryElevation,
    TemporaryElevationPermission,
)
from app.models.user import User
from app.schemas.access_review import (
    AccessReviewApplyOutcome,
    AccessReviewAssignmentSource,
    AccessReviewBeginApplyRequest,
    AccessReviewCampaignCreate,
    AccessReviewDecisionBatchRequest,
    AccessReviewDecisionInput,
    AccessReviewItemType,
    AccessReviewTransitionRequest,
)
from app.services.access_review_apply import (
    AccessReviewMutationResult,
    apply_access_review_item,
)
from app.services.access_reviews import (
    begin_access_review_apply,
    close_access_review_campaign,
    complete_access_review_apply,
    create_access_review_campaign,
    record_access_review_decisions,
)


_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


def test_assignment_fingerprint_recursively_ignores_audit_metadata():
    assignment_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    target_id = uuid.uuid4()
    created_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    def snapshot(
        source_key: str,
        actor_id: uuid.UUID | None,
        nested_actor_id: uuid.UUID | None,
        role_revision: int,
    ):
        return build_access_review_assignment_snapshot(
            ("direct_user_role", assignment_id, "oidc", None),
            ("user", principal_id, "principal@example.test"),
            ("role", target_id, "auditor", "Auditor", 3),
            ("read:audit",),
            {
                "schema_version": 1,
                "source_key": source_key,
                "audit": {"assigned_by_user_id": str(actor_id) if actor_id else None},
                "group_roles": [
                    {
                        "role_id": str(target_id),
                        "role_revision": role_revision,
                        "permissions": ["read:audit"],
                        "audit": {
                            "assigned_by_user_id": (
                                str(nested_actor_id) if nested_actor_id else None
                            )
                        },
                    }
                ],
            },
            created_at,
            None,
        )

    original = snapshot("oidc:role:source", uuid.uuid4(), uuid.uuid4(), 3)
    cleared_actors = snapshot("oidc:role:source", None, None, 3)
    changed_source = snapshot("oidc:role:changed", None, None, 3)
    changed_access = snapshot("oidc:role:source", None, None, 4)

    assert original.fingerprint == cleared_actors.fingerprint
    assert original.fingerprint != changed_source.fingerprint
    assert original.fingerprint != changed_access.fingerprint


def test_assignment_fingerprints_ignore_sibling_owner_revisions():
    principal_id = uuid.uuid4()
    target_id = uuid.uuid4()
    created_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    def group_snapshot(group_revision: int, role_revision: int):
        return build_access_review_assignment_snapshot(
            ("group_membership", uuid.UUID(int=1), "local", None),
            ("user", principal_id, "principal@example.test"),
            ("group", target_id, "soc", "SOC", group_revision),
            ("read:audit",),
            {
                "schema_version": 1,
                "group_revision": group_revision,
                "group_roles": [
                    {
                        "assignment_id": str(uuid.UUID(int=2)),
                        "role_id": str(uuid.UUID(int=3)),
                        "role_revision": role_revision,
                        "permissions": ["read:audit"],
                    }
                ],
            },
            created_at,
            None,
        )

    original_group = group_snapshot(1, 4)
    sibling_group_change = group_snapshot(2, 4)
    access_group_change = group_snapshot(2, 5)
    group_item = access_review_item_from_snapshot(
        uuid.uuid4(), 1, original_group, created_at
    )
    assert original_group.fingerprint == sibling_group_change.fingerprint
    assert sibling_group_change.matches_item(group_item)
    assert original_group.fingerprint != access_group_change.fingerprint
    assert not access_group_change.matches_item(group_item)

    def service_account_snapshot(account_revision: int, active: bool):
        return build_access_review_assignment_snapshot(
            ("service_account_role", uuid.UUID(int=4), "local", None),
            ("service_account", principal_id, "collector"),
            ("role", target_id, "analyst", "Analyst", 2),
            ("read:feeds",),
            {
                "schema_version": 1,
                "service_account_key": "collector",
                "service_account_revision": account_revision,
                "service_account_active": active,
            },
            created_at,
            None,
        )

    original_account = service_account_snapshot(1, True)
    sibling_account_change = service_account_snapshot(2, True)
    access_account_change = service_account_snapshot(2, False)
    assert original_account.fingerprint == sibling_account_change.fingerprint
    assert original_account.fingerprint != access_account_change.fingerprint

    def oidc_mapping_snapshot(
        *,
        mapping_set_revision: int,
        provider_generation: int,
        group_revision: int,
        claim_path: str = "groups",
        missing_claim_behavior: str = "preserve",
        provider_enabled: bool = True,
    ):
        return build_access_review_assignment_snapshot(
            ("oidc_group_mapping", uuid.UUID(int=5), "oidc", None),
            ("oidc_provider", principal_id, "Identity provider"),
            ("group", target_id, "soc", "SOC", group_revision),
            ("read:audit",),
            {
                "schema_version": 1,
                "mapping_set_id": str(uuid.UUID(int=6)),
                "mapping_set_key": "groups",
                "mapping_set_name": "Groups",
                "mapping_set_revision": mapping_set_revision,
                "mapping_set_enabled": True,
                "claim_path": claim_path,
                "missing_claim_behavior": missing_claim_behavior,
                "claim_value": "soc",
                "source_key": "oidc:group:" + "a" * 32,
                "access_policy_id": str(uuid.UUID(int=7)),
                "access_policy_revision": mapping_set_revision,
                "access_policy_enabled": True,
                "provider_enabled": provider_enabled,
                "provider_config_revision": mapping_set_revision,
                "provider_access_policy_generation": provider_generation,
                "group_roles": [
                    {
                        "role_id": str(uuid.UUID(int=8)),
                        "role_revision": 1,
                        "permissions": ["read:audit"],
                    }
                ],
            },
            created_at,
            None,
        )

    original_mapping = oidc_mapping_snapshot(
        mapping_set_revision=1,
        provider_generation=1,
        group_revision=1,
    )
    aggregate_mapping_change = oidc_mapping_snapshot(
        mapping_set_revision=2,
        provider_generation=3,
        group_revision=4,
    )
    semantic_mapping_change = oidc_mapping_snapshot(
        mapping_set_revision=2,
        provider_generation=3,
        group_revision=4,
        claim_path="realm.groups",
    )
    missing_claim_change = oidc_mapping_snapshot(
        mapping_set_revision=2,
        provider_generation=3,
        group_revision=4,
        missing_claim_behavior="deny",
    )
    provider_disabled = oidc_mapping_snapshot(
        mapping_set_revision=2,
        provider_generation=3,
        group_revision=4,
        provider_enabled=False,
    )
    mapping_item = access_review_item_from_snapshot(
        uuid.uuid4(), 1, original_mapping, created_at
    )
    assert original_mapping.fingerprint == aggregate_mapping_change.fingerprint
    assert aggregate_mapping_change.matches_item(mapping_item)
    assert original_mapping.fingerprint != semantic_mapping_change.fingerprint
    assert original_mapping.fingerprint != missing_claim_change.fingerprint
    assert original_mapping.fingerprint != provider_disabled.fingerprint

    oidc_expiry_1 = build_access_review_assignment_snapshot(
        ("direct_user_role", uuid.UUID(int=9), "oidc", None),
        ("user", principal_id, "principal@example.test"),
        ("role", target_id, "auditor", "Auditor", 1),
        ("read:audit",),
        {"schema_version": 1, "source_key": "oidc:role:" + "b" * 32},
        created_at,
        created_at + timedelta(hours=1),
    )
    oidc_expiry_2 = build_access_review_assignment_snapshot(
        ("direct_user_role", uuid.UUID(int=9), "oidc", None),
        ("user", principal_id, "principal@example.test"),
        ("role", target_id, "auditor", "Auditor", 1),
        ("read:audit",),
        {"schema_version": 1, "source_key": "oidc:role:" + "b" * 32},
        created_at,
        created_at + timedelta(hours=8),
    )
    assert oidc_expiry_1.fingerprint == oidc_expiry_2.fingerprint


def test_access_review_types_and_terminal_outcomes_share_one_contract():
    original_types = {
        "direct_user_role",
        "group_membership",
        "service_account_role",
        "oidc_role_mapping",
        "oidc_group_mapping",
        "live_elevation",
    }
    assert ACCESS_REVIEW_ITEM_TYPES == original_types | {"legacy_user_role"}
    assert set(get_args(AccessReviewItemType)) == ACCESS_REVIEW_ITEM_TYPES
    assert set(get_args(AccessReviewAssignmentSource)) == {
        "local",
        "legacy",
        "oidc",
        "temporary",
    }
    assert set(get_args(AccessReviewApplyOutcome)) == ACCESS_REVIEW_APPLY_OUTCOMES
    assert ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES == {
        "retained",
        "revoked",
        "already_absent",
        "superseded",
    }
    assert {
        "manual_action_required",
        "drifted",
        "failed",
    }.isdisjoint(ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES)


def test_access_review_service_captures_edges_and_only_mutates_during_apply(
    test_database_url,
    monkeypatch,
):
    schema_name = f"service_0068_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    creator_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()
    target_id = uuid.uuid4()
    role_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    group_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    group_role_id = uuid.uuid4()
    service_account_id = uuid.uuid4()
    service_account_role_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    mapping_set_id = uuid.uuid4()
    role_mapping_id = uuid.uuid4()
    group_mapping_id = uuid.uuid4()
    elevation_id = uuid.uuid4()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".alembic_version '
                "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
            )
        )
    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_url.replace("%", "%%"))
            get_settings.cache_clear()
            command.upgrade(_alembic_config(), "0068_access_reviews")

            with Session(schema_engine) as db:
                for user_id, email in (
                    (creator_id, "creator@example.test"),
                    (reviewer_id, "reviewer@example.test"),
                    (target_id, "target@example.test"),
                ):
                    db.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, password_hash, role, is_approved) "
                            "VALUES (:id, :email, 'hash', 'admin', true)"
                        ),
                        {"id": user_id, "email": email},
                    )
                db.execute(
                    text(
                        "INSERT INTO iam_roles "
                        "(id, key, name, description, is_system, revision) VALUES "
                        "(:id, 'review-auditor', 'Review auditor', '', false, 1)"
                    ),
                    {"id": role_id},
                )
                db.execute(
                    text(
                        "INSERT INTO iam_role_permissions (role_id, permission) "
                        "VALUES (:id, 'read:audit')"
                    ),
                    {"id": role_id},
                )
                db.execute(
                    text(
                        "INSERT INTO iam_user_role_assignments "
                        "(id, user_id, role_id, source, source_key, "
                        "assigned_by_user_id) VALUES "
                        "(:id, :target_id, :role_id, 'local', '', :creator_id)"
                    ),
                    {
                        "id": assignment_id,
                        "target_id": target_id,
                        "role_id": role_id,
                        "creator_id": creator_id,
                    },
                )
                now = datetime.now(timezone.utc)
                db.add_all(
                    [
                        IAMGroup(
                            id=group_id,
                            key="review-team",
                            name="Review team",
                            description="",
                            source="local",
                            is_system=False,
                            revision=1,
                            created_by_user_id=creator_id,
                        ),
                        IAMGroupMembership(
                            id=membership_id,
                            group_id=group_id,
                            user_id=target_id,
                            source="local",
                            source_key="",
                            assigned_by_user_id=creator_id,
                        ),
                        IAMGroupRoleAssignment(
                            id=group_role_id,
                            group_id=group_id,
                            role_id=role_id,
                            assigned_by_user_id=creator_id,
                        ),
                        ServiceAccount(
                            id=service_account_id,
                            key="review-exporter",
                            name="Review exporter",
                            description="",
                            is_active=True,
                            revision=1,
                            created_by_user_id=creator_id,
                        ),
                        ServiceAccountRoleAssignment(
                            id=service_account_role_id,
                            service_account_id=service_account_id,
                            role_id=role_id,
                            assigned_by_user_id=creator_id,
                        ),
                        OIDCProvider(
                            id=provider_id,
                            system_key="review-provider",
                            name="Review provider",
                            enabled=True,
                            issuer_url="https://idp.example.test/application/o/review/",
                            client_id="review-client",
                            public_base_url="https://threatlens.example.test",
                            scopes=["openid", "email"],
                            role_mappings_json=[],
                        ),
                        OIDCAccessPolicy(
                            id=policy_id,
                            provider_id=provider_id,
                            enabled=True,
                            revision=1,
                            updated_by_user_id=creator_id,
                        ),
                        OIDCClaimMappingSet(
                            id=mapping_set_id,
                            access_policy_id=policy_id,
                            key="review-mappings",
                            name="Review mappings",
                            claim_path="groups",
                            missing_claim_behavior="preserve",
                            enabled=True,
                            revision=1,
                            updated_by_user_id=creator_id,
                        ),
                        OIDCRoleClaimMapping(
                            id=role_mapping_id,
                            mapping_set_id=mapping_set_id,
                            source_key=f"oidc:role:{uuid.uuid4().hex}",
                            claim_value="review-auditors",
                            role_id=role_id,
                            role_is_system=False,
                        ),
                        OIDCGroupClaimMapping(
                            id=group_mapping_id,
                            mapping_set_id=mapping_set_id,
                            source_key=f"oidc:group:{uuid.uuid4().hex}",
                            claim_value="review-team",
                            group_id=group_id,
                            group_is_system=False,
                        ),
                        TemporaryElevation(
                            id=elevation_id,
                            target_user_id=target_id,
                            target_email_snapshot="target@example.test",
                            role_id=role_id,
                            role_key_snapshot="review-auditor",
                            role_name_snapshot="Review auditor",
                            role_revision_snapshot=1,
                            requested_by_user_id=creator_id,
                            requested_by_email_snapshot="creator@example.test",
                            requested_duration_seconds=3_600,
                            request_reason="Review elevated incident access",
                            request_expires_at=now + timedelta(days=1),
                            status="approved",
                            revision=1,
                            decided_by_user_id=reviewer_id,
                            decided_by_email_snapshot="reviewer@example.test",
                            decided_at=now,
                            decision_reason="Approved for review coverage",
                            grant_started_at=now,
                            grant_expires_at=now + timedelta(seconds=3_600),
                            created_at=now,
                            updated_at=now,
                        ),
                        TemporaryElevationPermission(
                            elevation_id=elevation_id,
                            permission="read:audit",
                        ),
                    ]
                )
                db.flush()
                creator = db.get(User, creator_id)
                reviewer = db.get(User, reviewer_id)
                assert creator is not None and reviewer is not None

                campaign = create_access_review_campaign(
                    db,
                    creator=creator,
                    payload=AccessReviewCampaignCreate(
                        name="Retain path review",
                        user_ids=[target_id],
                        service_account_ids=[service_account_id],
                        include_oidc_mappings=True,
                    ),
                )
                items = list(
                    db.scalars(
                        select(AccessReviewItem).where(
                            AccessReviewItem.campaign_id == campaign.id
                        )
                    ).all()
                )
                assert {item.item_type for item in items} == {
                    "direct_user_role",
                    "legacy_user_role",
                    "group_membership",
                    "service_account_role",
                    "oidc_role_mapping",
                    "oidc_group_mapping",
                    "live_elevation",
                }
                assert campaign.item_count == len(items) == 7

                record_access_review_decisions(
                    db,
                    campaign_id=campaign.id,
                    reviewer=reviewer,
                    payload=AccessReviewDecisionBatchRequest(
                        expected_revision=1,
                        decisions=[
                            AccessReviewDecisionInput(
                                item_id=item.id,
                                decision="retain",
                                reason="This access remains required",
                            )
                            for item in items
                        ],
                    ),
                )
                assert db.get(IAMUserRoleAssignment, assignment_id) is not None
                close_access_review_campaign(
                    db,
                    campaign_id=campaign.id,
                    actor=reviewer,
                    payload=AccessReviewTransitionRequest(
                        expected_revision=2,
                        reason="Every assignment has been reviewed",
                    ),
                )
                applying_campaign = begin_access_review_apply(
                    db,
                    campaign_id=campaign.id,
                    actor=reviewer,
                    payload=AccessReviewBeginApplyRequest(expected_revision=3),
                )
                receipt_results = [
                    apply_access_review_item(
                        db,
                        campaign_id=campaign.id,
                        item_id=item.id,
                        actor=reviewer,
                        expected_revision=applying_campaign.revision,
                        expected_item_fingerprint=item.assignment_fingerprint,
                    )
                    for item in items
                ]
                receipts = [result.receipt for result in receipt_results]
                assert {receipt.outcome for receipt in receipts} == {"retained"}
                assert not any(receipt.mutation_performed for receipt in receipts)
                complete_access_review_apply(
                    db,
                    campaign_id=campaign.id,
                    actor=reviewer,
                    expected_revision=4,
                )
                assert db.get(AccessReviewCampaign, campaign.id).status == "applied"
                assert db.get(IAMUserRoleAssignment, assignment_id) is not None

                revoke_campaign = create_access_review_campaign(
                    db,
                    creator=creator,
                    payload=AccessReviewCampaignCreate(
                        name="Explicit revoke review",
                        user_ids=[target_id],
                        include_oidc_mappings=False,
                    ),
                )
                revoke_items = list(
                    db.scalars(
                        select(AccessReviewItem).where(
                            AccessReviewItem.campaign_id == revoke_campaign.id
                        )
                    ).all()
                )
                record_access_review_decisions(
                    db,
                    campaign_id=revoke_campaign.id,
                    reviewer=reviewer,
                    payload=AccessReviewDecisionBatchRequest(
                        expected_revision=1,
                        decisions=[
                            AccessReviewDecisionInput(
                                item_id=item.id,
                                decision=(
                                    "revoke"
                                    if item.item_type == "direct_user_role"
                                    else "retain"
                                ),
                                reason="Apply this reviewed access decision",
                            )
                            for item in revoke_items
                        ],
                    ),
                )
                assert db.get(IAMUserRoleAssignment, assignment_id) is not None
                close_access_review_campaign(
                    db,
                    campaign_id=revoke_campaign.id,
                    actor=reviewer,
                    payload=AccessReviewTransitionRequest(
                        expected_revision=2,
                        reason="Every revoke campaign item is decided",
                    ),
                )
                applying_revoke_campaign = begin_access_review_apply(
                    db,
                    campaign_id=revoke_campaign.id,
                    actor=reviewer,
                    payload=AccessReviewBeginApplyRequest(expected_revision=3),
                )

                def revoke_assignment(session, context):
                    assignment = session.get(
                        IAMUserRoleAssignment, context.current_assignment.assignment_id
                    )
                    assert assignment is not None
                    session.delete(assignment)
                    return AccessReviewMutationResult(
                        mutation_performed=True,
                        result_snapshot={
                            "assignment_id": str(assignment.id),
                            "iam_policy_revision": 1,
                        },
                    )

                revoke_results = [
                    apply_access_review_item(
                        db,
                        campaign_id=revoke_campaign.id,
                        item_id=item.id,
                        actor=reviewer,
                        expected_revision=applying_revoke_campaign.revision,
                        expected_item_fingerprint=item.assignment_fingerprint,
                        coordinator=revoke_assignment,
                    )
                    for item in revoke_items
                ]
                revoke_receipts = [result.receipt for result in revoke_results]
                assert {receipt.outcome for receipt in revoke_receipts} == {
                    "retained",
                    "revoked",
                }
                complete_access_review_apply(
                    db,
                    campaign_id=revoke_campaign.id,
                    actor=reviewer,
                    expected_revision=4,
                )
                assert db.get(IAMUserRoleAssignment, assignment_id) is None
                db.commit()
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_access_review_migration_preserves_immutable_history(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0068_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    creator_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()
    applier_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    item_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    latest_decision_id = uuid.uuid4()
    receipt_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    target_id = uuid.uuid4()
    fingerprint = "a" * 64

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".alembic_version '
                "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
            )
        )

    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0067_action_approvals")
            command.upgrade(config, "0068_access_reviews")

            inspector = inspect(schema_engine)
            assert {
                "access_review_campaigns",
                "access_review_items",
                "access_review_decisions",
                "access_review_apply_receipts",
            } <= set(inspector.get_table_names(schema=schema_name))
            campaign_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "access_review_campaigns", schema=schema_name
                )
            }
            assert {
                "ck_access_review_campaigns_state",
                "ck_access_review_campaigns_scope",
                "ck_access_review_campaigns_item_count",
            } <= campaign_checks
            item_indexes = {
                index["name"]
                for index in inspector.get_indexes(
                    "access_review_items", schema=schema_name
                )
            }
            assert {
                "ix_access_review_items_campaign_type_ordinal",
                "ix_access_review_items_principal",
                "ix_access_review_items_target",
            } <= item_indexes
            campaign_unique_constraints = {
                constraint["name"]: constraint
                for constraint in inspector.get_unique_constraints(
                    "access_review_campaigns", schema=schema_name
                )
            }
            assert campaign_unique_constraints[
                "uq_access_review_campaigns_apply_run_owner"
            ]["column_names"] == ["id", "apply_run_id"]
            receipt_foreign_keys = {
                constraint["name"]: constraint
                for constraint in inspector.get_foreign_keys(
                    "access_review_apply_receipts", schema=schema_name
                )
            }
            campaign_run_foreign_key = receipt_foreign_keys[
                "fk_access_review_apply_receipts_campaign_run"
            ]
            assert campaign_run_foreign_key["constrained_columns"] == [
                "campaign_id",
                "apply_run_id",
            ]
            assert campaign_run_foreign_key["referred_columns"] == [
                "id",
                "apply_run_id",
            ]
            with schema_engine.connect() as connection:
                triggers = set(
                    connection.scalars(
                        text(
                            "SELECT tgname FROM pg_trigger "
                            "WHERE tgrelid IN ("
                            "'access_review_campaigns'::regclass, "
                            "'access_review_items'::regclass, "
                            "'access_review_decisions'::regclass, "
                            "'access_review_apply_receipts'::regclass) "
                            "AND NOT tgisinternal"
                        )
                    ).all()
                )
                item_count_trigger = connection.execute(
                    text(
                        "SELECT trigger.tgdeferrable, trigger.tginitdeferred "
                        "FROM pg_trigger AS trigger "
                        "JOIN pg_class AS relation "
                        "ON relation.oid = trigger.tgrelid "
                        "JOIN pg_namespace AS namespace "
                        "ON namespace.oid = relation.relnamespace "
                        "WHERE trigger.tgname = "
                        "'trg_access_review_campaign_item_count' "
                        "AND namespace.nspname = :schema_name"
                    ),
                    {"schema_name": schema_name},
                ).one()
            assert triggers == {
                "trg_access_review_campaign_item_count",
                "trg_access_review_campaign_history",
                "trg_access_review_item_insert_guard",
                "trg_access_review_item_history",
                "trg_access_review_decision_insert_guard",
                "trg_access_review_decision_history",
                "trg_access_review_receipt_insert_guard",
                "trg_access_review_receipt_history",
            }
            assert tuple(item_count_trigger) == (True, True)

            with schema_engine.begin() as connection:
                for user_id, email in (
                    (creator_id, "creator@example.test"),
                    (reviewer_id, "reviewer@example.test"),
                    (applier_id, "applier@example.test"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, password_hash, role, is_approved) "
                            "VALUES (:id, :email, 'hash', 'admin', true)"
                        ),
                        {"id": user_id, "email": email},
                    )
                connection.execute(
                    text(
                        "INSERT INTO access_review_campaigns "
                        "(id, name, description, scope_snapshot, scope_digest, "
                        "snapshot_at, review_due_at, item_count, created_by_user_id, "
                        "created_by_email_snapshot, status, revision) VALUES "
                        "(:id, 'Quarterly review', '', "
                        "'{\"schema_version\": 1}'::jsonb, :digest, now(), "
                        "now() + interval '14 days', 1, :creator_id, "
                        "'creator@example.test', 'open', 1)"
                    ),
                    {
                        "id": campaign_id,
                        "digest": "b" * 64,
                        "creator_id": creator_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO access_review_items "
                        "(id, campaign_id, ordinal, item_type, assignment_id, "
                        "assignment_source, assignment_fingerprint, principal_type, "
                        "principal_id_snapshot, principal_label_snapshot, target_type, "
                        "target_id_snapshot, target_key_snapshot, target_label_snapshot, "
                        "target_revision_snapshot, permissions_snapshot, "
                        "provenance_snapshot, assignment_created_at_snapshot) VALUES "
                        "(:id, :campaign_id, 1, 'legacy_user_role', :assignment_id, "
                        "'legacy', :fingerprint, 'user', :principal_id, "
                        "'creator@example.test', 'role', :target_id, 'auditor', "
                        "'Auditor', 1, '[\"read:audit\"]'::jsonb, "
                        "'{\"schema_version\": 1}'::jsonb, now())"
                    ),
                    {
                        "id": item_id,
                        "campaign_id": campaign_id,
                        "assignment_id": assignment_id,
                        "fingerprint": fingerprint,
                        "principal_id": creator_id,
                        "target_id": target_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO access_review_decisions "
                        "(id, campaign_id, item_id, item_fingerprint, sequence, "
                        "decision, decided_by_user_id, decided_by_email_snapshot, "
                        "reason, decided_at) VALUES "
                        "(:id, :campaign_id, :item_id, :fingerprint, 1, 'retain', "
                        ":reviewer_id, 'reviewer@example.test', "
                        "'Access remains required', now())"
                    ),
                    {
                        "id": decision_id,
                        "campaign_id": campaign_id,
                        "item_id": item_id,
                        "fingerprint": fingerprint,
                        "reviewer_id": reviewer_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO access_review_decisions "
                        "(id, campaign_id, item_id, item_fingerprint, sequence, "
                        "decision, decided_by_user_id, decided_by_email_snapshot, "
                        "reason, decided_at) VALUES "
                        "(:id, :campaign_id, :item_id, :fingerprint, 2, 'revoke', "
                        ":reviewer_id, 'reviewer@example.test', "
                        "'Access should be removed', now())"
                    ),
                    {
                        "id": latest_decision_id,
                        "campaign_id": campaign_id,
                        "item_id": item_id,
                        "fingerprint": fingerprint,
                        "reviewer_id": reviewer_id,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE access_review_campaigns "
                        "SET revision = revision + 1, updated_at = now() WHERE id = :id"
                    ),
                    {"id": campaign_id},
                )

            with pytest.raises(IntegrityError) as incomplete_campaign:
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO access_review_campaigns "
                            "(id, name, description, scope_snapshot, scope_digest, "
                            "snapshot_at, review_due_at, item_count, "
                            "created_by_email_snapshot, status, revision) VALUES "
                            "(:id, 'Incomplete review', '', "
                            "'{\"schema_version\": 1}'::jsonb, :digest, now(), "
                            "now() + interval '14 days', 1, "
                            "'creator@example.test', 'open', 1)"
                        ),
                        {"id": uuid.uuid4(), "digest": "d" * 64},
                    )
            assert (
                incomplete_campaign.value.orig.diag.constraint_name
                == "ck_access_review_campaigns_exact_item_count"
            )

            undecided_campaign_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO access_review_campaigns "
                        "(id, name, description, scope_snapshot, scope_digest, "
                        "snapshot_at, review_due_at, item_count, "
                        "created_by_email_snapshot, status, revision) VALUES "
                        "(:id, 'Undecided review', '', "
                        "'{\"schema_version\": 1}'::jsonb, :digest, now(), "
                        "now() + interval '14 days', 1, "
                        "'creator@example.test', 'open', 1)"
                    ),
                    {"id": undecided_campaign_id, "digest": "f" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO access_review_items "
                        "(id, campaign_id, ordinal, item_type, assignment_id, "
                        "assignment_source, assignment_fingerprint, principal_type, "
                        "principal_id_snapshot, principal_label_snapshot, target_type, "
                        "target_id_snapshot, target_key_snapshot, target_label_snapshot, "
                        "target_revision_snapshot, permissions_snapshot, "
                        "provenance_snapshot, assignment_created_at_snapshot) VALUES "
                        "(:id, :campaign_id, 1, 'legacy_user_role', :assignment_id, "
                        "'legacy', :fingerprint, 'user', :principal_id, "
                        "'creator@example.test', 'role', :target_id, 'viewer', "
                        "'Viewer', 1, '[]'::jsonb, '{}'::jsonb, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "campaign_id": undecided_campaign_id,
                        "assignment_id": uuid.uuid4(),
                        "fingerprint": "f" * 64,
                        "principal_id": creator_id,
                        "target_id": uuid.uuid4(),
                    },
                )
            with pytest.raises(DBAPIError, match="incomplete decisions"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE access_review_campaigns SET status = 'closed', "
                            "revision = revision + 1, closed_by_user_id = :actor_id, "
                            "closed_by_email_snapshot = 'applier@example.test', "
                            "closed_at = now(), close_reason = 'Review complete', "
                            "updated_at = now() WHERE id = :id"
                        ),
                        {"id": undecided_campaign_id, "actor_id": applier_id},
                    )

            with pytest.raises(
                IntegrityError, match="ordinal exceeds the campaign item_count"
            ):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO access_review_items "
                            "(id, campaign_id, ordinal, item_type, assignment_id, "
                            "assignment_source, assignment_fingerprint, "
                            "principal_type, principal_id_snapshot, "
                            "principal_label_snapshot, target_type, "
                            "target_id_snapshot, target_key_snapshot, "
                            "target_label_snapshot, target_revision_snapshot, "
                            "permissions_snapshot, provenance_snapshot, "
                            "assignment_created_at_snapshot) VALUES "
                            "(:id, :campaign_id, 2, 'legacy_user_role', "
                            ":assignment_id, 'legacy', :fingerprint, 'user', "
                            ":principal_id, 'creator@example.test', 'role', "
                            ":target_id, 'admin', 'Admin', 1, '[]'::jsonb, "
                            "'{}'::jsonb, now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "campaign_id": campaign_id,
                            "assignment_id": uuid.uuid4(),
                            "fingerprint": "e" * 64,
                            "principal_id": creator_id,
                            "target_id": uuid.uuid4(),
                        },
                    )

            with pytest.raises(DBAPIError, match="campaign snapshots are immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE access_review_campaigns SET name = 'Rewritten' "
                            "WHERE id = :id"
                        ),
                        {"id": campaign_id},
                    )
            with pytest.raises(DBAPIError, match="history rows are immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE access_review_items SET target_label_snapshot = "
                            "'Changed' WHERE id = :id"
                        ),
                        {"id": item_id},
                    )
            with pytest.raises(DBAPIError, match="decisions are immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE access_review_decisions SET reason = 'rewritten' "
                            "WHERE id = :id"
                        ),
                        {"id": decision_id},
                    )
            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO access_review_decisions "
                            "(id, campaign_id, item_id, item_fingerprint, sequence, "
                            "decision, decided_by_email_snapshot, reason, decided_at) "
                            "VALUES (:id, :campaign_id, :item_id, :fingerprint, 3, "
                            "'revoke', 'reviewer@example.test', 'wrong snapshot', now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "campaign_id": campaign_id,
                            "item_id": item_id,
                            "fingerprint": "c" * 64,
                        },
                    )

            apply_run_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE access_review_campaigns SET status = 'closed', "
                        "revision = revision + 1, closed_by_user_id = :actor_id, "
                        "closed_by_email_snapshot = 'applier@example.test', "
                        "closed_at = now(), close_reason = 'Review complete', "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": campaign_id, "actor_id": applier_id},
                )

            with pytest.raises(DBAPIError, match="only while a campaign is open"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO access_review_decisions "
                            "(id, campaign_id, item_id, item_fingerprint, sequence, "
                            "decision, decided_by_email_snapshot, reason, decided_at) "
                            "VALUES (:id, :campaign_id, :item_id, :fingerprint, 2, "
                            "'revoke', 'reviewer@example.test', "
                            "'Late decision rejected', now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "campaign_id": campaign_id,
                            "item_id": item_id,
                            "fingerprint": fingerprint,
                        },
                    )

            with pytest.raises(DBAPIError, match="only while a campaign is open"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO access_review_items "
                            "(id, campaign_id, ordinal, item_type, assignment_id, "
                            "assignment_source, assignment_fingerprint, "
                            "principal_type, principal_id_snapshot, "
                            "principal_label_snapshot, target_type, "
                            "target_id_snapshot, target_key_snapshot, "
                            "target_label_snapshot, target_revision_snapshot, "
                            "permissions_snapshot, provenance_snapshot, "
                            "assignment_created_at_snapshot) VALUES "
                            "(:id, :campaign_id, 2, 'legacy_user_role', "
                            ":assignment_id, 'legacy', :fingerprint, 'user', "
                            ":principal_id, 'creator@example.test', 'role', "
                            ":target_id, 'admin', 'Admin', 1, '[]'::jsonb, "
                            "'{}'::jsonb, now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "campaign_id": campaign_id,
                            "assignment_id": uuid.uuid4(),
                            "fingerprint": "e" * 64,
                            "principal_id": creator_id,
                            "target_id": uuid.uuid4(),
                        },
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE access_review_campaigns SET status = 'applying', "
                        "revision = revision + 1, "
                        "apply_started_by_user_id = :actor_id, "
                        "apply_started_by_email_snapshot = 'applier@example.test', "
                        "apply_started_at = now(), apply_run_id = :run_id, "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {
                        "id": campaign_id,
                        "actor_id": applier_id,
                        "run_id": apply_run_id,
                    },
                )

            with pytest.raises(DBAPIError, match="unresolved apply results"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE access_review_campaigns SET status = 'applied', "
                            "revision = revision + 1, applied_by_user_id = :actor_id, "
                            "applied_by_email_snapshot = 'applier@example.test', "
                            "applied_at = now(), updated_at = now() WHERE id = :id"
                        ),
                        {"id": campaign_id, "actor_id": applier_id},
                    )

            insert_receipt = text(
                "INSERT INTO access_review_apply_receipts "
                "(id, campaign_id, item_id, item_fingerprint, decision_id, "
                "apply_run_id, attempt, outcome, expected_target_revision, "
                "observed_target_revision, observed_fingerprint, "
                "mutation_performed, detail_code, detail, result_snapshot, "
                "applied_by_user_id, applied_by_email_snapshot, created_at) "
                "VALUES (:id, :campaign_id, :item_id, :fingerprint, "
                ":decision_id, :run_id, :attempt, :outcome, 1, 1, :fingerprint, "
                "false, :detail_code, :detail, '{}'::jsonb, :actor_id, "
                "'applier@example.test', now())"
            )
            receipt_parameters = {
                "campaign_id": campaign_id,
                "item_id": item_id,
                "fingerprint": fingerprint,
                "decision_id": latest_decision_id,
                "actor_id": applier_id,
            }

            with pytest.raises(DBAPIError, match="apply run does not match"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        insert_receipt,
                        receipt_parameters
                        | {
                            "id": uuid.uuid4(),
                            "run_id": uuid.uuid4(),
                            "attempt": 99,
                            "outcome": "retained",
                            "detail_code": "assignment_retained",
                            "detail": "Assignment retained",
                        },
                    )

            with pytest.raises(DBAPIError, match="latest item decision"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        insert_receipt,
                        receipt_parameters
                        | {
                            "id": uuid.uuid4(),
                            "decision_id": decision_id,
                            "run_id": apply_run_id,
                            "attempt": 1,
                            "outcome": "manual_action_required",
                            "detail_code": "external_access_change_required",
                            "detail": "External access change required",
                        },
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    insert_receipt,
                    receipt_parameters
                    | {
                        "id": receipt_id,
                        "run_id": apply_run_id,
                        "attempt": 1,
                        "outcome": "manual_action_required",
                        "detail_code": "external_access_change_required",
                        "detail": "External access change required",
                    },
                )

            with pytest.raises(DBAPIError, match="attempts must be contiguous"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        insert_receipt,
                        receipt_parameters
                        | {
                            "id": uuid.uuid4(),
                            "run_id": apply_run_id,
                            "attempt": 3,
                            "outcome": "superseded",
                            "detail_code": "review_item_superseded",
                            "detail": "Review item superseded",
                        },
                    )

            with pytest.raises(IntegrityError) as invalid_receipt:
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO access_review_apply_receipts "
                            "(id, campaign_id, item_id, item_fingerprint, decision_id, "
                            "apply_run_id, attempt, outcome, expected_target_revision, "
                            "mutation_performed, detail_code, detail, result_snapshot, "
                            "applied_by_email_snapshot, created_at) VALUES "
                            "(:id, :campaign_id, :item_id, :fingerprint, :decision_id, "
                            ":run_id, 2, 'revoked', 1, false, 'assignment_revoked', "
                            "'Assignment revoked', '{}'::jsonb, "
                            "'applier@example.test', now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "campaign_id": campaign_id,
                            "item_id": item_id,
                            "fingerprint": fingerprint,
                            "decision_id": latest_decision_id,
                            "run_id": apply_run_id,
                        },
                    )
            assert (
                invalid_receipt.value.orig.diag.constraint_name
                == "ck_access_review_apply_receipts_mutation_outcome"
            )
            with schema_engine.begin() as connection:
                connection.execute(
                    insert_receipt,
                    receipt_parameters
                    | {
                        "id": uuid.uuid4(),
                        "run_id": apply_run_id,
                        "attempt": 2,
                        "outcome": "superseded",
                        "detail_code": "review_item_superseded",
                        "detail": "Review item superseded",
                    },
                )
                assert set(
                    connection.scalars(
                        text(
                            "SELECT outcome FROM access_review_apply_receipts "
                            "WHERE campaign_id = :campaign_id"
                        ),
                        {"campaign_id": campaign_id},
                    ).all()
                ) == {"manual_action_required", "superseded"}

            with pytest.raises(DBAPIError, match="terminal apply result"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        insert_receipt,
                        receipt_parameters
                        | {
                            "id": uuid.uuid4(),
                            "run_id": apply_run_id,
                            "attempt": 3,
                            "outcome": "failed",
                            "detail_code": "coordinator_failed",
                            "detail": "Coordinator failed",
                        },
                    )
            with pytest.raises(DBAPIError, match="apply receipts are immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE access_review_apply_receipts "
                            "SET detail = 'Rewritten receipt' WHERE id = :id"
                        ),
                        {"id": receipt_id},
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE access_review_campaigns SET status = 'applied', "
                        "revision = revision + 1, applied_by_user_id = :actor_id, "
                        "applied_by_email_snapshot = 'applier@example.test', "
                        "applied_at = now(), updated_at = now() WHERE id = :id"
                    ),
                    {"id": campaign_id, "actor_id": applier_id},
                )

            with pytest.raises(DBAPIError, match="only while a campaign is applying"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        insert_receipt,
                        receipt_parameters
                        | {
                            "id": uuid.uuid4(),
                            "run_id": apply_run_id,
                            "attempt": 5,
                            "outcome": "superseded",
                            "detail_code": "superseded",
                            "detail": "Superseded after completion",
                        },
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM users WHERE id IN "
                        "(:creator_id, :reviewer_id, :applier_id)"
                    ),
                    {
                        "creator_id": creator_id,
                        "reviewer_id": reviewer_id,
                        "applier_id": applier_id,
                    },
                )
                actor_references = connection.execute(
                    text(
                        "SELECT created_by_user_id, closed_by_user_id, "
                        "apply_started_by_user_id, applied_by_user_id "
                        "FROM access_review_campaigns "
                        "WHERE id = :id"
                    ),
                    {"id": campaign_id},
                ).one()
                assert tuple(actor_references) == (None, None, None, None)
                assert (
                    connection.scalar(
                        text(
                            "SELECT decided_by_user_id FROM access_review_decisions "
                            "WHERE id = :id"
                        ),
                        {"id": decision_id},
                    )
                    is None
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT applied_by_user_id FROM access_review_apply_receipts "
                            "WHERE id = :id"
                        ),
                        {"id": receipt_id},
                    )
                    is None
                )

            with pytest.raises(RuntimeError, match="immutable governance history"):
                command.downgrade(config, "0067_action_approvals")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "TRUNCATE access_review_apply_receipts, "
                        "access_review_decisions, access_review_items, "
                        "access_review_campaigns"
                    )
                )
            command.downgrade(config, "0067_action_approvals")
            inspector = inspect(schema_engine)
            assert "access_review_campaigns" not in inspector.get_table_names(
                schema=schema_name
            )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
