"""Attribute provider usage and enforce cross-process admission budgets."""
from alembic import op
import sqlalchemy as sa

revision = "0098_ai_provider_operations"
down_revision = "0097_ai_workflow_durability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("ai_settings", "ai_provider_configurations"):
        op.add_column(table, sa.Column("max_concurrent_requests", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("hourly_token_budget", sa.BigInteger(), nullable=False, server_default="0"))
    for column in (
        sa.Column("provider_id", sa.Uuid()),
        sa.Column("provider_version", sa.Integer()),
        sa.Column("provider_name", sa.String(120)),
        sa.Column("failure_category", sa.String(64)),
        sa.Column("provider_io_outcome", sa.String(32)),
    ):
        op.add_column("ai_usage_events", column)
    # Leave historical attribution unknown; the old model string cannot identify
    # which of several independent profiles actually paid for a request.
    op.create_index("ix_ai_usage_provider_created", "ai_usage_events", ["provider_id", "created_at"])
    op.create_table("ai_provider_budget_states", sa.Column("provider_key", sa.String(64), primary_key=True))
    op.create_table("ai_provider_budget_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("charged_tokens", sa.BigInteger()),
        sa.Column("outcome", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ai_budget_created", "ai_provider_budget_reservations", ["created_at"])
    op.create_index("ix_ai_budget_provider_created", "ai_provider_budget_reservations", ["provider_key", "created_at"])
    op.create_index("ix_ai_budget_provider_active", "ai_provider_budget_reservations", ["provider_key", "completed_at", "expires_at"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM ai_provider_budget_reservations WHERE completed_at IS NULL AND expires_at > now())")):
        raise RuntimeError("Wait for active provider admission leases to settle before downgrading.")
    for table in ("ai_settings", "ai_provider_configurations"):
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE max_concurrent_requests <> 0 OR hourly_token_budget <> 0)")):
            raise RuntimeError("Disable provider workload budgets before downgrading; older workers cannot enforce them.")
    op.drop_table("ai_provider_budget_reservations")
    op.drop_table("ai_provider_budget_states")
    op.drop_index("ix_ai_usage_provider_created", table_name="ai_usage_events")
    for column in ("provider_id", "provider_version", "provider_name", "failure_category", "provider_io_outcome"):
        op.drop_column("ai_usage_events", column)
    for table in ("ai_settings", "ai_provider_configurations"):
        op.drop_column(table, "hourly_token_budget")
        op.drop_column(table, "max_concurrent_requests")
