"""add feed fetch fencing token

Revision ID: 0048_feed_fetch_fence
Revises: 0047_report_dispatch
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0048_feed_fetch_fence"
down_revision = "0047_report_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feeds",
        sa.Column(
            "fetch_fence",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("feeds", "fetch_fence")
