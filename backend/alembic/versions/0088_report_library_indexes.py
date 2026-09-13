"""Index report keyset navigation/title search and retained AI failures."""
from alembic import op
import sqlalchemy as sa

revision = "0088_report_library_indexes"
down_revision = "0087_async_exports"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_reports_created_id", "reports", ["created_at", "id"])
    op.create_index("ix_reports_title_search", "reports",
                    [sa.text("to_tsvector('simple'::regconfig, title)")], postgresql_using="gin")
    op.create_index("ix_ai_task_runs_failure_created", "ai_task_runs", ["created_at"],
                    postgresql_where=sa.text("status = 'error' OR error IS NOT NULL"))


def downgrade():
    op.drop_index("ix_ai_task_runs_failure_created", table_name="ai_task_runs")
    op.drop_index("ix_reports_title_search", table_name="reports")
    op.drop_index("ix_reports_created_id", table_name="reports")
