"""Approve team AI destinations and bound shared account short-window usage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0119_team_ai_governance"
down_revision = "0118_ai_qualification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_ai_governance",
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approved_provider_keys", postgresql.JSONB(), nullable=False),
        sa.Column("selected_provider_key", sa.String(64)),
        sa.Column("label_destinations", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_team_ai_governance_version"),
    )
    op.add_column("ai_quota_team_turns", sa.Column("last_denial_reason", sa.String(80)))
    op.add_column(
        "ai_quota_groups",
        sa.Column(
            "minute_request_budget", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "ai_quota_groups",
        sa.Column(
            "minute_token_budget", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "ai_quota_groups",
        sa.Column(
            "team_hourly_token_budgets",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.drop_constraint("ck_ai_quota_group_limits", "ai_quota_groups")
    op.create_check_constraint(
        "ck_ai_quota_group_limits",
        "ai_quota_groups",
        "max_concurrent_requests >= 0 AND hourly_token_budget >= 0 AND max_concurrent_per_team >= 0 AND minute_request_budget >= 0 AND minute_token_budget >= 0",
    )


def downgrade() -> None:
    retained = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM team_ai_governance) OR EXISTS (SELECT 1 FROM ai_quota_groups WHERE minute_request_budget <> 0 OR minute_token_budget <> 0 OR team_hourly_token_budgets <> '{}'::jsonb)"
            )
        )
        .scalar()
    )
    if retained:
        raise RuntimeError(
            "Remove team AI destination approvals and new quota policies explicitly before downgrading; rollback must not silently weaken egress or capacity policy."
        )
    op.drop_constraint("ck_ai_quota_group_limits", "ai_quota_groups")
    op.create_check_constraint(
        "ck_ai_quota_group_limits",
        "ai_quota_groups",
        "max_concurrent_requests >= 0 AND hourly_token_budget >= 0 AND max_concurrent_per_team >= 0",
    )
    for column in (
        "team_hourly_token_budgets",
        "minute_token_budget",
        "minute_request_budget",
    ):
        op.drop_column("ai_quota_groups", column)
    op.drop_column("ai_quota_team_turns", "last_denial_reason")
    op.drop_table("team_ai_governance")
