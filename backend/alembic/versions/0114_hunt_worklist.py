"""Versioned team hunt claims.

Revision ID: 0114_hunt_worklist
Revises: 0113_ai_quota_groups
"""

from alembic import op
import sqlalchemy as sa

revision = "0114_hunt_worklist"
down_revision = "0113_ai_quota_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_hunt_claims",
        sa.Column(
            "assessment_id",
            sa.Uuid(),
            sa.ForeignKey("team_item_assessments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("hunt_id", sa.String(80), primary_key=True),
        sa.Column(
            "owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_team_hunt_claim_version"),
    )
    op.create_index(
        "ix_team_hunt_claims_owner_user_id", "team_hunt_claims", ["owner_user_id"]
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM team_hunt_claims WHERE owner_user_id IS NOT NULL)
        THEN RAISE EXCEPTION 'Release team hunt claims before downgrade'; END IF;
    END $$""")
    op.drop_table("team_hunt_claims")
