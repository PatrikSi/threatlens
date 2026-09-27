"""Separate reconciliation attempts from successful progress and index due work."""

from alembic import op
import sqlalchemy as sa

revision = "0124_reconciliation_progress"
down_revision = "0123_item_identity_indexes"
branch_labels = depends_on = None


def upgrade():
    op.add_column("publication_consumers", sa.Column("last_reconcile_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("publication_consumers", sa.Column("reconciliation_error_at", sa.DateTime(timezone=True)))
    op.add_column("publication_consumers", sa.Column("reconciliation_error_code", sa.String(64)))
    # Historical timestamps prove an attempt, not successful reconciliation.
    op.execute(sa.text("UPDATE publication_consumers SET last_reconcile_attempt_at=last_reconciled_at, last_reconciled_at=NULL"))
    op.drop_index("ix_publication_consumer_reconcile", table_name="publication_consumers")
    op.create_index("ix_publication_consumer_reconcile", "publication_consumers", ["last_reconcile_attempt_at", "id"])
    op.create_index("ix_automation_execution_current_due", "automation_executions", ["next_check_at", "id"],
                    postgresql_where=sa.text("policy_state = 'current'"))


def downgrade():
    op.drop_index("ix_automation_execution_current_due", table_name="automation_executions")
    op.drop_index("ix_publication_consumer_reconcile", table_name="publication_consumers")
    op.create_index("ix_publication_consumer_reconcile", "publication_consumers", ["last_reconciled_at", "id"])
    op.drop_column("publication_consumers", "reconciliation_error_code")
    op.drop_column("publication_consumers", "reconciliation_error_at")
    op.drop_column("publication_consumers", "last_reconcile_attempt_at")
