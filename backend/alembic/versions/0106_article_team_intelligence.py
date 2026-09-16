"""Add opt-in shared extraction and versioned team article assessments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0106_article_team_intelligence"
down_revision = "0105_workspace_enforcement"
branch_labels = None
depends_on = None


def _result_columns() -> list[sa.Column]:
    return [
        sa.Column("result_context_version", sa.Integer()),
        sa.Column("result_source_version", sa.BigInteger()),
        sa.Column("result_article_id", sa.Uuid()),
        sa.Column("result_article_retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("result_source_encrypted", postgresql.JSONB()),
        sa.Column("result_json", postgresql.JSONB()),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
    ]


def upgrade() -> None:
    for name in ("structured_extraction_enabled", "hunt_suggestions_enabled"):
        op.add_column("ai_settings", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("item_ai_enrichments", sa.Column("structured_extraction_json", sa.JSON()))
    op.create_table(
        "team_ai_contexts",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("technology_stack", postgresql.JSONB(), nullable=False),
        sa.Column("priorities", postgresql.JSONB(), nullable=False),
        sa.Column("available_telemetry", postgresql.JSONB(), nullable=False),
        sa.Column("relevance_criteria", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_team_ai_contexts_version"),
    )
    op.create_table(
        "team_item_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("task_run_id", sa.Uuid(), sa.ForeignKey("ai_task_runs.id", ondelete="SET NULL")),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.Uuid()),
        sa.Column("article_retrieved_at", sa.DateTime(timezone=True)),
        *_result_columns(),
        sa.Column("principal_type", sa.String(16), nullable=False, server_default="user"),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_encrypted", postgresql.JSONB(), nullable=False),
        sa.Column("source_encrypted", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", "item_id", name="uq_team_item_assessments_team_item"),
        sa.CheckConstraint("version >= 1", name="ck_team_item_assessments_version"),
        sa.CheckConstraint("principal_type = 'user'", name="ck_team_item_assessments_principal"),
    )
    for name in ("item_id", "task_run_id"):
        op.create_index(f"ix_team_item_assessments_{name}", "team_item_assessments", [name])
    op.execute(
        "CREATE TRIGGER trg_pruning_team_assessment BEFORE INSERT OR UPDATE ON team_item_assessments "
        "FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_reference('ai_task_runs', 'task_run_id', '', 'changed')"
    )
    columns = _result_columns()
    for column in columns:
        if column.name in ("result_context_version", "result_source_version", "result_json"):
            column.nullable = False
    op.create_table(
        "team_assessment_revisions",
        sa.Column("assessment_id", sa.Uuid(), sa.ForeignKey("team_item_assessments.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        *columns,
        sa.Column("change_kind", sa.String(32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_team_assessment_revisions_version"),
    )


def downgrade() -> None:
    # Enforce the offline downgrade requirement before checking retained data.
    # A producer cannot insert its first result between this check and DROP.
    op.execute(
        "LOCK TABLE team_ai_contexts, team_item_assessments, team_assessment_revisions, "
        "item_ai_enrichments, ai_settings, ai_task_runs, ai_usage_events, ai_provider_attempt_receipts "
        "IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    populated = op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM team_ai_contexts)
          OR EXISTS (SELECT 1 FROM team_item_assessments)
          OR EXISTS (SELECT 1 FROM item_ai_enrichments WHERE structured_extraction_json IS NOT NULL)
          OR EXISTS (SELECT 1 FROM ai_settings WHERE structured_extraction_enabled OR hunt_suggestions_enabled)
          OR EXISTS (SELECT 1 FROM ai_task_runs WHERE task_type = 'team_assessment')
          OR EXISTS (SELECT 1 FROM ai_usage_events WHERE feature_type = 'team_assessment')
          OR EXISTS (SELECT 1 FROM ai_provider_attempt_receipts WHERE feature_type = 'team_assessment')
    """))
    if populated:
        raise RuntimeError(
            "Cannot downgrade article/team intelligence while feature settings, results or task history remain. "
            "Stop AI producers and workers, back up the database, and explicitly retire or export this data before downgrading."
        )
    op.drop_table("team_assessment_revisions")
    op.drop_table("team_item_assessments")
    op.drop_table("team_ai_contexts")
    op.drop_column("item_ai_enrichments", "structured_extraction_json")
    op.drop_column("ai_settings", "hunt_suggestions_enabled")
    op.drop_column("ai_settings", "structured_extraction_enabled")
