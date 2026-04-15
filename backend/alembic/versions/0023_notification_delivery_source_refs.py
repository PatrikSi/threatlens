"""add source delivery references for notification webhook fan-out

Revision ID: 0023_delivery_source_refs
Revises: 0022_settings_feed_urls
Create Date: 2026-04-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_delivery_source_refs"
down_revision = "0022_settings_feed_urls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("source_delivery_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_notification_webhook_deliveries_source_delivery_id",
        "notification_webhook_deliveries",
        ["source_delivery_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_notification_webhook_deliveries_source_delivery_id",
        "notification_webhook_deliveries",
        "notification_webhook_deliveries",
        ["source_delivery_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_notification_webhook_deliveries_source_delivery_id",
        "notification_webhook_deliveries",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_notification_webhook_deliveries_source_delivery_id",
        table_name="notification_webhook_deliveries",
    )
    op.drop_column("notification_webhook_deliveries", "source_delivery_id")
