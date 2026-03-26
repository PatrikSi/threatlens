"""add editable ai system prompts

Revision ID: 0016_ai_system_prompts
Revises: 0015_ai_daily_history
Create Date: 2026-03-26 23:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_ai_system_prompts"
down_revision = "0015_ai_daily_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_settings", sa.Column("item_enrichment_system_prompt", sa.Text(), nullable=True))
    op.add_column("ai_settings", sa.Column("daily_brief_system_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_settings", "daily_brief_system_prompt")
    op.drop_column("ai_settings", "item_enrichment_system_prompt")
