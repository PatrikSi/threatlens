"""add item tag metadata for tagging v2

Revision ID: 0007_item_tag_metadata_v2
Revises: 0006_alert_interests
Create Date: 2026-02-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_item_tag_metadata_v2"
down_revision = "0006_alert_interests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_tags",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
    )
    op.add_column(
        "item_tags",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="rule"),
    )
    op.add_column(
        "item_tags",
        sa.Column("rules_version", sa.String(length=64), nullable=True, server_default="legacy"),
    )
    op.add_column(
        "item_tags",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_check_constraint(
        "ck_item_tags_confidence",
        "item_tags",
        "confidence >= 0 AND confidence <= 1",
    )
    op.create_check_constraint(
        "ck_item_tags_source",
        "item_tags",
        "source IN ('rule', 'ioc', 'manual', 'ml')",
    )
    op.create_index("ix_item_tags_item_source", "item_tags", ["item_id", "source"])


def downgrade() -> None:
    op.drop_index("ix_item_tags_item_source", table_name="item_tags")
    op.drop_constraint("ck_item_tags_source", "item_tags", type_="check")
    op.drop_constraint("ck_item_tags_confidence", "item_tags", type_="check")
    op.drop_column("item_tags", "updated_at")
    op.drop_column("item_tags", "rules_version")
    op.drop_column("item_tags", "source")
    op.drop_column("item_tags", "confidence")
