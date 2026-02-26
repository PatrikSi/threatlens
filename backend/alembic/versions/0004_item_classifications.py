"""add item classifications

Revision ID: 0004_item_classifications
Revises: 0003_feed_metadata_and_schedule
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_item_classifications"
down_revision = "0003_feed_metadata_and_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_classifications",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("primary_category", sa.String(length=64), nullable=False),
        sa.Column("secondary_categories", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("scores_json", sa.JSON(), nullable=False),
        sa.Column("matched_terms_json", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False, server_default="v1"),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index("ix_item_classifications_primary_category", "item_classifications", ["primary_category"])
    op.create_index("ix_item_classifications_classified_at", "item_classifications", ["classified_at"])
    op.create_index("ix_item_classifications_source_hash", "item_classifications", ["source_hash"])


def downgrade() -> None:
    op.drop_index("ix_item_classifications_source_hash", table_name="item_classifications")
    op.drop_index("ix_item_classifications_classified_at", table_name="item_classifications")
    op.drop_index("ix_item_classifications_primary_category", table_name="item_classifications")
    op.drop_table("item_classifications")
