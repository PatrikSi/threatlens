"""Persist incomplete automatic tagging independently of classification.

Revision ID: 0089_tagging_recovery
Revises: 0088_report_library_indexes
"""
from alembic import op
import sqlalchemy as sa

revision = "0089_tagging_recovery"
down_revision = "0088_report_library_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("tagging_pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("items", sa.Column("tagging_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("items", sa.Column("tagging_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("items", sa.Column("tagging_error_code", sa.String(32), nullable=True))
    op.create_check_constraint("ck_items_tagging_attempts", "items", "tagging_attempts >= 0 AND tagging_attempts <= 5")
    op.create_index("ix_items_pending_tagging", "items", ["tagging_retry_at", "id"],
                    postgresql_where=sa.text("tagging_pending"))


def downgrade() -> None:
    op.drop_index("ix_items_pending_tagging", table_name="items")
    op.drop_constraint("ck_items_tagging_attempts", "items", type_="check")
    for name in ("tagging_error_code", "tagging_attempts", "tagging_retry_at", "tagging_pending"):
        op.drop_column("items", name)
