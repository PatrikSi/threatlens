"""add integration instances for smtp

Revision ID: 0034_integrations_smtp
Revises: 0033_ai_task_duration_bigint
Create Date: 2026-07-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0034_integrations_smtp"
down_revision = "0033_ai_task_duration_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("system_key", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("integration_type", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("secret_json", sa.JSON(), nullable=True),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_test_duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_key", name="uq_integration_instances_system_key"),
    )
    op.create_index(op.f("ix_integration_instances_direction"), "integration_instances", ["direction"], unique=False)
    op.create_index(op.f("ix_integration_instances_integration_type"), "integration_instances", ["integration_type"], unique=False)

    op.create_table(
        "integration_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_integration_runs_integration_id"), "integration_runs", ["integration_id"], unique=False)
    op.create_index(op.f("ix_integration_runs_run_type"), "integration_runs", ["run_type"], unique=False)
    op.create_index(op.f("ix_integration_runs_started_at"), "integration_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_integration_runs_status"), "integration_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_integration_runs_status"), table_name="integration_runs")
    op.drop_index(op.f("ix_integration_runs_started_at"), table_name="integration_runs")
    op.drop_index(op.f("ix_integration_runs_run_type"), table_name="integration_runs")
    op.drop_index(op.f("ix_integration_runs_integration_id"), table_name="integration_runs")
    op.drop_table("integration_runs")
    op.drop_index(op.f("ix_integration_instances_integration_type"), table_name="integration_instances")
    op.drop_index(op.f("ix_integration_instances_direction"), table_name="integration_instances")
    op.drop_table("integration_instances")
