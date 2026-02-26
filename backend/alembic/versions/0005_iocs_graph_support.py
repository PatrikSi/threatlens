"""add ioc tables for pivot graph

Revision ID: 0005_iocs_graph_support
Revises: 0004_item_classifications
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_iocs_graph_support"
down_revision = "0004_item_classifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iocs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("value_raw", sa.Text(), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "value_norm", name="uq_iocs_type_value_norm"),
    )
    op.create_index("ix_iocs_type", "iocs", ["type"])
    op.create_index("ix_iocs_value_norm", "iocs", ["value_norm"])
    op.create_index("ix_iocs_last_seen_at", "iocs", ["last_seen_at"])

    op.create_table(
        "item_iocs",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("ioc_id", sa.Uuid(), nullable=False),
        sa.Column("source_section", sa.String(length=32), nullable=False, server_default="article"),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ioc_id"], ["iocs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id", "ioc_id", name="pk_item_iocs"),
    )
    op.create_index("ix_item_iocs_ioc_id", "item_iocs", ["ioc_id"])


def downgrade() -> None:
    op.drop_index("ix_item_iocs_ioc_id", table_name="item_iocs")
    op.drop_table("item_iocs")

    op.drop_index("ix_iocs_last_seen_at", table_name="iocs")
    op.drop_index("ix_iocs_value_norm", table_name="iocs")
    op.drop_index("ix_iocs_type", table_name="iocs")
    op.drop_table("iocs")
