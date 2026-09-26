"""Independent team routing and shared account quota admission.

Revision ID: 0113_ai_quota_groups
Revises: 0112_automation_executions
"""

from alembic import op
import sqlalchemy as sa

revision = "0113_ai_quota_groups"
down_revision = "0112_automation_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_routing",
        sa.Column("team_assessment_provider_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_routing_team_provider",
        "ai_provider_routing",
        "ai_provider_configurations",
        ["team_assessment_provider_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "ai_quota_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(360), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "max_concurrent_requests", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "hourly_token_budget", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "max_concurrent_per_team", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_ai_quota_group_version"),
        sa.CheckConstraint(
            "max_concurrent_requests >= 0 AND hourly_token_budget >= 0 AND max_concurrent_per_team >= 0",
            name="ck_ai_quota_group_limits",
        ),
    )
    op.create_table(
        "ai_quota_group_members",
        sa.Column("provider_key", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("ai_quota_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_quota_group_members_group_id", "ai_quota_group_members", ["group_id"]
    )
    op.create_table(
        "ai_quota_team_turns",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("ai_quota_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("team_key", sa.String(64), primary_key=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True)),
        sa.Column("waiting_until", sa.DateTime(timezone=True)),
        sa.Column("last_served_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ai_quota_turn_expiry", "ai_quota_team_turns", ["waiting_until"])
    op.add_column(
        "ai_provider_budget_reservations", sa.Column("quota_group_key", sa.String(64))
    )
    op.add_column(
        "ai_provider_budget_reservations", sa.Column("team_key", sa.String(64))
    )
    op.create_index(
        "ix_ai_budget_group_created",
        "ai_provider_budget_reservations",
        ["quota_group_key", "created_at"],
    )
    op.execute(
        "INSERT INTO ai_provider_budget_states (provider_key) VALUES ('!quota-configuration') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    # Do not silently remove configured account budgets or routing on rollback.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM ai_quota_group_members)
           OR EXISTS (SELECT 1 FROM ai_provider_routing WHERE team_assessment_provider_id IS NOT NULL)
           OR EXISTS (SELECT 1 FROM ai_provider_budget_reservations WHERE quota_group_key IS NOT NULL)
        THEN RAISE EXCEPTION 'Archive and clear shared quota membership, reservation attribution and team-assessment routing before downgrade'; END IF;
    END $$""")
    op.drop_index(
        "ix_ai_budget_group_created", table_name="ai_provider_budget_reservations"
    )
    op.drop_column("ai_provider_budget_reservations", "team_key")
    op.drop_column("ai_provider_budget_reservations", "quota_group_key")
    op.drop_table("ai_quota_team_turns")
    op.drop_table("ai_quota_group_members")
    op.drop_table("ai_quota_groups")
    op.drop_constraint(
        "fk_ai_routing_team_provider", "ai_provider_routing", type_="foreignkey"
    )
    op.drop_column("ai_provider_routing", "team_assessment_provider_id")
