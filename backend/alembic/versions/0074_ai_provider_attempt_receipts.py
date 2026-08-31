"""add durable AI provider attempt receipts

Revision ID: 0074_ai_provider_receipts
Revises: 0073_alert_metric_data_policy
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0074_ai_provider_receipts"
down_revision = "0073_alert_metric_data_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_attempt_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "task_run_id_snapshot",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("feature_type", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("requested_max_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "reservation_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "pre_io_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_pre_io_failure_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("iam_revision", sa.Integer(), nullable=False),
        sa.Column("data_policy_revision", sa.Integer(), nullable=False),
        sa.Column("data_policy_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="reserved",
        ),
        sa.Column(
            "io_outcome",
            sa.String(length=24),
            nullable=False,
            server_default="reserved",
        ),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("next_max_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_action", sa.String(length=32), nullable=True),
        sa.Column("reconciled_from_state", sa.String(length=16), nullable=True),
        sa.Column(
            "reconciled_from_io_outcome", sa.String(length=24), nullable=True
        ),
        sa.Column(
            "reconciled_by_user_id_snapshot",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1 AND max_attempts >= 1 "
            "AND attempt_number <= max_attempts",
            name="ck_ai_provider_attempt_receipts_attempt_bounds",
        ),
        sa.CheckConstraint(
            "requested_max_tokens >= 1 AND "
            "(next_max_tokens IS NULL OR next_max_tokens >= 1)",
            name="ck_ai_provider_attempt_receipts_token_bounds",
        ),
        sa.CheckConstraint(
            "iam_revision >= 1",
            name="ck_ai_provider_attempt_receipts_iam_revision",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_ai_provider_attempt_receipts_revision",
        ),
        sa.CheckConstraint(
            "reservation_generation >= 1",
            name="ck_ai_provider_attempt_receipts_reservation_generation",
        ),
        sa.CheckConstraint(
            "pre_io_failure_count >= 0 AND "
            "((pre_io_failure_count = 0 AND last_pre_io_failure_at IS NULL) OR "
            "(pre_io_failure_count >= 1 AND last_pre_io_failure_at IS NOT NULL)) "
            "AND ((state = 'voided' "
            "AND reservation_generation = pre_io_failure_count) OR "
            "(state <> 'voided' "
            "AND reservation_generation = pre_io_failure_count + 1))",
            name="ck_ai_provider_attempt_receipts_pre_io_failures",
        ),
        sa.CheckConstraint(
            "data_policy_revision >= 1",
            name="ck_ai_provider_attempt_receipts_policy_revision",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_ai_provider_attempt_receipts_fingerprint",
        ),
        sa.CheckConstraint(
            "data_policy_mode IN ('disabled', 'audit', 'enforced', 'bypass')",
            name="ck_ai_provider_attempt_receipts_policy_mode",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'voided', 'failed', 'succeeded', 'ambiguous')",
            name="ck_ai_provider_attempt_receipts_state",
        ),
        sa.CheckConstraint(
            "io_outcome IN "
            "('reserved', 'not_sent', 'response_received', 'ambiguous')",
            name="ck_ai_provider_attempt_receipts_io_outcome",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND io_outcome = 'reserved' "
            "AND retryable IS NULL AND settled_at IS NULL "
            "AND next_max_tokens IS NULL) OR "
            "(state = 'voided' AND io_outcome = 'not_sent' "
            "AND retryable IS TRUE AND settled_at IS NOT NULL "
            "AND next_max_tokens IS NULL) OR "
            "(state = 'failed' "
            "AND io_outcome IN ('not_sent', 'response_received') "
            "AND retryable IS NOT NULL AND settled_at IS NOT NULL "
            "AND ((retryable AND next_max_tokens IS NOT NULL) "
            "OR (NOT retryable AND next_max_tokens IS NULL))) OR "
            "(state = 'succeeded' AND io_outcome = 'response_received' "
            "AND retryable IS FALSE AND settled_at IS NOT NULL "
            "AND next_max_tokens IS NULL) OR "
            "(state = 'ambiguous' AND io_outcome = 'ambiguous' "
            "AND retryable IS FALSE AND settled_at IS NOT NULL "
            "AND next_max_tokens IS NULL)",
            name="ck_ai_provider_attempt_receipts_lifecycle",
        ),
        sa.CheckConstraint(
            "(reconciliation_action IS NULL "
            "AND reconciled_from_state IS NULL "
            "AND reconciled_from_io_outcome IS NULL "
            "AND reconciled_by_user_id_snapshot IS NULL "
            "AND reconciled_at IS NULL) OR "
            "(reconciliation_action IN "
            "('confirmed_not_sent', 'acknowledged_may_have_sent') "
            "AND reconciled_from_state IN ('reserved', 'ambiguous') "
            "AND reconciled_from_io_outcome IN ('reserved', 'ambiguous') "
            "AND ((reconciled_from_state = 'reserved' "
            "AND reconciled_from_io_outcome = 'reserved') OR "
            "(reconciled_from_state = 'ambiguous' "
            "AND reconciled_from_io_outcome = 'ambiguous')) "
            "AND reconciled_by_user_id_snapshot IS NOT NULL "
            "AND reconciled_at IS NOT NULL "
            "AND settled_at IS NOT NULL AND settled_at <= reconciled_at "
            "AND ((reconciliation_action = 'confirmed_not_sent' "
            "AND state = 'failed' AND io_outcome = 'not_sent' "
            "AND ((retryable IS TRUE AND next_max_tokens IS NOT NULL "
            "AND attempt_number < max_attempts) OR "
            "(retryable IS FALSE AND next_max_tokens IS NULL "
            "AND attempt_number = max_attempts))) OR "
            "(reconciliation_action = 'acknowledged_may_have_sent' "
            "AND state = 'ambiguous' AND io_outcome = 'ambiguous' "
            "AND retryable IS FALSE AND next_max_tokens IS NULL)))",
            name="ck_ai_provider_attempt_receipts_reconciliation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "attempt_number",
            name="uq_ai_provider_attempt_receipts_operation_attempt",
        ),
    )
    op.create_index(
        "ix_ai_provider_attempt_receipts_task_run_snapshot",
        "ai_provider_attempt_receipts",
        ["task_run_id_snapshot"],
    )
    op.create_index(
        "ix_ai_provider_attempt_receipts_resource",
        "ai_provider_attempt_receipts",
        ["resource_type", "resource_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    state = (
        bind.execute(
            sa.text(
                "SELECT mode, coverage_version FROM data_policy_state "
                "WHERE id = 1 FOR UPDATE"
            )
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        raise RuntimeError(
            "Cannot downgrade AI provider attempt receipts because data policy "
            "state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            "Cannot downgrade AI provider attempt receipts while data policy "
            "audit or enforcement is active. Disable data policy first."
        )

    bind.execute(sa.text("LOCK TABLE ai_provider_attempt_receipts IN ACCESS EXCLUSIVE MODE"))
    receipt_count = int(
        bind.scalar(sa.text("SELECT count(*) FROM ai_provider_attempt_receipts")) or 0
    )
    if receipt_count:
        raise RuntimeError(
            "Cannot downgrade AI provider attempt receipts while durable provider "
            "history exists. Reconcile and explicitly purge the history first."
        )
    op.drop_index(
        "ix_ai_provider_attempt_receipts_resource",
        table_name="ai_provider_attempt_receipts",
    )
    op.drop_index(
        "ix_ai_provider_attempt_receipts_task_run_snapshot",
        table_name="ai_provider_attempt_receipts",
    )
    op.drop_table("ai_provider_attempt_receipts")
