"""add indexes for foreign-key lookup and cascade paths

Revision ID: 0043_fk_lookup_indexes
Revises: 0042_user_source
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op


revision = "0043_fk_lookup_indexes"
down_revision = "0042_user_source"
branch_labels = None
depends_on = None


INDEXES = (
    ("ix_ai_usage_events_item_id", "ai_usage_events", ["item_id"]),
    ("ix_ai_usage_events_daily_brief_id", "ai_usage_events", ["daily_brief_id"]),
    ("ix_item_state_item_id", "item_state", ["item_id"]),
    ("ix_item_tags_tag_id", "item_tags", ["tag_id"]),
    ("ix_notification_webhook_deliveries_item_id", "notification_webhook_deliveries", ["item_id"]),
    ("ix_notification_webhook_deliveries_feed_id", "notification_webhook_deliveries", ["feed_id"]),
    ("ix_saved_views_user_id", "saved_views", ["user_id"]),
    ("ix_tag_feedback_events_user_id", "tag_feedback_events", ["user_id"]),
)


def upgrade() -> None:
    for index_name, table_name, columns in INDEXES:
        op.create_index(index_name, table_name, columns, unique=False, if_not_exists=True)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name, if_exists=True)
