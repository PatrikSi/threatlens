"""add reusable integration credential sources

Revision ID: 0038_integration_credentials
Revises: 0037_integration_metrics
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_integration_credentials"
down_revision = "0037_integration_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_instances",
        sa.Column("credential_source_integration_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_integration_instances_credential_source",
        "integration_instances",
        "integration_instances",
        ["credential_source_integration_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_integration_instances_credential_source_integration_id",
        "integration_instances",
        ["credential_source_integration_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_instances_credential_source_integration_id",
        table_name="integration_instances",
    )
    op.drop_constraint(
        "fk_integration_instances_credential_source",
        "integration_instances",
        type_="foreignkey",
    )
    op.drop_column("integration_instances", "credential_source_integration_id")
