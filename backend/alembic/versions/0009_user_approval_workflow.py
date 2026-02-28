"""add user approval workflow columns

Revision ID: 0009_user_approval_workflow
Revises: 0008_tag_feedback_events
Create Date: 2026-02-28 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_user_approval_workflow"
down_revision = "0008_tag_feedback_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "users",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE users SET approved_at = created_at WHERE is_approved = true")
    op.alter_column("users", "is_approved", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "approved_at")
    op.drop_column("users", "is_approved")
