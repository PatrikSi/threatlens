"""add ai daily brief schedule fields

Revision ID: 0019_ai_daily_schedule
Revises: 0018_ai_retry_settings
Create Date: 2026-03-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_ai_daily_schedule"
down_revision = "0018_ai_retry_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_settings", sa.Column("daily_brief_schedule_hour_utc", sa.Integer(), nullable=False, server_default="9"))
    op.add_column("ai_settings", sa.Column("daily_brief_schedule_minute_utc", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("ai_settings", "daily_brief_schedule_minute_utc")
    op.drop_column("ai_settings", "daily_brief_schedule_hour_utc")
