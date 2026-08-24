"""identify report dispatch protocol generations

Revision ID: 0054_report_dispatch_protocol
Revises: 0053_report_operation_receipts
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0054_report_dispatch_protocol"
down_revision = "0053_report_operation_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the server default at v1 during rolling upgrades. Old API processes
    # do not know this column and must remain distinguishable from v2 publishers.
    op.add_column(
        "ai_task_runs",
        sa.Column(
            "dispatch_protocol_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_task_runs", "dispatch_protocol_version")
