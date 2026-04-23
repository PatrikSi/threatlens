"""add due-feed scheduling and item search/stat indexes

Revision ID: 0030_schedule_search_indexes
Revises: 0029_webhook_secret_backfill
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0030_schedule_search_indexes"
down_revision = "0029_webhook_secret_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    op.add_column("feeds", sa.Column("next_fetch_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_feeds_enabled_next_fetch_at", "feeds", ["enabled", "next_fetch_at"], unique=False)

    op.add_column("items", sa.Column("url_domain", sa.String(length=253), nullable=True))
    op.create_index("ix_items_url_domain", "items", ["url_domain"], unique=False)
    op.create_index("ix_items_status_first_seen_at", "items", ["status", "first_seen_at"], unique=False)
    op.create_index("ix_items_feed_first_seen_at", "items", ["feed_id", "first_seen_at"], unique=False)
    op.create_index("ix_items_feed_published_at", "items", ["feed_id", "published_at"], unique=False)

    if dialect_name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE feeds
                SET next_fetch_at = CASE
                    WHEN NOT enabled THEN NULL
                    WHEN dispatch_backoff_until IS NOT NULL AND dispatch_backoff_until > NOW() THEN dispatch_backoff_until
                    WHEN last_fetch_at IS NULL THEN NOW()
                    WHEN fetch_mode = 'interval' THEN last_fetch_at + (GREATEST(fetch_interval_seconds, 60) * INTERVAL '1 second')
                    ELSE NOW()
                END
                WHERE next_fetch_at IS NULL
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE items
                SET url_domain = NULLIF(
                    lower(
                        left(
                            split_part(
                                split_part(
                                    regexp_replace(coalesce(nullif(canonical_url, ''), url), '^[a-zA-Z]+://', ''),
                                    '/',
                                    1
                                ),
                                ':',
                                1
                            ),
                            253
                        )
                    ),
                    ''
                )
                WHERE url_domain IS NULL
                """
            )
        )
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_items_title_trgm ON items USING gin (lower(title) gin_trgm_ops)"))
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_items_summary_trgm "
                "ON items USING gin (lower(coalesce(summary, '')) gin_trgm_ops)"
            )
        )
        op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_items_url_trgm ON items USING gin (lower(url) gin_trgm_ops)"))
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_items_canonical_url_trgm "
                "ON items USING gin (lower(coalesce(canonical_url, '')) gin_trgm_ops)"
            )
        )
    else:
        op.execute(sa.text("UPDATE feeds SET next_fetch_at = CURRENT_TIMESTAMP WHERE enabled = 1 AND next_fetch_at IS NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP INDEX IF EXISTS ix_items_canonical_url_trgm"))
        op.execute(sa.text("DROP INDEX IF EXISTS ix_items_url_trgm"))
        op.execute(sa.text("DROP INDEX IF EXISTS ix_items_summary_trgm"))
        op.execute(sa.text("DROP INDEX IF EXISTS ix_items_title_trgm"))

    op.drop_index("ix_items_feed_published_at", table_name="items")
    op.drop_index("ix_items_feed_first_seen_at", table_name="items")
    op.drop_index("ix_items_status_first_seen_at", table_name="items")
    op.drop_index("ix_items_url_domain", table_name="items")
    op.drop_column("items", "url_domain")

    op.drop_index("ix_feeds_enabled_next_fetch_at", table_name="feeds")
    op.drop_column("feeds", "next_fetch_at")
