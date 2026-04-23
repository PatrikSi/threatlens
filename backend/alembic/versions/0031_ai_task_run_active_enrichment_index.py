"""add active ai item enrichment lookup index

Revision ID: 0031_ai_task_run_active_idx
Revises: 0030_schedule_search_indexes
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0031_ai_task_run_active_idx"
down_revision = "0030_schedule_search_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ai_task_runs_item_task_status_active",
        "ai_task_runs",
        ["item_id", "task_type", "status"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_ai_task_runs_item_task_status_active", table_name="ai_task_runs")
