"""add notification webhook delivery history

Revision ID: 0011_webhook_delivery_history
Revises: 0010_notification_webhooks
Create Date: 2026-03-26 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_webhook_delivery_history"
down_revision = "0010_notification_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("feed_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_kind", sa.String(length=16), nullable=False, server_default="live"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("rendered_url", sa.Text(), nullable=False),
        sa.Column("rendered_method", sa.String(length=16), nullable=False),
        sa.Column("rendered_headers_json", sa.JSON(), nullable=False),
        sa.Column("rendered_query_params_json", sa.JSON(), nullable=False),
        sa.Column("rendered_body", sa.Text(), nullable=True),
        sa.Column("response_body_preview", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("item_title_snapshot", sa.Text(), nullable=True),
        sa.Column("feed_name_snapshot", sa.String(length=255), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["webhook_id"], ["notification_webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notification_webhook_deliveries_attempted_at"),
        "notification_webhook_deliveries",
        ["attempted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_webhook_deliveries_user_id"),
        "notification_webhook_deliveries",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_webhook_deliveries_webhook_id"),
        "notification_webhook_deliveries",
        ["webhook_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_webhook_deliveries_webhook_id"), table_name="notification_webhook_deliveries")
    op.drop_index(op.f("ix_notification_webhook_deliveries_user_id"), table_name="notification_webhook_deliveries")
    op.drop_index(op.f("ix_notification_webhook_deliveries_attempted_at"), table_name="notification_webhook_deliveries")
    op.drop_table("notification_webhook_deliveries")
