"""add OIDC identity provider and external identities

Revision ID: 0040_oidc_identity
Revises: 0039_guid_dedupe_repair
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0040_oidc_identity"
down_revision = "0039_guid_dedupe_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_login_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("system_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("issuer_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "client_auth_method",
            sa.String(length=32),
            nullable=False,
            server_default="client_secret_basic",
        ),
        sa.Column("public_base_url", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[\"openid\", \"profile\", \"email\"]'::json"),
        ),
        sa.Column("role_claim", sa.String(length=255), nullable=False, server_default="groups"),
        sa.Column("role_mappings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("default_role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("jit_provisioning_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("auto_approve_users", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sync_roles_on_login", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "client_auth_method IN ('client_secret_basic', 'client_secret_post', 'none')",
            name="ck_oidc_providers_client_auth_method",
        ),
        sa.CheckConstraint(
            "default_role IN ('admin', 'analyst', 'viewer')",
            name="ck_oidc_providers_default_role",
        ),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_key", name="uq_oidc_providers_system_key"),
    )
    op.create_index("ix_oidc_providers_updated_by_user_id", "oidc_providers", ["updated_by_user_id"], unique=False)

    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("email_at_link", sa.String(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["provider_id"], ["oidc_providers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
        sa.UniqueConstraint("provider_id", "user_id", name="uq_external_identities_provider_user"),
    )
    op.create_index("ix_external_identities_provider_id", "external_identities", ["provider_id"], unique=False)
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_external_identities_user_id", table_name="external_identities")
    op.drop_index("ix_external_identities_provider_id", table_name="external_identities")
    op.drop_table("external_identities")
    op.drop_index("ix_oidc_providers_updated_by_user_id", table_name="oidc_providers")
    op.drop_table("oidc_providers")
    op.drop_column("users", "password_login_enabled")
