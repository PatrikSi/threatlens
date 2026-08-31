"""add revisioned OIDC custom claim mappings

Revision ID: 0065_oidc_claim_mappings
Revises: 0064_service_accounts
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0065_oidc_claim_mappings"
down_revision = "0064_service_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    legacy_counts = {
        "iam_user_role_assignments": int(
            bind.scalar(
                sa.text(
                    "SELECT count(*) FROM iam_user_role_assignments WHERE source = 'oidc'"
                )
            )
            or 0
        ),
        "iam_group_memberships": int(
            bind.scalar(
                sa.text(
                    "SELECT count(*) FROM iam_group_memberships WHERE source = 'oidc'"
                )
            )
            or 0
        ),
    }
    populated_legacy = [
        f"{table}={count}" for table, count in legacy_counts.items() if count
    ]
    if populated_legacy:
        raise RuntimeError(
            "Cannot enable managed OIDC claim mappings while unsupported preexisting "
            "OIDC IAM grants exist "
            f"({', '.join(populated_legacy)}). Export those rows, replace them with "
            "locally managed assignments, remove the OIDC-sourced rows, and retry the "
            "upgrade. Run scripts/prepare_oidc_access_upgrade.py for the supported "
            "preflight and conversion. ThreatLens will not silently expire or "
            "reinterpret existing access."
        )

    op.add_column(
        "oidc_providers",
        sa.Column(
            "oidc_access_policy_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_oidc_providers_access_policy_generation",
        "oidc_providers",
        "oidc_access_policy_generation >= 0",
    )
    op.add_column(
        "external_identities",
        sa.Column("role_sync_provenance", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "external_identities",
        sa.Column("role_sync_previous_role", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "external_identities",
        sa.Column("role_sync_applied_role", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "external_identities",
        sa.Column(
            "role_sync_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_external_identities_role_sync_provenance",
        "external_identities",
        "(role_sync_provenance IS NULL AND role_sync_previous_role IS NULL "
        "AND role_sync_applied_role IS NULL AND role_sync_updated_at IS NULL) OR "
        "(role_sync_provenance IN ('tracked', 'legacy') "
        "AND role_sync_applied_role IN ('admin', 'analyst', 'viewer') "
        "AND (role_sync_previous_role IS NULL OR "
        "role_sync_previous_role IN ('admin', 'analyst', 'viewer')) "
        "AND role_sync_updated_at IS NOT NULL)",
    )
    bind.execute(
        sa.text(
            "UPDATE external_identities AS identity "
            "SET role_sync_provenance = 'legacy', "
            "role_sync_applied_role = users.role, role_sync_updated_at = now() "
            "FROM users, oidc_providers AS provider "
            "WHERE identity.user_id = users.id "
            "AND identity.provider_id = provider.id "
            "AND provider.sync_roles_on_login = true"
        )
    )
    # These redundant unique indexes let PostgreSQL enforce that an OIDC mapping
    # always points at the non-system form of an IAM target with a composite FK.
    op.create_index(
        "ux_iam_roles_id_is_system_oidc",
        "iam_roles",
        ["id", "is_system"],
        unique=True,
    )
    op.create_index(
        "ux_iam_groups_id_is_system_oidc",
        "iam_groups",
        ["id", "is_system"],
        unique=True,
    )

    op.create_table(
        "oidc_access_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_oidc_access_policies_revision",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["oidc_providers.id"],
            ondelete="CASCADE",
            name="fk_oidc_access_policies_provider",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_oidc_access_policies_updated_by",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id",
            name="uq_oidc_access_policies_provider",
        ),
    )
    op.create_index(
        "ix_oidc_access_policies_updated_by",
        "oidc_access_policies",
        ["updated_by_user_id"],
    )

    op.create_table(
        "oidc_claim_mapping_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("access_policy_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("claim_path", sa.String(length=255), nullable=False),
        sa.Column(
            "missing_claim_behavior",
            sa.String(length=16),
            nullable=False,
            server_default="preserve",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "key ~ '^[a-z][a-z0-9-]{1,62}[a-z0-9]$'",
            name="ck_oidc_claim_mapping_sets_key",
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND length(name) > 0 AND name !~ '[[:cntrl:]]'",
            name="ck_oidc_claim_mapping_sets_name",
        ),
        sa.CheckConstraint(
            "claim_path ~ '^[A-Za-z0-9_:-]+([.][A-Za-z0-9_:-]+)*$'",
            name="ck_oidc_claim_mapping_sets_claim_path",
        ),
        sa.CheckConstraint(
            "missing_claim_behavior IN ('preserve', 'remove', 'deny')",
            name="ck_oidc_claim_mapping_sets_missing_behavior",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_oidc_claim_mapping_sets_revision",
        ),
        sa.ForeignKeyConstraint(
            ["access_policy_id"],
            ["oidc_access_policies.id"],
            ondelete="CASCADE",
            name="fk_oidc_claim_mapping_sets_policy",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_oidc_claim_mapping_sets_updated_by",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "access_policy_id",
            "key",
            name="uq_oidc_claim_mapping_sets_policy_key",
        ),
    )
    op.create_index(
        "ix_oidc_claim_mapping_sets_policy_enabled_name",
        "oidc_claim_mapping_sets",
        ["access_policy_id", "enabled", "name"],
    )
    op.create_index(
        "ix_oidc_claim_mapping_sets_updated_by",
        "oidc_claim_mapping_sets",
        ["updated_by_user_id"],
    )

    op.create_table(
        "oidc_role_claim_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mapping_set_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column(
            "claim_value",
            sa.String(length=512, collation="C"),
            nullable=False,
        ),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role_is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source_key ~ '^oidc:role:[0-9a-f]{32}$'",
            name="ck_oidc_role_claim_mappings_source_key",
        ),
        sa.CheckConstraint(
            "length(claim_value) > 0 AND claim_value = btrim(claim_value) "
            "AND claim_value !~ '[[:cntrl:]]'",
            name="ck_oidc_role_claim_mappings_claim_value",
        ),
        sa.CheckConstraint(
            "NOT role_is_system",
            name="ck_oidc_role_claim_mappings_custom_role",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_set_id"],
            ["oidc_claim_mapping_sets.id"],
            ondelete="CASCADE",
            name="fk_oidc_role_claim_mappings_set",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "role_is_system"],
            ["iam_roles.id", "iam_roles.is_system"],
            ondelete="RESTRICT",
            name="fk_oidc_role_claim_mappings_custom_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_key",
            name="uq_oidc_role_claim_mappings_source_key",
        ),
        sa.UniqueConstraint(
            "mapping_set_id",
            "claim_value",
            name="uq_oidc_role_claim_mappings_set_value",
        ),
        sa.UniqueConstraint(
            "id",
            "source_key",
            "role_id",
            name="uq_oidc_role_claim_mappings_grant_owner",
        ),
    )
    op.create_index(
        "ix_oidc_role_claim_mappings_role",
        "oidc_role_claim_mappings",
        ["role_id"],
    )

    op.create_table(
        "oidc_group_claim_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mapping_set_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column(
            "claim_value",
            sa.String(length=512, collation="C"),
            nullable=False,
        ),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column(
            "group_is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source_key ~ '^oidc:group:[0-9a-f]{32}$'",
            name="ck_oidc_group_claim_mappings_source_key",
        ),
        sa.CheckConstraint(
            "length(claim_value) > 0 AND claim_value = btrim(claim_value) "
            "AND claim_value !~ '[[:cntrl:]]'",
            name="ck_oidc_group_claim_mappings_claim_value",
        ),
        sa.CheckConstraint(
            "NOT group_is_system",
            name="ck_oidc_group_claim_mappings_custom_group",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_set_id"],
            ["oidc_claim_mapping_sets.id"],
            ondelete="CASCADE",
            name="fk_oidc_group_claim_mappings_set",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "group_is_system"],
            ["iam_groups.id", "iam_groups.is_system"],
            ondelete="RESTRICT",
            name="fk_oidc_group_claim_mappings_custom_group",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_key",
            name="uq_oidc_group_claim_mappings_source_key",
        ),
        sa.UniqueConstraint(
            "mapping_set_id",
            "claim_value",
            name="uq_oidc_group_claim_mappings_set_value",
        ),
        sa.UniqueConstraint(
            "id",
            "source_key",
            "group_id",
            name="uq_oidc_group_claim_mappings_grant_owner",
        ),
    )
    op.create_index(
        "ix_oidc_group_claim_mappings_group",
        "oidc_group_claim_mappings",
        ["group_id"],
    )

    op.add_column(
        "iam_user_role_assignments",
        sa.Column("oidc_role_mapping_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "iam_user_role_assignments",
        sa.Column(
            "oidc_assertion_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_iam_user_role_assignments_oidc_mapping",
        "iam_user_role_assignments",
        "oidc_role_claim_mappings",
        ["oidc_role_mapping_id", "source_key", "role_id"],
        ["id", "source_key", "role_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_iam_user_role_assignments_oidc_ownership",
        "iam_user_role_assignments",
        "(source = 'local' AND oidc_role_mapping_id IS NULL "
        "AND oidc_assertion_expires_at IS NULL) OR "
        "(source = 'oidc' AND oidc_role_mapping_id IS NOT NULL "
        "AND oidc_assertion_expires_at IS NOT NULL)",
    )
    op.create_index(
        "ix_iam_user_role_assignments_oidc_mapping",
        "iam_user_role_assignments",
        ["oidc_role_mapping_id"],
    )
    op.create_index(
        "ix_iam_user_role_assignments_oidc_expiry",
        "iam_user_role_assignments",
        ["oidc_assertion_expires_at"],
    )

    op.add_column(
        "iam_group_memberships",
        sa.Column("oidc_group_mapping_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "iam_group_memberships",
        sa.Column(
            "oidc_assertion_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_iam_group_memberships_oidc_mapping",
        "iam_group_memberships",
        "oidc_group_claim_mappings",
        ["oidc_group_mapping_id", "source_key", "group_id"],
        ["id", "source_key", "group_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_iam_group_memberships_oidc_ownership",
        "iam_group_memberships",
        "(source = 'local' AND oidc_group_mapping_id IS NULL "
        "AND oidc_assertion_expires_at IS NULL) OR "
        "(source = 'oidc' AND oidc_group_mapping_id IS NOT NULL "
        "AND oidc_assertion_expires_at IS NOT NULL)",
    )
    op.create_index(
        "ix_iam_group_memberships_oidc_mapping",
        "iam_group_memberships",
        ["oidc_group_mapping_id"],
    )
    op.create_index(
        "ix_iam_group_memberships_oidc_expiry",
        "iam_group_memberships",
        ["oidc_assertion_expires_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: int(bind.scalar(sa.text(f"SELECT count(*) FROM {table}")) or 0)  # noqa: S608
        for table in (
            "oidc_access_policies",
            "oidc_claim_mapping_sets",
            "oidc_role_claim_mappings",
            "oidc_group_claim_mappings",
        )
    }
    counts["iam_user_role_assignments(source=oidc)"] = int(
        bind.scalar(
            sa.text(
                "SELECT count(*) FROM iam_user_role_assignments WHERE source = 'oidc'"
            )
        )
        or 0
    )
    counts["iam_group_memberships(source=oidc)"] = int(
        bind.scalar(
            sa.text("SELECT count(*) FROM iam_group_memberships WHERE source = 'oidc'")
        )
        or 0
    )
    counts["external_identities(role_sync_provenance)"] = int(
        bind.scalar(
            sa.text(
                "SELECT count(*) FROM external_identities "
                "WHERE role_sync_provenance IS NOT NULL"
            )
        )
        or 0
    )
    populated = [f"{table}={count}" for table, count in counts.items() if count]
    if populated:
        raise RuntimeError(
            "Cannot downgrade OIDC claim-mapping persistence while access policy "
            f"state would be lost ({', '.join(populated)}). Export or remove the "
            "OIDC access policy, verify that its mapping inventory is empty, confirm "
            "every linked account's intended local role and clear fixed-role sync "
            "provenance, then retry the downgrade."
        )

    op.drop_index(
        "ix_iam_group_memberships_oidc_expiry",
        table_name="iam_group_memberships",
    )
    op.drop_index(
        "ix_iam_group_memberships_oidc_mapping",
        table_name="iam_group_memberships",
    )
    op.drop_constraint(
        "ck_iam_group_memberships_oidc_ownership",
        "iam_group_memberships",
        type_="check",
    )
    op.drop_constraint(
        "fk_iam_group_memberships_oidc_mapping",
        "iam_group_memberships",
        type_="foreignkey",
    )
    op.drop_column("iam_group_memberships", "oidc_assertion_expires_at")
    op.drop_column("iam_group_memberships", "oidc_group_mapping_id")

    op.drop_index(
        "ix_iam_user_role_assignments_oidc_expiry",
        table_name="iam_user_role_assignments",
    )
    op.drop_index(
        "ix_iam_user_role_assignments_oidc_mapping",
        table_name="iam_user_role_assignments",
    )
    op.drop_constraint(
        "ck_iam_user_role_assignments_oidc_ownership",
        "iam_user_role_assignments",
        type_="check",
    )
    op.drop_constraint(
        "fk_iam_user_role_assignments_oidc_mapping",
        "iam_user_role_assignments",
        type_="foreignkey",
    )
    op.drop_column("iam_user_role_assignments", "oidc_assertion_expires_at")
    op.drop_column("iam_user_role_assignments", "oidc_role_mapping_id")

    op.drop_constraint(
        "ck_external_identities_role_sync_provenance",
        "external_identities",
        type_="check",
    )
    op.drop_column("external_identities", "role_sync_updated_at")
    op.drop_column("external_identities", "role_sync_applied_role")
    op.drop_column("external_identities", "role_sync_previous_role")
    op.drop_column("external_identities", "role_sync_provenance")

    op.drop_index(
        "ix_oidc_group_claim_mappings_group",
        table_name="oidc_group_claim_mappings",
    )
    op.drop_table("oidc_group_claim_mappings")
    op.drop_index(
        "ix_oidc_role_claim_mappings_role",
        table_name="oidc_role_claim_mappings",
    )
    op.drop_table("oidc_role_claim_mappings")
    op.drop_index(
        "ix_oidc_claim_mapping_sets_updated_by",
        table_name="oidc_claim_mapping_sets",
    )
    op.drop_index(
        "ix_oidc_claim_mapping_sets_policy_enabled_name",
        table_name="oidc_claim_mapping_sets",
    )
    op.drop_table("oidc_claim_mapping_sets")
    op.drop_index(
        "ix_oidc_access_policies_updated_by",
        table_name="oidc_access_policies",
    )
    op.drop_table("oidc_access_policies")
    op.drop_index(
        "ux_iam_groups_id_is_system_oidc",
        table_name="iam_groups",
    )
    op.drop_index(
        "ux_iam_roles_id_is_system_oidc",
        table_name="iam_roles",
    )
    op.drop_constraint(
        "ck_oidc_providers_access_policy_generation",
        "oidc_providers",
        type_="check",
    )
    op.drop_column("oidc_providers", "oidc_access_policy_generation")
