"""record durable alert task publication

Revision ID: 0061_alert_dispatch_publication
Revises: 0060_iam_hardening
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0061_alert_dispatch_publication"
down_revision = "0060_iam_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_evaluation_requests",
        sa.Column("dispatch_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_alert_evaluation_requests_dispatch_publication",
        "alert_evaluation_requests",
        ["state", "dispatch_published_at", "available_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_evaluation_requests_dispatch_publication",
        table_name="alert_evaluation_requests",
    )
    op.drop_column("alert_evaluation_requests", "dispatch_published_at")
