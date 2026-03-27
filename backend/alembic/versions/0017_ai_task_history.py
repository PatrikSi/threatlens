"""add ai task history tables

Revision ID: 0017_ai_task_history
Revises: 0016_ai_system_prompts
Create Date: 2026-03-27 00:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_ai_task_history"
down_revision = "0016_ai_system_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_task_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("worker_name", sa.String(length=255), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("daily_brief_id", sa.Uuid(), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_char_count", sa.Integer(), nullable=True),
        sa.Column("response_char_count", sa.Integer(), nullable=True),
        sa.Column("input_text_chars", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_unchanged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_ineligible_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["daily_brief_id"], ["ai_daily_briefs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["ai_task_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_task_runs_task_type"), "ai_task_runs", ["task_type"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_trigger_source"), "ai_task_runs", ["trigger_source"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_status"), "ai_task_runs", ["status"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_reason"), "ai_task_runs", ["reason"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_celery_task_id"), "ai_task_runs", ["celery_task_id"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_actor_user_id"), "ai_task_runs", ["actor_user_id"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_item_id"), "ai_task_runs", ["item_id"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_daily_brief_id"), "ai_task_runs", ["daily_brief_id"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_parent_run_id"), "ai_task_runs", ["parent_run_id"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_model"), "ai_task_runs", ["model"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_queued_at"), "ai_task_runs", ["queued_at"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_started_at"), "ai_task_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_ai_task_runs_finished_at"), "ai_task_runs", ["finished_at"], unique=False)

    op.create_table(
        "ai_task_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_run_id"], ["ai_task_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_task_events_task_run_id"), "ai_task_events", ["task_run_id"], unique=False)
    op.create_index(op.f("ix_ai_task_events_event_type"), "ai_task_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_ai_task_events_created_at"), "ai_task_events", ["created_at"], unique=False)

    op.create_table(
        "ai_daily_brief_source_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("daily_brief_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exclusion_reason", sa.String(length=64), nullable=True),
        sa.Column("title_snapshot", sa.Text(), nullable=False),
        sa.Column("feed_name_snapshot", sa.Text(), nullable=True),
        sa.Column("url_snapshot", sa.Text(), nullable=True),
        sa.Column("classification_snapshot", sa.String(length=64), nullable=True),
        sa.Column("relevance_score_snapshot", sa.Float(), nullable=True),
        sa.Column("relevance_label_snapshot", sa.String(length=16), nullable=True),
        sa.Column("published_at_snapshot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at_snapshot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["daily_brief_id"], ["ai_daily_briefs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_daily_brief_source_items_daily_brief_id"),
        "ai_daily_brief_source_items",
        ["daily_brief_id"],
        unique=False,
    )
    op.create_index(op.f("ix_ai_daily_brief_source_items_item_id"), "ai_daily_brief_source_items", ["item_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_task_events_created_at"), table_name="ai_task_events")
    op.drop_index(op.f("ix_ai_task_events_event_type"), table_name="ai_task_events")
    op.drop_index(op.f("ix_ai_task_events_task_run_id"), table_name="ai_task_events")
    op.drop_table("ai_task_events")

    op.drop_index(op.f("ix_ai_daily_brief_source_items_item_id"), table_name="ai_daily_brief_source_items")
    op.drop_index(op.f("ix_ai_daily_brief_source_items_daily_brief_id"), table_name="ai_daily_brief_source_items")
    op.drop_table("ai_daily_brief_source_items")

    op.drop_index(op.f("ix_ai_task_runs_finished_at"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_started_at"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_queued_at"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_model"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_parent_run_id"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_daily_brief_id"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_item_id"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_actor_user_id"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_celery_task_id"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_reason"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_status"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_trigger_source"), table_name="ai_task_runs")
    op.drop_index(op.f("ix_ai_task_runs_task_type"), table_name="ai_task_runs")
    op.drop_table("ai_task_runs")
