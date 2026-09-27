"""Durable and budgeted feature compatibility qualification."""
from alembic import op
import sqlalchemy as sa
revision = "0118_ai_qualification"
down_revision = "0117_article_continuations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_qualifications",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("request_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("provider_version", sa.Integer(), nullable=False),
        sa.Column("principal_type", sa.String(32), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_encrypted", sa.JSON(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_ai_qualifications_provider_id", "ai_qualifications", ["provider_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM ai_qualifications)")):
        raise RuntimeError("Archive and explicitly clear provider qualifications before downgrade")
    op.drop_table("ai_qualifications")
