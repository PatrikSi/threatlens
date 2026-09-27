"""Durable team review deadlines, reminders and shared queue filters."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0120_hunt_review_workflow"
down_revision = "0119_team_ai_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_hunt_claims",
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "team_hunt_claims",
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
    )
    for name in ("review_due_at", "reminded_at", "reminder_acknowledged_at"):
        op.add_column("team_hunt_claims", sa.Column(name, sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_team_hunt_review_metadata",
        "team_hunt_claims",
        "review_version >= 0 AND priority IN ('low', 'normal', 'high', 'urgent')",
    )
    op.create_index(
        "ix_team_hunt_review_due", "team_hunt_claims", ["review_due_at", "reminded_at"]
    )
    op.create_table(
        "team_hunt_views",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filters_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("team_id", "name", name="uq_team_hunt_view_name"),
        sa.CheckConstraint("version >= 1", name="ck_team_hunt_view_version"),
    )
    op.create_index("ix_team_hunt_views_team_id", "team_hunt_views", ["team_id"])


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM team_hunt_views) OR EXISTS (SELECT 1 FROM team_hunt_claims WHERE review_version > 0)"
            )
        )
        .scalar()
    ):
        raise RuntimeError(
            "Retained hunt review schedules or saved views prevent a lossy downgrade. Export and explicitly remove them first."
        )
    op.drop_table("team_hunt_views")
    op.drop_index("ix_team_hunt_review_due", "team_hunt_claims")
    op.drop_constraint("ck_team_hunt_review_metadata", "team_hunt_claims")
    for name in (
        "reminder_acknowledged_at",
        "reminded_at",
        "review_due_at",
        "priority",
        "review_version",
    ):
        op.drop_column("team_hunt_claims", name)
