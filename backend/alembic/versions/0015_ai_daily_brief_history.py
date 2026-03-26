"""add ai daily brief history retention

Revision ID: 0015_ai_daily_history
Revises: 0014_ai_integration
Create Date: 2026-03-26 22:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_ai_daily_history"
down_revision = "0014_ai_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column("daily_brief_history_limit", sa.Integer(), nullable=False, server_default="7"),
    )


def downgrade() -> None:
    op.drop_column("ai_settings", "daily_brief_history_limit")
