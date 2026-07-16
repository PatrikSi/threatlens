"""normalize integration subscription feed filters

Revision ID: 0036_integration_sub_feeds
Revises: 0035_integration_platform
Create Date: 2026-07-14
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "0036_integration_sub_feeds"
down_revision = "0035_integration_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_subscriptions",
        sa.Column("feed_scope", sa.String(length=16), nullable=False, server_default="all"),
    )
    op.create_index(
        "ix_integration_subscriptions_feed_scope",
        "integration_subscriptions",
        ["feed_scope"],
        unique=False,
    )
    op.create_table(
        "integration_subscription_feeds",
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["integration_subscriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subscription_id", "feed_id"),
    )
    op.create_index(
        "ix_integration_subscription_feeds_feed_id",
        "integration_subscription_feeds",
        ["feed_id"],
        unique=False,
    )
    op.create_index(
        "uq_integration_deliveries_live_event_subscription",
        "integration_deliveries",
        ["event_id", "subscription_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL AND delivery_kind = 'live'"),
    )
    _backfill_subscription_feeds()


def downgrade() -> None:
    op.drop_index(
        "uq_integration_deliveries_live_event_subscription",
        table_name="integration_deliveries",
    )
    op.drop_index("ix_integration_subscription_feeds_feed_id", table_name="integration_subscription_feeds")
    op.drop_table("integration_subscription_feeds")
    op.drop_index("ix_integration_subscriptions_feed_scope", table_name="integration_subscriptions")
    op.drop_column("integration_subscriptions", "feed_scope")


def _backfill_subscription_feeds() -> None:
    connection = op.get_bind()
    known_feed_ids = {
        row[0]
        for row in connection.execute(sa.text("SELECT id FROM feeds")).all()
    }
    subscriptions = connection.execute(
        sa.text("SELECT id, filter_json FROM integration_subscriptions")
    ).mappings()
    for subscription in subscriptions:
        filters = subscription["filter_json"] if isinstance(subscription["filter_json"], dict) else {}
        feed_scope = filters.get("feed_scope") if filters.get("feed_scope") in {"all", "selected"} else "all"
        connection.execute(
            sa.text("UPDATE integration_subscriptions SET feed_scope = :feed_scope WHERE id = :id"),
            {"feed_scope": feed_scope, "id": subscription["id"]},
        )
        if feed_scope != "selected":
            continue
        for raw_feed_id in filters.get("feed_ids") or []:
            try:
                feed_id = uuid.UUID(str(raw_feed_id))
            except (TypeError, ValueError):
                continue
            if feed_id not in known_feed_ids:
                continue
            connection.execute(
                sa.text(
                    """
                    INSERT INTO integration_subscription_feeds (subscription_id, feed_id)
                    VALUES (:subscription_id, :feed_id)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"subscription_id": subscription["id"], "feed_id": feed_id},
            )
