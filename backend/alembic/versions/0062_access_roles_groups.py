"""add canonical access roles and groups

Revision ID: 0062_access_roles_groups
Revises: 0061_alert_dispatch_publication
Create Date: 2026-08-30
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


revision = "0062_access_roles_groups"
down_revision = "0061_alert_dispatch_publication"
branch_labels = None
depends_on = None


SYSTEM_ROLE_IDS = {
    "admin": uuid.UUID("00000000-0000-4000-8000-000000000001"),
    "analyst": uuid.UUID("00000000-0000-4000-8000-000000000002"),
    "viewer": uuid.UUID("00000000-0000-4000-8000-000000000003"),
}
ALL_USERS_GROUP_ID = uuid.UUID("00000000-0000-4000-8000-000000000101")

SYSTEM_ROLE_PERMISSIONS = {
    "admin": ("*:*",),
    "analyst": (
        "read:feeds",
        "read:items",
        "read:investigations",
        "read:notifications",
        "read:reports",
        "read:stats",
        "read:tags",
        "write:alerts",
        "write:feeds",
        "write:investigations",
        "write:items",
        "write:notifications",
        "write:reports",
        "write:tags",
        "write:tokens",
        "write:views",
    ),
    "viewer": (
        "read:feeds",
        "read:items",
        "read:investigations",
        "read:notifications",
        "read:reports",
        "read:stats",
        "read:tags",
        "write:alerts",
        "write:tokens",
        "write:views",
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    unknown_roles = bind.execute(
        sa.text(
            "SELECT role, count(*) AS count FROM users "
            "WHERE role NOT IN ('admin', 'analyst', 'viewer') GROUP BY role"
        )
    ).all()
    if unknown_roles:
        summary = ", ".join(f"{row.role} ({row.count})" for row in unknown_roles)
        raise RuntimeError(
            "Cannot migrate access roles while users contain unsupported legacy role "
            f"values: {summary}. Correct those rows to admin, analyst, or viewer and retry."
        )

    op.create_table(
        "iam_policy_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_iam_policy_state_singleton"),
        sa.CheckConstraint("revision >= 1", name="ck_iam_policy_state_revision"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "iam_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("revision >= 1", name="ck_iam_roles_revision"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_iam_roles_key"),
    )
    op.create_index("ix_iam_roles_system_name", "iam_roles", ["is_system", "name"])
    op.create_table(
        "iam_role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=96), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission"),
    )
    op.create_index(
        "ix_iam_role_permissions_permission",
        "iam_role_permissions",
        ["permission"],
    )
    op.create_table(
        "iam_user_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="local"
        ),
        sa.Column(
            "source_key", sa.String(length=255), nullable=False, server_default=""
        ),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source IN ('local', 'oidc')",
            name="ck_iam_user_role_assignments_source",
        ),
        sa.CheckConstraint(
            "(source = 'local' AND source_key = '') OR "
            "(source = 'oidc' AND length(source_key) > 0)",
            name="ck_iam_user_role_assignments_source_key",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "role_id",
            "source",
            "source_key",
            name="uq_iam_user_role_assignments_origin",
        ),
    )
    op.create_index(
        "ix_iam_user_role_assignments_user",
        "iam_user_role_assignments",
        ["user_id"],
    )
    op.create_index(
        "ix_iam_user_role_assignments_role",
        "iam_user_role_assignments",
        ["role_id"],
    )
    _create_group_tables()
    _add_audit_context_columns()

    bind.execute(sa.text("INSERT INTO iam_policy_state (id, revision) VALUES (1, 1)"))
    _seed_system_roles(bind)


def _create_group_tables() -> None:
    op.create_table(
        "iam_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="local"
        ),
        sa.Column("external_key", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("source IN ('local', 'oidc')", name="ck_iam_groups_source"),
        sa.CheckConstraint(
            "(source = 'local' AND external_key IS NULL) OR "
            "(source = 'oidc' AND external_key IS NOT NULL AND length(external_key) > 0)",
            name="ck_iam_groups_external_key",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_iam_groups_revision"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_iam_groups_key"),
        sa.UniqueConstraint(
            "source", "external_key", name="uq_iam_groups_external_origin"
        ),
    )
    op.create_index("ix_iam_groups_source_name", "iam_groups", ["source", "name"])
    op.create_table(
        "iam_group_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="local"
        ),
        sa.Column(
            "source_key", sa.String(length=255), nullable=False, server_default=""
        ),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source IN ('local', 'oidc')", name="ck_iam_group_memberships_source"
        ),
        sa.CheckConstraint(
            "(source = 'local' AND source_key = '') OR "
            "(source = 'oidc' AND length(source_key) > 0)",
            name="ck_iam_group_memberships_source_key",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["group_id"], ["iam_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "user_id",
            "source",
            "source_key",
            name="uq_iam_group_memberships_origin",
        ),
    )
    op.create_index(
        "ix_iam_group_memberships_user", "iam_group_memberships", ["user_id"]
    )
    op.create_index(
        "ix_iam_group_memberships_group", "iam_group_memberships", ["group_id"]
    )
    op.create_table(
        "iam_group_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["group_id"], ["iam_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id", "role_id", name="uq_iam_group_role_assignments"
        ),
    )
    op.create_index(
        "ix_iam_group_role_assignments_role",
        "iam_group_role_assignments",
        ["role_id"],
    )


def _add_audit_context_columns() -> None:
    for column in (
        sa.Column("actor_principal_type", sa.String(length=32), nullable=True),
        sa.Column("actor_principal_id", sa.Uuid(), nullable=True),
        sa.Column("credential_kind", sa.String(length=32), nullable=True),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
    ):
        op.add_column("audit_logs", column)
    op.create_index(
        "ix_audit_logs_actor_principal",
        "audit_logs",
        ["actor_principal_type", "actor_principal_id", "created_at"],
    )
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index(
        "ix_audit_logs_credential_created",
        "audit_logs",
        ["credential_id", "credential_kind", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_resource_created",
        "audit_logs",
        ["resource_type", "resource_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_success_created",
        "audit_logs",
        ["success", "created_at"],
    )


def _seed_system_roles(bind) -> None:
    role_rows = (
        (
            SYSTEM_ROLE_IDS["admin"],
            "admin",
            "Administrator",
            "Sealed deployment administrator and local break-glass role.",
        ),
        (
            SYSTEM_ROLE_IDS["analyst"],
            "analyst",
            "Analyst",
            "Built-in threat analyst compatibility role.",
        ),
        (
            SYSTEM_ROLE_IDS["viewer"],
            "viewer",
            "Viewer",
            "Built-in read-oriented compatibility role.",
        ),
    )
    for role_id, key, name, description in role_rows:
        bind.execute(
            sa.text(
                "INSERT INTO iam_roles "
                "(id, key, name, description, is_system, revision) "
                "VALUES (:id, :key, :name, :description, true, 1)"
            ),
            {"id": role_id, "key": key, "name": name, "description": description},
        )
        for permission in SYSTEM_ROLE_PERMISSIONS[key]:
            bind.execute(
                sa.text(
                    "INSERT INTO iam_role_permissions (role_id, permission) "
                    "VALUES (:role_id, :permission)"
                ),
                {"role_id": role_id, "permission": permission},
            )
    bind.execute(
        sa.text(
            "INSERT INTO iam_groups "
            "(id, key, name, description, source, external_key, is_system, revision) "
            "VALUES (:id, 'all-users', 'All users', "
            "'Compatibility group representing every active approved human user.', "
            "'local', NULL, true, 1)"
        ),
        {"id": ALL_USERS_GROUP_ID},
    )


def downgrade() -> None:
    bind = op.get_bind()
    active_custom_state = bind.scalar(
        sa.text(
            "SELECT ("
            "(SELECT count(*) FROM iam_roles WHERE is_system IS FALSE) + "
            "(SELECT count(*) FROM iam_groups WHERE is_system IS FALSE) + "
            "(SELECT count(*) FROM iam_user_role_assignments) + "
            "(SELECT count(*) FROM iam_group_memberships) + "
            "(SELECT count(*) FROM iam_group_role_assignments)"
            ")"
        )
    )
    if int(active_custom_state or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade access governance while custom roles, groups, or assignments "
            "exist. Remove that state, verify a backup, and retry the downgrade."
        )

    # Preserve enriched evidence in the legacy JSON envelope before removing the
    # additive columns. A downgrade may reduce queryability, but must not erase it.
    op.execute(
        sa.text(
            "UPDATE audit_logs SET metadata_json = metadata_json || "
            "jsonb_build_object('_access_context', jsonb_strip_nulls(jsonb_build_object("
            "'actor_principal_type', actor_principal_type, "
            "'actor_principal_id', actor_principal_id, "
            "'credential_kind', credential_kind, "
            "'credential_id', credential_id, "
            "'request_id', request_id, "
            "'source_ip', source_ip))) "
            "WHERE actor_principal_type IS NOT NULL "
            "OR actor_principal_id IS NOT NULL "
            "OR credential_kind IS NOT NULL "
            "OR credential_id IS NOT NULL "
            "OR request_id IS NOT NULL "
            "OR source_ip IS NOT NULL"
        )
    )

    op.drop_index("ix_audit_logs_success_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_credential_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_principal", table_name="audit_logs")
    for column_name in (
        "source_ip",
        "request_id",
        "credential_id",
        "credential_kind",
        "actor_principal_id",
        "actor_principal_type",
    ):
        op.drop_column("audit_logs", column_name)

    op.drop_index(
        "ix_iam_group_role_assignments_role",
        table_name="iam_group_role_assignments",
    )
    op.drop_table("iam_group_role_assignments")
    op.drop_index("ix_iam_group_memberships_group", table_name="iam_group_memberships")
    op.drop_index("ix_iam_group_memberships_user", table_name="iam_group_memberships")
    op.drop_table("iam_group_memberships")
    op.drop_index("ix_iam_groups_source_name", table_name="iam_groups")
    op.drop_table("iam_groups")
    op.drop_index(
        "ix_iam_user_role_assignments_role",
        table_name="iam_user_role_assignments",
    )
    op.drop_index(
        "ix_iam_user_role_assignments_user",
        table_name="iam_user_role_assignments",
    )
    op.drop_table("iam_user_role_assignments")
    op.drop_index(
        "ix_iam_role_permissions_permission", table_name="iam_role_permissions"
    )
    op.drop_table("iam_role_permissions")
    op.drop_index("ix_iam_roles_system_name", table_name="iam_roles")
    op.drop_table("iam_roles")
    op.drop_table("iam_policy_state")
