"""Retain pipeline freshness and bounded runtime pressure measurements."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0092_operations_freshness"
down_revision = "0091_processing_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_health_samples", sa.Column(
        "backlogs_json", postgresql.JSONB(), nullable=False, server_default="[]",
    ))
    op.add_column("system_health_samples", sa.Column(
        "runtime_metrics_json", postgresql.JSONB(), nullable=False, server_default="{}",
    ))


def downgrade() -> None:
    op.drop_column("system_health_samples", "runtime_metrics_json")
    op.drop_column("system_health_samples", "backlogs_json")
