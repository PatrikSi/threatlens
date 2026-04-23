"""track item IOC extraction completion state

Revision ID: 0028_item_ioc_extract
Revises: 0027_feed_url_secret
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_item_ioc_extract"
down_revision = "0027_feed_url_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("ioc_extraction_state", sa.String(length=32), nullable=True))
    op.create_index("ix_items_ioc_extraction_state", "items", ["ioc_extraction_state"], unique=False)
    op.execute(
        sa.text(
            """
            UPDATE items
            SET ioc_extraction_state = 'completed'
            WHERE EXISTS (
                SELECT 1
                FROM item_iocs
                WHERE item_iocs.item_id = items.id
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_items_ioc_extraction_state", table_name="items")
    op.drop_column("items", "ioc_extraction_state")
