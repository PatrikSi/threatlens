"""Persist bounded article extraction section checkpoints."""

from alembic import op
import sqlalchemy as sa

revision = "0111_extraction_sections"
down_revision = "0110_indicator_review_lineage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "item_ai_enrichments",
        sa.Column("extraction_progress_json", sa.JSON(), nullable=True),
    )


def downgrade():
    retained = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM item_ai_enrichments "
            "WHERE COALESCE(json_typeof(extraction_progress_json), 'null') <> 'null')"
        )
    )
    if retained:
        raise RuntimeError(
            "Archive and explicitly clear extraction checkpoints before downgrade; paid section progress must not be discarded"
        )
    op.drop_column("item_ai_enrichments", "extraction_progress_json")
