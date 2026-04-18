"""add durable dispatch claims to feeds

Revision ID: 0025_feed_dispatch_claims
Revises: 0024_delivery_outbox
Create Date: 2026-04-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_feed_dispatch_claims"
down_revision = "0024_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("feeds", sa.Column("dispatch_backoff_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_feeds_dispatch_claimed_at", "feeds", ["dispatch_claimed_at"], unique=False)
    op.create_index("ix_feeds_dispatch_backoff_until", "feeds", ["dispatch_backoff_until"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feeds_dispatch_backoff_until", table_name="feeds")
    op.drop_index("ix_feeds_dispatch_claimed_at", table_name="feeds")
    op.drop_column("feeds", "dispatch_backoff_until")
    op.drop_column("feeds", "dispatch_claimed_at")
