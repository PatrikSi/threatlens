"""Preserve successful enrichment provenance and disclose briefing fallbacks."""

from alembic import op
import sqlalchemy as sa


revision = "0100_ai_evidence_provenance"
down_revision = "0099_ai_workflow_history_pruning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_ai_enrichments",
        sa.Column("result_provenance_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ai_daily_briefs",
        sa.Column(
            "evidence_warnings_json", sa.JSON(), server_default="[]", nullable=False
        ),
    )
    # Historical source hashes can describe failed attempts, not retained output.
    # Do not infer successful provenance or queue billable regeneration here.


def downgrade() -> None:
    op.drop_column("ai_daily_briefs", "evidence_warnings_json")
    op.drop_column("item_ai_enrichments", "result_provenance_json")
