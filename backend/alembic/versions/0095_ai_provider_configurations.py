"""Add independent AI provider profiles without changing legacy configuration.

Revision ID: 0095_ai_provider_configurations
Revises: 0094_permission_history_pruning
"""

import sqlalchemy as sa
from alembic import op

revision = "0095_ai_provider_configurations"
down_revision = "0094_permission_history_pruning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_configurations",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(360), nullable=False, unique=True),
        sa.Column(
            "provider_type",
            sa.String(32),
            nullable=False,
            server_default="openai_compatible",
        ),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column(
            "max_completion_tokens", sa.Integer(), nullable=False, server_default="5000"
        ),
        sa.Column(
            "request_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
        sa.Column(
            "request_max_retries", sa.Integer(), nullable=False, server_default="3"
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("api_key_encrypted", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_ai_provider_version"),
    )
    op.create_table(
        "ai_provider_routing",
        sa.Column(
            "singleton_key",
            sa.Integer(),
            nullable=False,
            primary_key=True,
            server_default="1",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *(
            sa.Column(
                field,
                sa.Uuid(),
                sa.ForeignKey("ai_provider_configurations.id", ondelete="RESTRICT"),
            )
            for field in (
                "default_provider_id",
                "item_enrichment_provider_id",
                "daily_brief_provider_id",
                "report_provider_id",
            )
        ),
        sa.CheckConstraint(
            "singleton_key = 1", name="ck_ai_provider_routing_singleton"
        ),
        sa.CheckConstraint("version >= 1", name="ck_ai_provider_routing_version"),
    )
    op.execute(
        sa.text(
            "INSERT INTO ai_provider_routing (singleton_key, version) VALUES (1, 1)"
        )
    )


def downgrade() -> None:
    # An old binary cannot represent independent encrypted provider credentials.
    # Require explicit removal so downgrade never silently loses or reroutes them.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM ai_provider_configurations)")
    ):
        raise RuntimeError(
            "Clear AI provider routing and delete named providers before downgrading; legacy AI settings are preserved."
        )
    # Deleting an unassigned profile deliberately leaves task snapshots intact.
    # Old workers do not understand those snapshots and would use legacy routing.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ai_task_runs "
            "WHERE finished_at IS NULL "
            "AND status NOT IN ('ready', 'error', 'skipped') "
            "AND metadata_json #>> '{provider_selection,provider_id}' IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Finish or cancel outstanding named-provider AI tasks and wait for them "
            "to settle before downgrading; older workers cannot preserve their routing."
        )
    op.drop_table("ai_provider_routing")
    op.drop_table("ai_provider_configurations")
