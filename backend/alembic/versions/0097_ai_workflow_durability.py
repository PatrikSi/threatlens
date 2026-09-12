"""Persist AI dispatch and immutable article reprocessing membership."""

from alembic import op
import sqlalchemy as sa

revision = "0097_ai_workflow_durability"
down_revision = "0096_ai_provider_capabilities"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_workflow_dispatches",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("task_name", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("delivery_id", sa.String(255)),
        sa.Column("claim_token", sa.String(64)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(128)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_workflow_dispatch_due", "ai_workflow_dispatches", ["state", "next_attempt_at"])
    op.create_table(
        "ai_reprocess_members",
        sa.Column("parent_run_id", sa.Uuid(), sa.ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_id", sa.Uuid(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("child_run_id", sa.Uuid()),
        sa.Column("outcome", sa.String(16)),
        sa.Column("reason", sa.String(64)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ai_reprocess_members_child", "ai_reprocess_members", ["child_run_id"], unique=True)
    op.create_table(
        "ai_report_stage_artifacts",
        sa.Column("task_run_id", sa.Uuid(), sa.ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("operation_scope", sa.String(128), primary_key=True),
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("completion_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_report_stage_artifacts_report_id", "ai_report_stage_artifacts", ["report_id"])


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM ai_workflow_dispatches WHERE state <> 'complete') "
        "OR EXISTS (SELECT 1 FROM ai_report_stage_artifacts a JOIN ai_task_runs r "
        "ON r.id = a.task_run_id WHERE r.finished_at IS NULL)"
    )):
        raise RuntimeError("Finish or cancel accepted AI work before removing durable queue recovery.")
    op.drop_table("ai_report_stage_artifacts")
    op.drop_table("ai_reprocess_members")
    op.drop_table("ai_workflow_dispatches")
