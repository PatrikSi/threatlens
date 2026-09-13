"""Require consumer progress before repeating queued export publications."""

from alembic import op
import sqlalchemy as sa


revision = "0101_export_dispatch_progress"
down_revision = "0100_ai_evidence_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("export_jobs", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("export_jobs", sa.Column("published_canary_at", sa.DateTime(timezone=True), nullable=True))
    # Legacy queued jobs receive at most one fresh publication reservation. Do
    # not guess that they were delivered or expire legitimately waiting work.


def downgrade() -> None:
    op.drop_column("export_jobs", "published_canary_at")
    op.drop_column("export_jobs", "published_at")
