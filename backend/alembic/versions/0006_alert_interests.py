"""add alert interests

Revision ID: 0006_alert_interests
Revises: 0005_iocs_graph_support
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_alert_interests"
down_revision = "0005_iocs_graph_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_interests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_interests_user_id", "alert_interests", ["user_id"])
    op.create_index("ix_alert_interests_user_id_category", "alert_interests", ["user_id", "category"])
    op.create_index("ix_alert_interests_user_id_enabled", "alert_interests", ["user_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_alert_interests_user_id_enabled", table_name="alert_interests")
    op.drop_index("ix_alert_interests_user_id_category", table_name="alert_interests")
    op.drop_index("ix_alert_interests_user_id", table_name="alert_interests")
    op.drop_table("alert_interests")
