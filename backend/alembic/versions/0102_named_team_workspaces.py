"""Add group-backed teams and ownership of shared views and investigations."""

from alembic import op
import sqlalchemy as sa

revision = "0102_named_team_workspaces"
down_revision = "0101_export_dispatch_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "membership_group_id",
            sa.Uuid(),
            sa.ForeignKey("iam_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "manager_group_id",
            sa.Uuid(),
            sa.ForeignKey("iam_groups.id", ondelete="RESTRICT"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name="ck_teams_revision"),
    )
    for column in ("membership_group_id", "manager_group_id"):
        op.create_index(f"ix_teams_{column}", "teams", [column])
    for table in ("saved_views", "investigations"):
        op.add_column(
            table,
            sa.Column(
                "team_id",
                sa.Uuid(),
                sa.ForeignKey("teams.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_team_id", table, ["team_id"])
    op.alter_column("saved_views", "user_id", nullable=True)
    op.create_check_constraint(
        "ck_saved_views_owner",
        "saved_views",
        "(user_id IS NOT NULL AND team_id IS NULL) OR (user_id IS NULL AND team_id IS NOT NULL)",
    )


def downgrade() -> None:
    # Downgrade cannot choose a private owner for shared team content safely.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM saved_views WHERE team_id IS NOT NULL) OR EXISTS (SELECT 1 FROM investigations WHERE team_id IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Archive or explicitly migrate team-owned views and investigations before downgrading named teams."
        )
    op.drop_constraint("ck_saved_views_owner", "saved_views", type_="check")
    op.alter_column("saved_views", "user_id", nullable=False)
    for table in ("saved_views", "investigations"):
        op.drop_index(f"ix_{table}_team_id", table_name=table)
        op.drop_column(table, "team_id")
    op.drop_table("teams")
