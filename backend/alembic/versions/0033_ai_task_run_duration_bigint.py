"""store ai task run durations as bigint

Revision ID: 0033_ai_task_duration_bigint
Revises: 0032_webhook_delivery_idx
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0033_ai_task_duration_bigint"
down_revision = "0032_webhook_delivery_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ai_task_runs",
        "duration_ms",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="duration_ms::bigint",
    )


def downgrade() -> None:
    op.alter_column(
        "ai_task_runs",
        "duration_ms",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="LEAST(duration_ms, 2147483647)::integer",
    )
