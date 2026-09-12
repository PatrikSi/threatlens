"""Add explicit AI request capabilities and optional model limits.

Revision ID: 0096_ai_provider_capabilities
Revises: 0095_ai_provider_configurations
"""

from alembic import op
import sqlalchemy as sa

revision = "0096_ai_provider_capabilities"
down_revision = "0095_ai_provider_configurations"
branch_labels = None
depends_on = None

TABLES = ("ai_settings", "ai_provider_configurations")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("request_dialect", sa.String(32), nullable=False, server_default="chat_completions"))
        op.add_column(table, sa.Column("reasoning_effort", sa.String(16), nullable=True))
        op.add_column(table, sa.Column("structured_output_mode", sa.String(16), nullable=False, server_default="off"))
        op.add_column(table, sa.Column("model_context_window_tokens", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("model_max_output_tokens", sa.Integer(), nullable=True))
        op.alter_column(table, "temperature", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    # An older binary cannot preserve these wire settings or model guardrails.
    connection = op.get_bind()
    for table in TABLES:
        configured = connection.scalar(sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {table} WHERE request_dialect <> 'chat_completions' "
            "OR reasoning_effort IS NOT NULL OR structured_output_mode <> 'off' "
            "OR model_context_window_tokens IS NOT NULL OR model_max_output_tokens IS NOT NULL "
            "OR temperature IS NULL)"
        ))
        if configured:
            raise RuntimeError("Restore legacy AI capabilities and resolve queued AI work before downgrading 0096.")
    for table in TABLES:
        op.alter_column(table, "temperature", existing_type=sa.Float(), nullable=False)
        for column in (
            "model_max_output_tokens", "model_context_window_tokens", "structured_output_mode",
            "reasoning_effort", "request_dialect",
        ):
            op.drop_column(table, column)
