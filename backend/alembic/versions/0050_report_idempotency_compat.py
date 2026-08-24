"""repair report idempotency rolling-upgrade compatibility

Revision ID: 0050_report_idempotency_compat
Revises: 0049_report_generation_fence
Create Date: 2026-08-24
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op


revision = "0050_report_idempotency_compat"
down_revision = "0049_report_generation_fence"
branch_labels = None
depends_on = None


_LEGACY_CONSTRAINT = "uq_reports_owner_request_idempotency_key"
_HASH_CONSTRAINT = "uq_reports_owner_request_idempotency_key_hash"


def upgrade() -> None:
    connection = op.get_bind()
    columns = {
        column["name"] for column in sa.inspect(connection).get_columns("reports")
    }
    if "request_idempotency_key" not in columns:
        op.add_column(
            "reports",
            sa.Column(
                "request_idempotency_key",
                sa.String(length=255),
                nullable=True,
            ),
        )
    if "request_idempotency_key_hash" not in columns:
        op.add_column(
            "reports",
            sa.Column(
                "request_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            ),
        )
    _backfill_missing_hashes(connection)
    _repair_unique_constraints(connection)


def _backfill_missing_hashes(connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT id, request_idempotency_key FROM reports "
            "WHERE request_idempotency_key IS NOT NULL "
            "AND request_idempotency_key_hash IS NULL"
        )
    ).mappings()
    for row in rows:
        key_hash = hashlib.sha256(
            f"report:create\0{row['request_idempotency_key']}".encode("utf-8")
        ).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE reports SET request_idempotency_key_hash = :key_hash "
                "WHERE id = :report_id"
            ),
            {"key_hash": key_hash, "report_id": row["id"]},
        )


def _repair_unique_constraints(connection) -> None:
    constraints = {
        constraint["name"]: tuple(constraint.get("column_names") or [])
        for constraint in sa.inspect(connection).get_unique_constraints("reports")
        if constraint.get("name")
    }
    expected_legacy = ("owner_user_id", "request_idempotency_key")
    if constraints.get(_LEGACY_CONSTRAINT) not in {None, expected_legacy}:
        op.drop_constraint(_LEGACY_CONSTRAINT, "reports", type_="unique")
        constraints.pop(_LEGACY_CONSTRAINT, None)
    if _LEGACY_CONSTRAINT not in constraints:
        op.create_unique_constraint(
            _LEGACY_CONSTRAINT,
            "reports",
            list(expected_legacy),
        )

    expected_hash = ("owner_user_id", "request_idempotency_key_hash")
    if constraints.get(_HASH_CONSTRAINT) not in {None, expected_hash}:
        op.drop_constraint(_HASH_CONSTRAINT, "reports", type_="unique")
        constraints.pop(_HASH_CONSTRAINT, None)
    if _HASH_CONSTRAINT not in constraints:
        op.create_unique_constraint(
            _HASH_CONSTRAINT,
            "reports",
            list(expected_hash),
        )


def downgrade() -> None:
    # This migration repairs both the original rename-based draft and the
    # expand/backfill schema. Keeping both nullable columns is necessary for
    # old and new workers to remain compatible during a downgrade rollout.
    pass
