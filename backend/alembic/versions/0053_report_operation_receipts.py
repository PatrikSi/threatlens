"""add reporting operation idempotency receipts

Revision ID: 0053_report_operation_receipts
Revises: 0052_legacy_worker_guard
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0053_report_operation_receipts"
down_revision = "0052_legacy_worker_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("report_operation_receipts"):
        return
    op.create_table(
        "report_operation_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "key_hash",
            name="uq_report_operation_receipts_actor_key",
        ),
    )
    op.create_index(
        op.f("ix_report_operation_receipts_actor_user_id"),
        "report_operation_receipts",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_operation_receipts_resource",
        "report_operation_receipts",
        ["resource_type", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    # The previous application ignores this additive table. Preserve receipts so
    # a rollback/re-upgrade cannot make accepted keys replay as new operations.
    pass
