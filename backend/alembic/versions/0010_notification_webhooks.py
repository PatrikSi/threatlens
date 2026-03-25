"""add notification webhooks

Revision ID: 0010_notification_webhooks
Revises: 0009_user_approval_workflow
Create Date: 2026-03-25 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_notification_webhooks"
down_revision = "0009_user_approval_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("event_type", sa.String(length=64), nullable=False, server_default="rss_item_new"),
        sa.Column("url_template", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False, server_default="POST"),
        sa.Column("feed_scope", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("feed_ids_json", sa.JSON(), nullable=False),
        sa.Column("query_params_json", sa.JSON(), nullable=False),
        sa.Column("headers_json", sa.JSON(), nullable=False),
        sa.Column("body_mode", sa.String(length=16), nullable=False, server_default="json"),
        sa.Column("body_fields_json", sa.JSON(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_webhooks_user_id"), "notification_webhooks", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_webhooks_user_id"), table_name="notification_webhooks")
    op.drop_table("notification_webhooks")
