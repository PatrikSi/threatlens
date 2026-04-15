"""add durable state to notification webhook deliveries

Revision ID: 0024_delivery_outbox
Revises: 0023_delivery_source_refs
Create Date: 2026-04-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_delivery_outbox"
down_revision = "0023_delivery_source_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("scope_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("delivery_state", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notification_webhook_deliveries_scope_key",
        "notification_webhook_deliveries",
        ["scope_key"],
        unique=False,
    )
    op.create_index(
        "ix_notification_webhook_deliveries_delivery_state",
        "notification_webhook_deliveries",
        ["delivery_state"],
        unique=False,
    )

    op.execute(
        """
        UPDATE notification_webhook_deliveries
        SET delivery_state = CASE WHEN success THEN 'succeeded' ELSE 'failed' END,
            attempt_count = 1
        """
    )

    op.alter_column(
        "notification_webhook_deliveries",
        "delivery_state",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="pending",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_webhook_deliveries_delivery_state",
        table_name="notification_webhook_deliveries",
    )
    op.drop_index(
        "ix_notification_webhook_deliveries_scope_key",
        table_name="notification_webhook_deliveries",
    )
    op.drop_column("notification_webhook_deliveries", "claimed_at")
    op.drop_column("notification_webhook_deliveries", "attempt_count")
    op.drop_column("notification_webhook_deliveries", "delivery_state")
    op.drop_column("notification_webhook_deliveries", "scope_key")
