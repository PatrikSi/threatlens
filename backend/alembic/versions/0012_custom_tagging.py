"""add custom tagging settings and rules

Revision ID: 0012_custom_tagging
Revises: 0011_webhook_delivery_history
Create Date: 2026-03-26 00:00:02.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_custom_tagging"
down_revision = "0011_webhook_delivery_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tagging_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("enabled_categories_json", sa.JSON(), nullable=False),
        sa.Column("min_auto_tag_confidence", sa.Float(), nullable=False, server_default="0.45"),
        sa.Column("secondary_tag_limit", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tagging_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tag_name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("match_type", sa.String(length=16), nullable=False, server_default="contains"),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("applies_to_json", sa.JSON(), nullable=False),
        sa.Column("required_categories_json", sa.JSON(), nullable=False),
        sa.Column("feed_scope", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("feed_ids_json", sa.JSON(), nullable=False),
        sa.Column("min_classification_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("tagging_rules")
    op.drop_table("tagging_settings")
