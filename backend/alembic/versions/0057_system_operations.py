"""add system operation run history

Revision ID: 0057_system_operations
Revises: 0056_report_task_lineage
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0057_system_operations"
down_revision = "0056_report_task_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_operation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("initiated_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="offline"),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "operation_type IN ('backup', 'verify', 'restore_drill', 'restore', 'diagnostics')",
            name="ck_system_operation_runs_operation_type",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_system_operation_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_operation_runs_started_at",
        "system_operation_runs",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_operation_runs_type_started",
        "system_operation_runs",
        ["operation_type", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_operation_runs_status_started",
        "system_operation_runs",
        ["status", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_system_operation_runs_status_started",
        table_name="system_operation_runs",
    )
    op.drop_index(
        "ix_system_operation_runs_type_started",
        table_name="system_operation_runs",
    )
    op.drop_index(
        "ix_system_operation_runs_started_at",
        table_name="system_operation_runs",
    )
    op.drop_table("system_operation_runs")
