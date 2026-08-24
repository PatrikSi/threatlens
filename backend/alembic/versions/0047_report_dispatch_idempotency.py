"""add report dispatch and retry idempotency constraints

Revision ID: 0047_report_dispatch
Revises: 0046_report_resilience
Create Date: 2026-08-24
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op


revision = "0047_report_dispatch"
down_revision = "0046_report_resilience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "request_idempotency_key_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )
    _hash_legacy_report_keys()
    op.create_unique_constraint(
        "uq_reports_owner_request_idempotency_key_hash",
        "reports",
        ["owner_user_id", "request_idempotency_key_hash"],
    )
    op.add_column(
        "ai_task_runs",
        sa.Column("request_idempotency_key_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ai_task_runs",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_ai_task_runs_actor_request_idempotency_key",
        "ai_task_runs",
        ["actor_user_id", "request_idempotency_key_hash"],
        unique=True,
    )

    op.execute(
        """
        WITH duplicate_runs AS (
            SELECT id
            FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY report_id
                        ORDER BY created_at DESC, id DESC
                    ) AS position
                FROM ai_task_runs
                WHERE report_id IS NOT NULL
                  AND task_type = 'report'
                  AND status IN ('queued', 'running')
                  AND finished_at IS NULL
            ) ranked
            WHERE position > 1
        )
        UPDATE ai_task_runs
        SET status = 'error',
            reason = 'superseded_duplicate',
            error = 'Duplicate active report run superseded during resilience upgrade.',
            finished_at = now(),
            updated_at = now()
        WHERE id IN (SELECT id FROM duplicate_runs)
        """
    )
    op.create_index(
        "uq_ai_task_runs_active_report",
        "ai_task_runs",
        ["report_id"],
        unique=True,
        postgresql_where=sa.text(
            "report_id IS NOT NULL AND task_type = 'report' "
            "AND status IN ('queued', 'running') AND finished_at IS NULL"
        ),
    )


def _hash_legacy_report_keys() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, request_idempotency_key FROM reports "
            "WHERE request_idempotency_key IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        stored_key = row["request_idempotency_key"]
        key_hash = hashlib.sha256(
            f"report:create\0{stored_key}".encode("utf-8")
        ).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE reports SET request_idempotency_key_hash = :key_hash "
                "WHERE id = :report_id"
            ),
            {"key_hash": key_hash, "report_id": row["id"]},
        )


def downgrade() -> None:
    op.drop_index("uq_ai_task_runs_active_report", table_name="ai_task_runs")
    op.drop_index(
        "uq_ai_task_runs_actor_request_idempotency_key",
        table_name="ai_task_runs",
    )
    op.drop_column("ai_task_runs", "request_fingerprint")
    op.drop_column("ai_task_runs", "request_idempotency_key_hash")
    op.drop_constraint(
        "uq_reports_owner_request_idempotency_key_hash",
        "reports",
        type_="unique",
    )
    op.drop_column("reports", "request_idempotency_key_hash")
