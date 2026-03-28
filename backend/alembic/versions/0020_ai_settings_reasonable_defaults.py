"""set reasonable ai settings defaults

Revision ID: 0020_ai_defaults
Revises: 0019_ai_daily_schedule
Create Date: 2026-03-28 00:00:00.000000
"""

from alembic import op


revision = "0020_ai_defaults"
down_revision = "0019_ai_daily_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_settings ALTER COLUMN max_completion_tokens SET DEFAULT 5000")
    op.execute("ALTER TABLE ai_settings ALTER COLUMN request_timeout_seconds SET DEFAULT 300")
    op.execute("ALTER TABLE ai_settings ALTER COLUMN request_max_retries SET DEFAULT 3")

    # Update only rows still carrying the previous untouched baseline defaults.
    op.execute("UPDATE ai_settings SET max_completion_tokens = 5000 WHERE max_completion_tokens = 700")
    op.execute("UPDATE ai_settings SET request_timeout_seconds = 300 WHERE request_timeout_seconds = 60")
    op.execute("UPDATE ai_settings SET request_max_retries = 3 WHERE request_max_retries = 0")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_settings ALTER COLUMN request_max_retries SET DEFAULT 0")
    op.execute("ALTER TABLE ai_settings ALTER COLUMN request_timeout_seconds SET DEFAULT 60")
    op.execute("ALTER TABLE ai_settings ALTER COLUMN max_completion_tokens SET DEFAULT 700")
