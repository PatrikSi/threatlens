"""index normalized IOC values for evidence candidate search

Revision ID: 0084_ioc_candidate_search
Revises: 0083_system_health_history
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0084_ioc_candidate_search"
down_revision = "0083_system_health_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        op.execute(
            sa.text(
                "CREATE INDEX ix_iocs_value_norm_trgm "
                "ON iocs USING gin (lower(value_norm) gin_trgm_ops)"
            )
        )
    else:
        op.create_index(
            "ix_iocs_value_norm_trgm",
            "iocs",
            [sa.text("lower(value_norm)")],
        )
    op.create_index(
        "ix_alert_occurrences_owner_created",
        "alert_occurrences",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_reports_observed_at",
        "reports",
        [sa.text("coalesce(generated_at, created_at)")],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_observed_at", table_name="reports")
    op.drop_index(
        "ix_alert_occurrences_owner_created",
        table_name="alert_occurrences",
    )
    op.drop_index("ix_iocs_value_norm_trgm", table_name="iocs")
