"""add notification delivery event snapshots

Revision ID: 0013_notification_events
Revises: 0012_custom_tagging
Create Date: 2026-03-26 00:00:03.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_notification_events"
down_revision = "0012_custom_tagging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("event_type_snapshot", sa.String(length=64), nullable=False, server_default="rss_item_new"),
    )
    op.create_index(
        op.f("ix_notification_webhook_deliveries_event_type_snapshot"),
        "notification_webhook_deliveries",
        ["event_type_snapshot"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_notification_webhook_deliveries_event_type_snapshot"),
        table_name="notification_webhook_deliveries",
    )
    op.drop_column("notification_webhook_deliveries", "event_type_snapshot")
