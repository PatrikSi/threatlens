"""add durable reporting resilience state

Revision ID: 0046_report_resilience
Revises: 0045_reporting_context
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0046_report_resilience"
down_revision = "0045_reporting_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("request_idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("reports", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("reports", sa.Column("generation_lease_token", sa.String(length=64), nullable=True))
    op.add_column("reports", sa.Column("generation_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f("ix_reports_generation_lease_expires_at"),
        "reports",
        ["generation_lease_expires_at"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_reports_owner_request_idempotency_key",
        "reports",
        ["owner_user_id", "request_idempotency_key"],
    )

    op.add_column(
        "ai_task_runs",
        sa.Column("dispatch_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("ai_task_runs", sa.Column("dispatch_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_task_runs", sa.Column("dispatch_error", sa.Text(), nullable=True))
    op.create_index(
        op.f("ix_ai_task_runs_dispatch_next_attempt_at"),
        "ai_task_runs",
        ["dispatch_next_attempt_at"],
        unique=False,
    )

    op.add_column(
        "report_schedules",
        sa.Column("failure_state", sa.String(length=16), nullable=False, server_default="healthy"),
    )
    op.add_column(
        "report_schedules",
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "report_schedules",
        sa.Column("consecutive_failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("report_schedules", sa.Column("last_error_code", sa.String(length=64), nullable=True))
    op.add_column("report_schedules", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("report_schedules", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("report_schedules", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f("ix_report_schedules_failure_state"),
        "report_schedules",
        ["failure_state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_report_schedules_retry_at"),
        "report_schedules",
        ["retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_report_schedules_retry_at"), table_name="report_schedules")
    op.drop_index(op.f("ix_report_schedules_failure_state"), table_name="report_schedules")
    op.drop_column("report_schedules", "retry_at")
    op.drop_column("report_schedules", "last_error_at")
    op.drop_column("report_schedules", "last_error")
    op.drop_column("report_schedules", "last_error_code")
    op.drop_column("report_schedules", "consecutive_failure_count")
    op.drop_column("report_schedules", "failure_count")
    op.drop_column("report_schedules", "failure_state")

    op.drop_index(op.f("ix_ai_task_runs_dispatch_next_attempt_at"), table_name="ai_task_runs")
    op.drop_column("ai_task_runs", "dispatch_error")
    op.drop_column("ai_task_runs", "dispatch_next_attempt_at")
    op.drop_column("ai_task_runs", "dispatch_attempt_count")

    op.drop_constraint("uq_reports_owner_request_idempotency_key", "reports", type_="unique")
    op.drop_index(op.f("ix_reports_generation_lease_expires_at"), table_name="reports")
    op.drop_column("reports", "generation_lease_expires_at")
    op.drop_column("reports", "generation_lease_token")
    op.drop_column("reports", "request_fingerprint")
    op.drop_column("reports", "request_idempotency_key")
