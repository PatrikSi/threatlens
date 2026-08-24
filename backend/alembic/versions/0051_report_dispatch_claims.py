"""add durable report dispatch publication claims

Revision ID: 0051_report_dispatch_claims
Revises: 0050_report_idempotency_compat
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0051_report_dispatch_claims"
down_revision = "0050_report_idempotency_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_task_runs",
        sa.Column("dispatch_claim_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ai_task_runs",
        sa.Column(
            "dispatch_claim_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_task_runs",
        sa.Column("dispatch_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_ai_task_runs_dispatch_claim_expires_at"),
        "ai_task_runs",
        ["dispatch_claim_expires_at"],
        unique=False,
    )
    op.execute(
        """
        UPDATE ai_task_runs
        SET dispatch_published_at = COALESCE(updated_at, queued_at, now()),
            dispatch_attempt_count = 0,
            dispatch_next_attempt_at = now() + interval '5 minutes'
        WHERE task_type = 'report'
          AND status = 'queued'
          AND finished_at IS NULL
          AND celery_task_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ai_task_runs_dispatch_claim_expires_at"),
        table_name="ai_task_runs",
    )
    op.drop_column("ai_task_runs", "dispatch_published_at")
    op.drop_column("ai_task_runs", "dispatch_claim_expires_at")
    op.drop_column("ai_task_runs", "dispatch_claim_token")
