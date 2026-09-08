"""Persist retention scan progress beyond oversized protected parents."""

from alembic import op
import sqlalchemy as sa

revision = "0090_lifecycle_scan_cursors"
down_revision = "0089_tagging_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_scan_cursors",
        sa.Column("dataset", sa.String(64), primary_key=True),
        sa.Column("last_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(last_timestamp IS NULL) = (last_id IS NULL)",
            name="ck_lifecycle_scan_cursor_anchor",
        ),
    )


def downgrade() -> None:
    op.drop_table("lifecycle_scan_cursors")
