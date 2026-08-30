"""add bounded service-account principals and credentials

Revision ID: 0064_service_accounts
Revises: 0063_workspace_policy
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0064_service_accounts"
down_revision = "0063_workspace_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("disabled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("revision >= 1", name="ck_service_accounts_revision"),
        sa.CheckConstraint(
            "(is_active AND disabled_at IS NULL) OR "
            "(NOT is_active AND disabled_at IS NOT NULL)",
            name="ck_service_accounts_active_state",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_service_accounts_key"),
    )
    op.create_index(
        "ix_service_accounts_active_name",
        "service_accounts",
        ["is_active", "name"],
    )

    op.create_table(
        "service_account_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_account_id", sa.Uuid(), nullable=False),
        sa.Column("rotated_from_credential_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("operation_kind", sa.String(length=16), nullable=True),
        sa.Column("operation_key_hash", sa.String(length=64), nullable=True),
        sa.Column("operation_request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=True),
        sa.Column("last_used_user_agent", sa.String(length=512), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "token_prefix LIKE 'tlsa\\_%' ESCAPE '\\'",
            name="ck_service_account_credentials_prefix",
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64",
            name="ck_service_account_credentials_hash_length",
        ),
        sa.CheckConstraint(
            "operation_key_hash IS NULL OR length(operation_key_hash) = 64",
            name="ck_service_account_credentials_operation_key_hash_length",
        ),
        sa.CheckConstraint(
            "operation_request_hash IS NULL OR length(operation_request_hash) = 64",
            name="ck_service_account_credentials_operation_request_hash_length",
        ),
        sa.CheckConstraint(
            "(operation_kind IS NULL AND operation_key_hash IS NULL AND "
            "operation_request_hash IS NULL) OR "
            "(operation_kind IN ('issue', 'rotate') AND "
            "operation_key_hash IS NOT NULL AND operation_request_hash IS NOT NULL)",
            name="ck_service_account_credentials_operation_receipt",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) > 0 "
            "AND NOT jsonb_path_exists(scopes, "
            "'$[*] ? (@.type() != \"string\")')",
            name="ck_service_account_credentials_scopes_array",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_service_account_credentials_expiry",
        ),
        sa.CheckConstraint(
            "original_expires_at IS NULL OR original_expires_at >= expires_at",
            name="ck_service_account_credentials_original_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"], ["service_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_credential_id"],
            ["service_account_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_prefix", name="uq_service_account_credentials_prefix"
        ),
        sa.UniqueConstraint("token_hash", name="uq_service_account_credentials_hash"),
        sa.UniqueConstraint(
            "service_account_id",
            "operation_key_hash",
            name="uq_service_account_credentials_operation_key",
        ),
    )
    op.create_index(
        "ix_service_account_credentials_account_created",
        "service_account_credentials",
        ["service_account_id", "created_at"],
    )
    op.create_index(
        "ix_service_account_credentials_active_expiry",
        "service_account_credentials",
        ["service_account_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "service_account_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_account_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"], ["service_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_account_id",
            "role_id",
            name="uq_service_account_role_assignments",
        ),
    )
    op.create_index(
        "ix_service_account_role_assignments_role",
        "service_account_role_assignments",
        ["role_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: int(
            bind.scalar(sa.text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            or 0
        )
        for table in (
            "service_accounts",
            "service_account_credentials",
            "service_account_role_assignments",
        )
    }
    populated = [f"{table}={count}" for table, count in counts.items() if count]
    if populated:
        raise RuntimeError(
            "Cannot downgrade service-account persistence while non-human identity "
            f"state would be lost ({', '.join(populated)}). Export each account, "
            "disable it, then use the service-account DELETE API with its current "
            "revision. Verify that the account inventory is empty and retry the "
            "downgrade."
        )

    op.drop_index(
        "ix_service_account_role_assignments_role",
        table_name="service_account_role_assignments",
    )
    op.drop_table("service_account_role_assignments")
    op.drop_index(
        "ix_service_account_credentials_active_expiry",
        table_name="service_account_credentials",
    )
    op.drop_index(
        "ix_service_account_credentials_account_created",
        table_name="service_account_credentials",
    )
    op.drop_table("service_account_credentials")
    op.drop_index("ix_service_accounts_active_name", table_name="service_accounts")
    op.drop_table("service_accounts")
