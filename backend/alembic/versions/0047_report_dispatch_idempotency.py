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
    _hash_legacy_report_keys()
    op.alter_column(
        "reports",
        "request_idempotency_key",
        new_column_name="request_idempotency_key_hash",
        existing_type=sa.String(length=255),
    )
    op.alter_column(
        "reports",
        "request_idempotency_key_hash",
        type_=sa.String(length=64),
        existing_type=sa.String(length=255),
        existing_nullable=True,
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
        if len(stored_key) == 64 and all(
            character in "0123456789abcdef" for character in stored_key
        ):
            continue
        key_hash = hashlib.sha256(
            f"report:create\0{stored_key}".encode("utf-8")
        ).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE reports SET request_idempotency_key = :key_hash "
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
    op.alter_column(
        "reports",
        "request_idempotency_key_hash",
        type_=sa.String(length=255),
        existing_type=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "reports",
        "request_idempotency_key_hash",
        new_column_name="request_idempotency_key",
        existing_type=sa.String(length=255),
    )
