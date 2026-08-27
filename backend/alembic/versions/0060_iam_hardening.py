"""add revocable browser sessions and local TOTP MFA

Revision ID: 0060_iam_hardening
Revises: 0059_alerting_v2
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0060_iam_hardening"
down_revision = "0059_alerting_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oidc_providers",
        sa.Column(
            "config_revision", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.create_check_constraint(
        "ck_oidc_providers_config_revision",
        "oidc_providers",
        "config_revision >= 1",
    )
    op.add_column(
        "api_tokens",
        sa.Column("parent_token_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_tokens_parent_token_id_api_tokens",
        "api_tokens",
        "api_tokens",
        ["parent_token_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_api_tokens_parent_token_id", "api_tokens", ["parent_token_id"]
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "auth_token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("auth_method", sa.String(length=16), nullable=False),
        sa.Column("mfa_method", sa.String(length=32), nullable=True),
        sa.Column("identity_acr", sa.String(length=255), nullable=True),
        sa.Column("identity_amr_json", sa.JSON(), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "identity_authenticated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
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
            "auth_method IN ('local', 'oidc')", name="ck_auth_sessions_auth_method"
        ),
        sa.CheckConstraint(
            "mfa_method IS NULL OR mfa_method IN ('totp', 'recovery_code', 'external')",
            name="ck_auth_sessions_mfa_method",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    for name, columns in (
        ("ix_auth_sessions_user_id", ["user_id"]),
        ("ix_auth_sessions_idle_expires_at", ["idle_expires_at"]),
        ("ix_auth_sessions_absolute_expires_at", ["absolute_expires_at"]),
        ("ix_auth_sessions_revoked_at", ["revoked_at"]),
        (
            "ix_auth_sessions_user_active",
            ["user_id", "auth_token_version", "revoked_at", "absolute_expires_at"],
        ),
        ("ix_auth_sessions_expiry", ["idle_expires_at", "absolute_expires_at"]),
    ):
        op.create_index(name, "auth_sessions", columns)

    op.create_table(
        "user_totp_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("enrollment_session_id", sa.Uuid(), nullable=True),
        sa.Column("enrollment_auth_token_version", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("last_accepted_step", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recovery_codes_generated_at", sa.DateTime(timezone=True), nullable=True
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
            "status IN ('pending', 'active')",
            name="ck_user_totp_credentials_status",
        ),
        sa.CheckConstraint(
            "status != 'pending' OR (enrollment_session_id IS NOT NULL AND enrollment_auth_token_version IS NOT NULL)",
            name="ck_user_totp_credentials_pending_binding",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_session_id"], ["auth_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_user_recovery_codes_ordinal"),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["user_totp_credentials.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "credential_id",
            "ordinal",
            name="uq_user_recovery_codes_credential_ordinal",
        ),
        sa.UniqueConstraint(
            "credential_id",
            "code_hash",
            name="uq_user_recovery_codes_credential_hash",
        ),
    )
    op.create_index(
        "ix_user_recovery_codes_credential_id", "user_recovery_codes", ["credential_id"]
    )
    op.create_index(
        "ix_user_recovery_codes_credential_unused",
        "user_recovery_codes",
        ["credential_id", "used_at"],
    )

    op.create_table(
        "mfa_login_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "auth_token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
        sa.Column(
            "password_authenticated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_mfa_login_challenges_attempt_count"
        ),
        sa.CheckConstraint(
            "max_attempts >= 1", name="ck_mfa_login_challenges_max_attempts"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    for name, columns in (
        ("ix_mfa_login_challenges_user_id", ["user_id"]),
        ("ix_mfa_login_challenges_expires_at", ["expires_at"]),
        ("ix_mfa_login_challenges_expiry", ["expires_at", "consumed_at"]),
        ("ix_mfa_login_challenges_user_created", ["user_id", "created_at"]),
    ):
        op.create_index(name, "mfa_login_challenges", columns)


def downgrade() -> None:
    bind = op.get_bind()
    active_mfa_count = bind.scalar(
        sa.text("SELECT count(*) FROM user_totp_credentials WHERE status = 'active'")
    )
    if int(active_mfa_count or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade IAM hardening while local MFA is active. Disable local MFA for every account "
            "and complete a verified backup before retrying the downgrade."
        )
    for name in (
        "ix_mfa_login_challenges_user_created",
        "ix_mfa_login_challenges_expiry",
        "ix_mfa_login_challenges_expires_at",
        "ix_mfa_login_challenges_user_id",
    ):
        op.drop_index(name, table_name="mfa_login_challenges")
    op.drop_table("mfa_login_challenges")
    op.drop_index(
        "ix_user_recovery_codes_credential_unused",
        table_name="user_recovery_codes",
    )
    op.drop_index(
        "ix_user_recovery_codes_credential_id", table_name="user_recovery_codes"
    )
    op.drop_table("user_recovery_codes")
    op.drop_table("user_totp_credentials")
    for name in (
        "ix_auth_sessions_expiry",
        "ix_auth_sessions_user_active",
        "ix_auth_sessions_revoked_at",
        "ix_auth_sessions_absolute_expires_at",
        "ix_auth_sessions_idle_expires_at",
        "ix_auth_sessions_user_id",
    ):
        op.drop_index(name, table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_api_tokens_parent_token_id", table_name="api_tokens")
    op.drop_constraint(
        "fk_api_tokens_parent_token_id_api_tokens",
        "api_tokens",
        type_="foreignkey",
    )
    op.drop_column("api_tokens", "parent_token_id")
    op.drop_constraint(
        "ck_oidc_providers_config_revision",
        "oidc_providers",
        type_="check",
    )
    op.drop_column("oidc_providers", "config_revision")
