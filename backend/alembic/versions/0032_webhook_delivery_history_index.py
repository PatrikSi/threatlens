"""add webhook delivery history lookup index

Revision ID: 0032_webhook_delivery_idx
Revises: 0031_ai_task_run_active_idx
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0032_webhook_delivery_idx"
down_revision = "0031_ai_task_run_active_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_notification_webhook_deliveries_webhook_attempted_id",
        "notification_webhook_deliveries",
        ["webhook_id", sa.text("attempted_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_webhook_deliveries_webhook_attempted_id",
        table_name="notification_webhook_deliveries",
    )
