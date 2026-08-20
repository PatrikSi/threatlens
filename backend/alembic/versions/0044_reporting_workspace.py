"""add reporting workspace and AI context guardrails

Revision ID: 0044_reporting
Revises: 0043_fk_lookup_indexes
Create Date: 2026-08-13
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0044_reporting"
down_revision = "0043_fk_lookup_indexes"
branch_labels = None
depends_on = None


BUILTIN_TEMPLATES = (
    (
        "11111111-1111-4111-8111-111111111101",
        "weekly_threat_landscape",
        "Weekly Threat Landscape",
        "A sourced weekly view of material threats, campaigns, vulnerabilities, and defensive priorities.",
        "weekly_landscape",
        "security_team",
        "Explain the most important changes in the threat landscape and the actions they justify.",
    ),
    (
        "11111111-1111-4111-8111-111111111102",
        "executive_security_summary",
        "Executive Security Summary",
        "A concise leadership briefing focused on business exposure, change, and decisions.",
        "executive_summary",
        "executive",
        "Translate material security developments into risk, exposure, and decision-ready actions.",
    ),
    (
        "11111111-1111-4111-8111-111111111103",
        "vulnerability_exploitation_review",
        "Vulnerability and Exploitation Review",
        "A review of vulnerabilities, exploitation evidence, affected technology, and validation priorities.",
        "vulnerability_review",
        "vulnerability_management",
        "Prioritize vulnerabilities and exploitation developments supported by the selected evidence.",
    ),
    (
        "11111111-1111-4111-8111-111111111104",
        "malware_campaign_review",
        "Malware and Campaign Review",
        "A campaign-oriented synthesis of malware, actors, techniques, and infrastructure.",
        "campaign_review",
        "soc",
        "Connect related campaign evidence and identify defensible monitoring priorities.",
    ),
    (
        "11111111-1111-4111-8111-111111111105",
        "technology_stack_exposure",
        "Technology Stack Exposure",
        "A company-context report emphasizing developments relevant to configured technologies and vendors.",
        "stack_exposure",
        "security_team",
        "Identify evidence relevant to the configured technology stack without assuming deployment or impact.",
    ),
    (
        "11111111-1111-4111-8111-111111111106",
        "ioc_infrastructure_summary",
        "IOC and Emerging Infrastructure Summary",
        "A sourced overview of notable observables and emerging malicious infrastructure.",
        "ioc_summary",
        "soc",
        "Summarize notable observables, their context, and cautious monitoring opportunities.",
    ),
    (
        "11111111-1111-4111-8111-111111111107",
        "custom_intelligence_report",
        "Custom Intelligence Report",
        "A flexible sourced intelligence report for a user-defined objective and audience.",
        "custom",
        "security_team",
        "Synthesize the selected evidence for the stated objective and audience.",
    ),
)


DEFAULT_SECTIONS = [
    {"key": "executive_summary", "title": "Executive Summary", "enabled": True},
    {"key": "scope_evidence", "title": "Scope and Evidence", "enabled": True},
    {"key": "key_developments", "title": "Key Developments", "enabled": True},
    {"key": "threat_landscape", "title": "Threat Landscape", "enabled": True},
    {"key": "vulnerabilities", "title": "Vulnerabilities and Exploitation", "enabled": True},
    {"key": "campaigns", "title": "Malware, Campaigns, and Infrastructure", "enabled": True},
    {"key": "organization_relevance", "title": "Organizational Relevance", "enabled": True},
    {"key": "recommended_actions", "title": "Recommended Actions", "enabled": True},
    {"key": "observables", "title": "Indicators and Observables", "enabled": True},
    {"key": "sources", "title": "Sources", "enabled": True},
]


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("builtin_key", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_type", sa.String(64), nullable=False, server_default="custom"),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="private"),
        sa.Column("audience", sa.String(64), nullable=False, server_default="security_team"),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("tone", sa.String(32), nullable=False, server_default="analytical"),
        sa.Column("detail_level", sa.String(16), nullable=False, server_default="standard"),
        sa.Column("use_company_context", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
        sa.Column("sections_json", sa.JSON(), nullable=False),
        sa.Column("default_filters_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("builtin_key"),
    )
    op.create_index("ix_report_templates_owner_user_id", "report_templates", ["owner_user_id"])
    op.create_index("ix_report_templates_visibility", "report_templates", ["visibility"])

    op.create_table(
        "report_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cadence", sa.String(16), nullable=False, server_default="weekly"),
        sa.Column("day_of_week", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_of_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hour", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("window_type", sa.String(32), nullable=False, server_default="previous_complete_week"),
        sa.Column("rolling_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
        sa.Column("delivery_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delivery_mode", sa.String(16), nullable=False, server_default="summary"),
        sa.Column("skip_empty", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("missed_run_policy", sa.String(16), nullable=False, server_default="latest"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["report_templates.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_report_schedules_template_id", ["template_id"]),
        ("ix_report_schedules_owner_user_id", ["owner_user_id"]),
        ("ix_report_schedules_enabled", ["enabled"]),
        ("ix_report_schedules_next_run_at", ["next_run_at"]),
    ):
        op.create_index(name, "report_schedules", columns)

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("report_type", sa.String(64), nullable=False, server_default="custom"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("trigger_source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("generation_stage", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("generation_key", sa.String(255), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("prompt_config_json", sa.JSON(), nullable=False),
        sa.Column("sections_config_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("included_source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded_source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("context_window_tokens", sa.Integer(), nullable=False, server_default="8192"),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generation_batches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("delivery_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delivery_mode", sa.String(16), nullable=False, server_default="summary"),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schedule_id"], ["report_schedules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["report_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_key"),
    )
    for name, columns in (
        ("ix_reports_template_id", ["template_id"]),
        ("ix_reports_schedule_id", ["schedule_id"]),
        ("ix_reports_owner_user_id", ["owner_user_id"]),
        ("ix_reports_status", ["status"]),
        ("ix_reports_period_start", ["period_start"]),
        ("ix_reports_period_end", ["period_end"]),
        ("ix_reports_generated_at", ["generated_at"]),
    ):
        op.create_index(name, "reports", columns)

    op.create_table(
        "report_source_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("citation_key", sa.String(16), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exclusion_reason", sa.String(64), nullable=True),
        sa.Column("title_snapshot", sa.Text(), nullable=False),
        sa.Column("feed_name_snapshot", sa.Text(), nullable=False),
        sa.Column("url_snapshot", sa.Text(), nullable=False),
        sa.Column("classification_snapshot", sa.String(64), nullable=True),
        sa.Column("relevance_score_snapshot", sa.Float(), nullable=True),
        sa.Column("relevance_label_snapshot", sa.String(16), nullable=True),
        sa.Column("published_at_snapshot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at_snapshot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tags_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("iocs_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "citation_key", name="uq_report_source_items_report_citation"),
        sa.UniqueConstraint("report_id", "item_id", name="uq_report_source_items_report_item"),
    )
    op.create_index("ix_report_source_items_report_id", "report_source_items", ["report_id"])
    op.create_index("ix_report_source_items_item_id", "report_source_items", ["item_id"])

    op.create_table(
        "report_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("section_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("body_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_points_json", sa.JSON(), nullable=False),
        sa.Column("citations_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "section_key", name="uq_report_sections_report_key"),
    )
    op.create_index("ix_report_sections_report_id", "report_sections", ["report_id"])

    op.add_column("ai_task_runs", sa.Column("report_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_ai_task_runs_report_id", "ai_task_runs", "reports", ["report_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_ai_task_runs_report_id", "ai_task_runs", ["report_id"])
    op.add_column("ai_usage_events", sa.Column("report_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_ai_usage_events_report_id", "ai_usage_events", "reports", ["report_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_ai_usage_events_report_id", "ai_usage_events", ["report_id"])

    for name, type_, default in (
        ("reporting_enabled", sa.Boolean(), sa.true()),
        ("report_context_window_tokens", sa.Integer(), "8192"),
        ("report_reserved_output_tokens", sa.Integer(), "1200"),
        ("report_source_token_cap", sa.Integer(), "700"),
        ("report_max_sources", sa.Integer(), "100"),
        ("report_max_model_calls", sa.Integer(), "20"),
        ("report_context_safety_percent", sa.Integer(), "15"),
    ):
        op.add_column("ai_settings", sa.Column(name, type_, nullable=False, server_default=default))

    template_table = sa.table(
        "report_templates",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("builtin_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("report_type", sa.String()),
        sa.column("visibility", sa.String()),
        sa.column("audience", sa.String()),
        sa.column("objective", sa.Text()),
        sa.column("tone", sa.String()),
        sa.column("detail_level", sa.String()),
        sa.column("use_company_context", sa.Boolean()),
        sa.column("sections_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
    )
    op.bulk_insert(
        template_table,
        [
            {
                "id": uuid.UUID(template_id),
                "builtin_key": key,
                "name": name,
                "description": description,
                "report_type": report_type,
                "visibility": "shared",
                "audience": audience,
                "objective": objective,
                "tone": "analytical",
                "detail_level": "standard",
                "use_company_context": True,
                "sections_json": DEFAULT_SECTIONS,
                "default_filters_json": {},
            }
            for template_id, key, name, description, report_type, audience, objective in BUILTIN_TEMPLATES
        ],
    )


def downgrade() -> None:
    for name in (
        "report_context_safety_percent",
        "report_max_model_calls",
        "report_max_sources",
        "report_source_token_cap",
        "report_reserved_output_tokens",
        "report_context_window_tokens",
        "reporting_enabled",
    ):
        op.drop_column("ai_settings", name)
    op.drop_index("ix_ai_usage_events_report_id", table_name="ai_usage_events")
    op.drop_constraint("fk_ai_usage_events_report_id", "ai_usage_events", type_="foreignkey")
    op.drop_column("ai_usage_events", "report_id")
    op.drop_index("ix_ai_task_runs_report_id", table_name="ai_task_runs")
    op.drop_constraint("fk_ai_task_runs_report_id", "ai_task_runs", type_="foreignkey")
    op.drop_column("ai_task_runs", "report_id")
    op.drop_table("report_sections")
    op.drop_table("report_source_items")
    op.drop_table("reports")
    op.drop_table("report_schedules")
    op.drop_table("report_templates")
