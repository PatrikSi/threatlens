"""add OIDC email verification policy

Revision ID: 0041_oidc_email_policy
Revises: 0040_oidc_identity
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0041_oidc_email_policy"
down_revision = "0040_oidc_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oidc_providers",
        sa.Column(
            "require_verified_email",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("oidc_providers", "require_verified_email")
