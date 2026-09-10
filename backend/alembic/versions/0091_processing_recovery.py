"""Persist bounded processing dispatch and resumable targeted recovery."""

from alembic import op
import sqlalchemy as sa

revision = "0091_processing_recovery"
down_revision = "0090_lifecycle_scan_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_dispatch_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_feed_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_processing_dispatch_singleton"),
    )
    op.execute("INSERT INTO processing_dispatch_state (id) VALUES (1)")
    op.add_column(
        "items",
        sa.Column(
            "classification_required_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "items", sa.Column("tagging_pending_since_at", sa.DateTime(timezone=True))
    )
    op.execute(
        "UPDATE items SET tagging_pending_since_at = now() WHERE tagging_pending"
    )
    op.create_table(
        "processing_recovery_runs",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "principal_type", sa.String(length=24), nullable=False, primary_key=False
        ),
        sa.Column("principal_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "request_hash", sa.String(length=64), nullable=False, primary_key=False
        ),
        sa.Column(
            "authorization_encrypted", sa.JSON(), nullable=False, primary_key=False
        ),
        sa.Column("source_encrypted", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("total_count", sa.Integer(), nullable=False, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "version > 0 AND total_count BETWEEN 1 AND 100",
            name="ck_processing_recovery_bounds",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial','cancelled','failed')",
            name="ck_processing_recovery_status",
        ),
        sa.UniqueConstraint(
            "principal_type",
            "principal_id",
            "idempotency_key",
            name="uq_processing_recovery_request",
        ),
    )
    op.create_index(
        "ix_processing_recovery_owner",
        "processing_recovery_runs",
        ["principal_type", "principal_id", "created_at", "id"],
    )
    op.create_table(
        "processing_work",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=False,
        ),
        sa.Column(
            "feed_id",
            sa.Uuid(),
            sa.ForeignKey("feeds.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=False,
        ),
        sa.Column("stage", sa.String(length=24), nullable=False, primary_key=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column(
            "required_since_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("generation", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("attempts", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("claim_token", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            primary_key=False,
        ),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), nullable=True, primary_key=False
        ),
        sa.Column(
            "published_canary_at",
            sa.DateTime(timezone=True),
            nullable=True,
            primary_key=False,
        ),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
            primary_key=False,
        ),
        sa.Column("reason", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column(
            "recovery_run_id",
            sa.Uuid(),
            sa.ForeignKey("processing_recovery_runs.id", ondelete="SET NULL"),
            nullable=True,
            primary_key=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "generation > 0 AND version > 0 AND attempts >= 0",
            name="ck_processing_work_bounds",
        ),
        sa.CheckConstraint(
            "stage IN ('article','classification','ioc','tagging')",
            name="ck_processing_work_stage",
        ),
        sa.CheckConstraint(
            "status IN ('waiting','queued','running','retry_wait','succeeded','attention','cancelled')",
            name="ck_processing_work_status",
        ),
        sa.UniqueConstraint("item_id", "stage", name="uq_processing_work_item_stage"),
    )
    op.create_index(
        "ix_processing_work_dispatch",
        "processing_work",
        ["status", "next_retry_at", "lease_expires_at"],
    )
    op.create_index("ix_processing_work_feed", "processing_work", ["feed_id", "status"])
    op.create_table(
        "processing_recovery_items",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("processing_recovery_runs.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("stage", sa.String(length=24), nullable=False, primary_key=True),
        sa.Column(
            "work_id",
            sa.Uuid(),
            sa.ForeignKey("processing_work.id", ondelete="SET NULL"),
            nullable=True,
            primary_key=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("state", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("reason", sa.String(length=64), nullable=True, primary_key=False),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_processing_recovery_item_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("processing_dispatch_state")
    op.drop_table("processing_recovery_items")
    op.drop_table("processing_work")
    op.drop_table("processing_recovery_runs")
    op.drop_column("items", "tagging_pending_since_at")
    op.drop_column("items", "classification_required_at")
