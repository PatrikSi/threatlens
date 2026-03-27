"""add ai retry settings

Revision ID: 0018_ai_retry_settings
Revises: 0017_ai_task_history
Create Date: 2026-03-27 17:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_ai_retry_settings"
down_revision = "0017_ai_task_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column("request_max_retries", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ai_settings", "request_max_retries")
