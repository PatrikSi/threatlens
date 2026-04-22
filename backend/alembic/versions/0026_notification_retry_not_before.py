"""persist retry scheduling for notification deliveries

Revision ID: 0026_notification_retry_nb
Revises: 0025_feed_dispatch_claims
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_notification_retry_nb"
down_revision = "0025_feed_dispatch_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notification_webhook_deliveries_not_before",
        "notification_webhook_deliveries",
        ["not_before"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_webhook_deliveries_not_before",
        table_name="notification_webhook_deliveries",
    )
    op.drop_column("notification_webhook_deliveries", "not_before")
