"""add transactional report generation fence

Revision ID: 0049_report_generation_fence
Revises: 0048_feed_fetch_fence
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0049_report_generation_fence"
down_revision = "0048_feed_fetch_fence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_generation_leases",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column(
            "generation_fence",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("report_id"),
    )
    op.create_index(
        op.f("ix_report_generation_leases_lease_expires_at"),
        "report_generation_leases",
        ["lease_expires_at"],
        unique=False,
    )

    # Seed active legacy leases so new workers respect work started before a
    # rolling upgrade reached every report worker.
    op.execute(
        """
        INSERT INTO report_generation_leases (
            report_id,
            generation_fence,
            lease_token,
            lease_expires_at
        )
        SELECT
            id,
            1,
            generation_lease_token,
            generation_lease_expires_at
        FROM reports
        WHERE generation_lease_token IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_report_generation_leases_lease_expires_at"),
        table_name="report_generation_leases",
    )
    op.drop_table("report_generation_leases")
