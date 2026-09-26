"""Persist bounded article extraction section checkpoints."""

from alembic import op
import sqlalchemy as sa

revision = "0111_extraction_sections"
down_revision = "0110_indicator_review_lineage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("item_ai_enrichments", sa.Column("extraction_progress_json", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("item_ai_enrichments", "extraction_progress_json")
