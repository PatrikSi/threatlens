"""preserve report generation context and template topic filters

Revision ID: 0045_reporting_context
Revises: 0044_reporting
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0045_reporting_context"
down_revision = "0044_reporting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_templates",
        sa.Column(
            "focus_topics_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "report_templates",
        sa.Column(
            "excluded_topics_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "reports",
        sa.Column(
            "generation_context_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "generation_context_json")
    op.drop_column("report_templates", "excluded_topics_json")
    op.drop_column("report_templates", "focus_topics_json")
