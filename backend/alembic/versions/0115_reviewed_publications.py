"""Retain reviewed publication identities, evidence boundaries and withdrawals."""

from alembic import op
import sqlalchemy as sa

revision = "0115_reviewed_publications"
down_revision = "0114_hunt_worklist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indicator_publications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("marking", sa.String(16), nullable=False),
        sa.Column("distribution", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("indicator_count", sa.Integer(), nullable=False),
        sa.Column("withdrawn_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", "idempotency_key", name="uq_indicator_publication_request"),
    )
    op.create_index("ix_indicator_publication_team_created", "indicator_publications", ["team_id", "created_at", "id"])
    op.create_index("ix_indicator_publication_check", "indicator_publications", ["next_check_at", "id"])
    op.create_table(
        "indicator_publication_labels",
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("indicator_publications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("handling_label_id", sa.Uuid(), sa.ForeignKey("handling_labels.id", ondelete="RESTRICT"), primary_key=True),
    )
    op.create_table(
        "indicator_publication_sources",
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("indicator_publications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_id", sa.Uuid(), primary_key=True),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
    )


def downgrade() -> None:
    retained = op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM indicator_publications)"))
    if retained:
        raise RuntimeError("Reviewed publications must be explicitly archived before dropping their withdrawal and access history")
    op.drop_table("indicator_publication_sources")
    op.drop_table("indicator_publication_labels")
    op.drop_table("indicator_publications")
