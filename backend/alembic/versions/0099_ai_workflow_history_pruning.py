"""Fence new AI workflow history references during bounded lifecycle pruning."""
from alembic import op
import sqlalchemy as sa


revision = "0099_ai_workflow_history_pruning"
down_revision = "0098_ai_provider_operations"
branch_labels = None
depends_on = None


REFERENCES = (
    ("ai_workflow_dispatch", "ai_workflow_dispatches", "run_id"),
    ("ai_reprocess_member", "ai_reprocess_members", "parent_run_id"),
    ("ai_report_stage", "ai_report_stage_artifacts", "task_run_id"),
)


def upgrade() -> None:
    for name, table, column in REFERENCES:
        op.execute(
            f"CREATE TRIGGER trg_pruning_{name} BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_reference("
            f"'ai_task_runs', '{column}', '', 'changed')"
        )


def downgrade() -> None:
    connection = op.get_bind()
    # Prevent a pruning claim from appearing between this check and the DDL.
    connection.execute(sa.text("LOCK TABLE lifecycle_pruning_records IN SHARE ROW EXCLUSIVE MODE"))
    if connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM lifecycle_pruning_records WHERE dataset = 'ai_task_runs')"
    )):
        raise RuntimeError("Complete AI task history pruning before downgrading workflow reference guards.")
    for name, table, _ in REFERENCES:
        op.execute(f"DROP TRIGGER trg_pruning_{name} ON {table}")
