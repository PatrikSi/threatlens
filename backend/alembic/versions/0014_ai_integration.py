"""add ai integration tables

Revision ID: 0014_ai_integration
Revises: 0013_notification_events
Create Date: 2026-03-26 00:00:03.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_ai_integration"
down_revision = "0013_notification_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False, server_default="openai_compatible"),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("max_completion_tokens", sa.Integer(), nullable=False, server_default="700"),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("summary_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("relevance_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("daily_brief_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auto_enrich_new_items", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("daily_brief_window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("daily_brief_max_items", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("relevance_medium_threshold", sa.Float(), nullable=False, server_default="0.55"),
        sa.Column("relevance_high_threshold", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("company_industry", sa.String(length=255), nullable=True),
        sa.Column("company_regions_json", sa.JSON(), nullable=False),
        sa.Column("company_stack_json", sa.JSON(), nullable=False),
        sa.Column("company_priority_topics_json", sa.JSON(), nullable=False),
        sa.Column("company_keywords_json", sa.JSON(), nullable=False),
        sa.Column("company_exclusions_json", sa.JSON(), nullable=False),
        sa.Column("company_profile_text", sa.Text(), nullable=True),
        sa.Column("global_instructions", sa.Text(), nullable=True),
        sa.Column("item_summary_instructions", sa.Text(), nullable=True),
        sa.Column("relevance_instructions", sa.Text(), nullable=True),
        sa.Column("daily_brief_instructions", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ai_daily_briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("brief_text", sa.Text(), nullable=True),
        sa.Column("key_points_json", sa.JSON(), nullable=False),
        sa.Column("recommended_actions_json", sa.JSON(), nullable=False),
        sa.Column("top_item_ids_json", sa.JSON(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brief_date", name="uq_ai_daily_briefs_brief_date"),
    )
    op.create_index(op.f("ix_ai_daily_briefs_brief_date"), "ai_daily_briefs", ["brief_date"], unique=False)

    op.create_table(
        "item_ai_enrichments",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("source_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("relevance_label", sa.String(length=16), nullable=True),
        sa.Column("relevance_reasons_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )

    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feature_type", sa.String(length=32), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("daily_brief_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["daily_brief_id"], ["ai_daily_briefs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_usage_events_created_at"), "ai_usage_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_usage_events_feature_type"), "ai_usage_events", ["feature_type"], unique=False)
    op.create_index(op.f("ix_ai_usage_events_model"), "ai_usage_events", ["model"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_usage_events_model"), table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_feature_type"), table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_created_at"), table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
    op.drop_table("item_ai_enrichments")
    op.drop_index(op.f("ix_ai_daily_briefs_brief_date"), table_name="ai_daily_briefs")
    op.drop_table("ai_daily_briefs")
    op.drop_table("ai_settings")
