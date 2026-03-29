"""add user auth token version

Revision ID: 0021_user_auth_token_version
Revises: 0020_ai_defaults
Create Date: 2026-03-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_user_auth_token_version"
down_revision = "0020_ai_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "auth_token_version")
