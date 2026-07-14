"""add integration delivery metric rollups

Revision ID: 0037_integration_metrics
Revises: 0036_integration_sub_feeds
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_integration_metrics"
down_revision = "0036_integration_sub_feeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_deliveries",
        sa.Column("metrics_aggregated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_integration_deliveries_metrics_aggregated_at",
        "integration_deliveries",
        ["metrics_aggregated_at"],
        unique=False,
    )
    op.create_table(
        "integration_delivery_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("connector_type", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dead_letter_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_total_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_max_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bucket_start",
            "integration_id",
            "connector_type",
            "event_type",
            name="uq_integration_delivery_metrics_bucket_dimension",
        ),
    )
    op.create_index(
        "ix_integration_delivery_metrics_bucket_start",
        "integration_delivery_metrics",
        ["bucket_start"],
        unique=False,
    )
    op.create_index(
        "ix_integration_delivery_metrics_integration_id",
        "integration_delivery_metrics",
        ["integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_delivery_metrics_connector_bucket",
        "integration_delivery_metrics",
        ["connector_type", "bucket_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_delivery_metrics_connector_bucket",
        table_name="integration_delivery_metrics",
    )
    op.drop_index(
        "ix_integration_delivery_metrics_integration_id",
        table_name="integration_delivery_metrics",
    )
    op.drop_index(
        "ix_integration_delivery_metrics_bucket_start",
        table_name="integration_delivery_metrics",
    )
    op.drop_table("integration_delivery_metrics")
    op.drop_index(
        "ix_integration_deliveries_metrics_aggregated_at",
        table_name="integration_deliveries",
    )
    op.drop_column("integration_deliveries", "metrics_aggregated_at")
