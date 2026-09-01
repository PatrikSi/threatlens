"""add bounded system health history

Revision ID: 0083_system_health_history
Revises: 0082_audit_identity_snapshots
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0083_system_health_history"
down_revision = "0082_audit_identity_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_health_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overall_status", sa.String(length=16), nullable=False),
        sa.Column(
            "component_statuses_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("worker_status", sa.String(length=16), nullable=False),
        sa.Column("worker_reason", sa.String(length=64), nullable=False),
        sa.Column(
            "responding_worker_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "observed_worker_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "worker_inventory_truncated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("total_capacity", sa.Integer(), nullable=True),
        sa.Column("active_count", sa.Integer(), nullable=True),
        sa.Column("reserved_count", sa.Integer(), nullable=True),
        sa.Column("scheduled_count", sa.Integer(), nullable=True),
        sa.Column(
            "missing_queues_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "stale_execution_queues_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "backlog_pending_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "backlog_stale_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "critical_issue_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "warning_issue_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "issue_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "overall_status IN ('healthy', 'degraded', 'critical', 'unavailable', 'unknown')",
            name="ck_system_health_samples_overall_status",
        ),
        sa.CheckConstraint(
            "worker_status IN ('healthy', 'degraded', 'critical', 'unavailable', 'unknown')",
            name="ck_system_health_samples_worker_status",
        ),
        sa.CheckConstraint(
            "worker_reason IN ('healthy', 'no_replies', 'probe_failed', "
            "'queue_inventory_unavailable', 'partial_inventory', "
            "'missing_consumers', 'canary_dispatch_unavailable', "
            "'execution_evidence_missing', "
            "'execution_stalled', 'saturated')",
            name="ck_system_health_samples_worker_reason",
        ),
        sa.CheckConstraint(
            "responding_worker_count >= 0 "
            "AND observed_worker_count >= responding_worker_count "
            "AND (total_capacity IS NULL OR total_capacity >= 0) "
            "AND (active_count IS NULL OR active_count >= 0) "
            "AND (reserved_count IS NULL OR reserved_count >= 0) "
            "AND (scheduled_count IS NULL OR scheduled_count >= 0) "
            "AND backlog_pending_count >= 0 AND backlog_stale_count >= 0 "
            "AND critical_issue_count >= 0 AND warning_issue_count >= 0",
            name="ck_system_health_samples_nonnegative_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_health_samples_sampled_at",
        "system_health_samples",
        ["sampled_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_system_health_samples_sampled_at",
        table_name="system_health_samples",
    )
    op.drop_table("system_health_samples")
