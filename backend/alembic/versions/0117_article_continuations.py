"""Retain exact, credential-bound article continuation authorizations."""
from alembic import op
import sqlalchemy as sa

revision = "0117_article_continuations"
down_revision = "0116_team_integrations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_article_continuations",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("principal_type", sa.String(32), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_encrypted", sa.JSON(), nullable=False),
        sa.Column("expected_progress_digest", sa.String(64), nullable=False),
        sa.Column("section_limit", sa.Integer(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ai_article_continuations_item_id", "ai_article_continuations", ["item_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM ai_article_continuations)")):
        raise RuntimeError("Archive and explicitly clear article continuation authorizations before downgrade")
    op.drop_table("ai_article_continuations")
