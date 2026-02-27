"""add tag feedback events table

Revision ID: 0008_tag_feedback_events
Revises: 0007_item_tag_metadata_v2
Create Date: 2026-02-27 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_tag_feedback_events"
down_revision = "0007_item_tag_metadata_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tag_feedback_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tag_name", sa.String(length=64), nullable=False),
        sa.Column("signal_type", sa.String(length=24), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "signal_type IN ('manual_add', 'manual_remove', 'star', 'unstar', 'read', 'unread')",
            name="ck_tag_feedback_signal_type",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tag_feedback_events_tag_name", "tag_feedback_events", ["tag_name"])
    op.create_index("ix_tag_feedback_events_item_id", "tag_feedback_events", ["item_id"])
    op.create_index("ix_tag_feedback_events_created_at", "tag_feedback_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_tag_feedback_events_created_at", table_name="tag_feedback_events")
    op.drop_index("ix_tag_feedback_events_item_id", table_name="tag_feedback_events")
    op.drop_index("ix_tag_feedback_events_tag_name", table_name="tag_feedback_events")
    op.drop_table("tag_feedback_events")
