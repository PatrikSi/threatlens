"""track how user accounts were provisioned

Revision ID: 0042_user_source
Revises: 0041_oidc_email_policy
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0042_user_source"
down_revision = "0041_oidc_email_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "provisioning_source",
            sa.String(length=32),
            nullable=False,
            server_default="local",
        ),
    )
    op.create_check_constraint(
        "ck_users_provisioning_source",
        "users",
        "provisioning_source IN ('local', 'oidc')",
    )
    op.execute(
        sa.text(
            """
            UPDATE users
            SET provisioning_source = 'oidc'
            WHERE EXISTS (
                SELECT 1
                FROM external_identities
                WHERE external_identities.user_id = users.id
            )
            AND (
                users.password_login_enabled = false
                OR EXISTS (
                    SELECT 1
                    FROM audit_logs
                    WHERE audit_logs.action = 'oidc.user.provision'
                    AND audit_logs.resource_id = CAST(users.id AS VARCHAR)
                )
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_provisioning_source", "users", type_="check")
    op.drop_column("users", "provisioning_source")
