"""add collaborative investigation collections

Revision ID: 0058_investigations
Revises: 0057_system_operations
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0058_investigations"
down_revision = "0057_system_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"),
        sa.Column("disposition", sa.String(length=64), nullable=True),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('open', 'monitoring', 'closed', 'archived')",
            name="ck_investigations_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_investigations_severity",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'team')",
            name="ck_investigations_visibility",
        ),
        sa.CheckConstraint("version >= 1", name="ck_investigations_version"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_investigations_status", ["status"]),
        ("ix_investigations_severity", ["severity"]),
        ("ix_investigations_visibility", ["visibility"]),
        ("ix_investigations_assignee_user_id", ["assignee_user_id"]),
        ("ix_investigations_created_by_user_id", ["created_by_user_id"]),
        ("ix_investigations_status_updated_at", ["status", "updated_at"]),
        ("ix_investigations_assignee_status", ["assignee_user_id", "status"]),
    ):
        op.create_index(name, "investigations", columns)

    op.create_table(
        "investigation_members",
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')",
            name="ck_investigation_members_role",
        ),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("investigation_id", "user_id", name="pk_investigation_members"),
    )
    op.create_index("ix_investigation_members_user_id", "investigation_members", ["user_id"])
    op.create_index(
        "ix_investigation_members_investigation_role",
        "investigation_members",
        ["investigation_id", "role"],
    )

    op.create_table(
        "investigation_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("title_snapshot", sa.String(length=512), nullable=False),
        sa.Column("description_snapshot", sa.Text(), nullable=True),
        sa.Column("url_snapshot", sa.Text(), nullable=True),
        sa.Column(
            "metadata_snapshot_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "source_type IN ('item', 'ioc', 'report', 'alert_occurrence')",
            name="ck_investigation_evidence_source_type",
        ),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "investigation_id",
            "source_type",
            "source_id",
            name="uq_investigation_evidence_source",
        ),
    )
    op.create_index(
        "ix_investigation_evidence_investigation_id",
        "investigation_evidence",
        ["investigation_id"],
    )
    op.create_index(
        "ix_investigation_evidence_source",
        "investigation_evidence",
        ["source_type", "source_id"],
    )

    op.create_table(
        "investigation_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_investigation_notes_version"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investigation_notes_investigation_created",
        "investigation_notes",
        ["investigation_id", "created_at"],
    )

    op.create_table(
        "investigation_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investigation_activities_investigation_created",
        "investigation_activities",
        ["investigation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investigation_activities_investigation_created",
        table_name="investigation_activities",
    )
    op.drop_table("investigation_activities")
    op.drop_index(
        "ix_investigation_notes_investigation_created",
        table_name="investigation_notes",
    )
    op.drop_table("investigation_notes")
    op.drop_index("ix_investigation_evidence_source", table_name="investigation_evidence")
    op.drop_index("ix_investigation_evidence_investigation_id", table_name="investigation_evidence")
    op.drop_table("investigation_evidence")
    op.drop_index(
        "ix_investigation_members_investigation_role",
        table_name="investigation_members",
    )
    op.drop_index("ix_investigation_members_user_id", table_name="investigation_members")
    op.drop_table("investigation_members")
    for name in (
        "ix_investigations_assignee_status",
        "ix_investigations_status_updated_at",
        "ix_investigations_created_by_user_id",
        "ix_investigations_assignee_user_id",
        "ix_investigations_visibility",
        "ix_investigations_severity",
        "ix_investigations_status",
    ):
        op.drop_index(name, table_name="investigations")
    op.drop_table("investigations")
