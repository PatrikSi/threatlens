"""Persist recoverable export jobs and encrypted bounded artifact chunks.

Revision ID: 0087_async_exports
Revises: 0086_classification_versions
"""
from alembic import op
import sqlalchemy as sa

revision = "0087_async_exports"
down_revision = "0086_classification_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("principal_type", sa.String(24), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_encrypted", sa.JSON(), nullable=False),
        sa.Column("authorization_encrypted", sa.JSON(), nullable=False),
        sa.Column("format", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer()),
        sa.Column("file_size", sa.BigInteger()),
        sa.Column("filename", sa.String(255)),
        sa.Column("media_type", sa.String(120)),
        sa.Column("source_encrypted", sa.JSON()),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("principal_type", "principal_id", "idempotency_key", name="uq_export_jobs_request"),
        sa.CheckConstraint("principal_type IN ('user', 'service_account')", name="ck_export_jobs_principal"),
        sa.CheckConstraint("status IN ('queued', 'running', 'ready', 'failed', 'cancelled', 'expired')", name="ck_export_jobs_status"),
        sa.CheckConstraint("attempts >= 0 AND reserved_bytes >= 0 AND completed_items >= 0", name="ck_export_jobs_bounds"),
    )
    op.create_index("ix_export_jobs_owner_created", "export_jobs", ["principal_type", "principal_id", "created_at", "id"])
    op.create_index("ix_export_jobs_dispatch", "export_jobs", ["status", "next_attempt_at", "lease_expires_at"])
    op.create_index("ix_export_jobs_expiry", "export_jobs", ["expires_at"])
    op.create_table(
        "export_job_chunks",
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("export_jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0 AND length(ciphertext) <= 500000", name="ck_export_job_chunks_bounds"),
    )


def downgrade():
    op.drop_table("export_job_chunks")
    op.drop_table("export_jobs")
