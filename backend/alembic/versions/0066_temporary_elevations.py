"""add bounded temporary elevation workflow

Revision ID: 0066_temporary_elevations
Revises: 0065_oidc_claim_mappings
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0066_temporary_elevations"
down_revision = "0065_oidc_claim_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column(
            "authorization_elevation_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_audit_logs_authorization_elevation_ids",
        "audit_logs",
        "jsonb_typeof(authorization_elevation_ids) = 'array' AND "
        "NOT jsonb_path_exists(authorization_elevation_ids, "
        "'$[*] ? (@.type() != \"string\")')",
    )
    op.create_index(
        "ix_audit_logs_authorization_elevation_ids",
        "audit_logs",
        ["authorization_elevation_ids"],
        postgresql_using="gin",
    )

    op.create_table(
        "governance_operation_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=96), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column(
            "response_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "response_schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(key_hash) = 64",
            name="ck_governance_operation_receipts_key_hash",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_governance_operation_receipts_fingerprint",
        ),
        sa.CheckConstraint(
            "http_status BETWEEN 200 AND 299",
            name="ck_governance_operation_receipts_http_status",
        ),
        sa.CheckConstraint(
            "response_schema_version >= 1",
            name="ck_governance_operation_receipts_schema_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "operation",
            "key_hash",
            name="uq_governance_operation_receipts_actor_operation_key",
        ),
    )
    op.create_index(
        "ix_governance_operation_receipts_resource",
        "governance_operation_receipts",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "ix_governance_operation_receipts_created",
        "governance_operation_receipts",
        ["created_at"],
    )

    op.create_table(
        "temporary_elevations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.Column("target_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column("role_key_snapshot", sa.String(length=64), nullable=False),
        sa.Column("role_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column("role_revision_snapshot", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("requested_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column("request_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("grant_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("closed_by_principal_type", sa.String(length=16), nullable=True),
        sa.Column("closed_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled', 'revoked')",
            name="ck_temporary_elevations_status",
        ),
        sa.CheckConstraint(
            "requested_duration_seconds BETWEEN 300 AND 86400",
            name="ck_temporary_elevations_duration",
        ),
        sa.CheckConstraint(
            "length(request_reason) BETWEEN 10 AND 2000 "
            "AND btrim(request_reason) = request_reason",
            name="ck_temporary_elevations_request_reason",
        ),
        sa.CheckConstraint(
            "decision_reason IS NULL OR (length(decision_reason) BETWEEN 3 AND 2000 "
            "AND btrim(decision_reason) = decision_reason)",
            name="ck_temporary_elevations_decision_reason",
        ),
        sa.CheckConstraint(
            "close_reason IS NULL OR (length(close_reason) BETWEEN 3 AND 2000 "
            "AND btrim(close_reason) = close_reason)",
            name="ck_temporary_elevations_close_reason",
        ),
        sa.CheckConstraint(
            "role_revision_snapshot >= 1",
            name="ck_temporary_elevations_role_revision",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_temporary_elevations_revision"),
        sa.CheckConstraint(
            "request_expires_at > created_at",
            name="ck_temporary_elevations_request_expiry",
        ),
        sa.CheckConstraint(
            "grant_expires_at IS NULL OR "
            "(grant_started_at IS NOT NULL AND grant_expires_at = grant_started_at + "
            "requested_duration_seconds * interval '1 second')",
            name="ck_temporary_elevations_grant_expiry",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR requested_by_user_id IS NULL OR "
            "decided_by_user_id <> requested_by_user_id",
            name="ck_temporary_elevations_no_self_decision",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> target_user_id",
            name="ck_temporary_elevations_no_target_decision",
        ),
        sa.CheckConstraint(
            "(closed_by_principal_type IS NULL AND closed_by_user_id IS NULL "
            "AND closed_by_email_snapshot IS NULL) OR "
            "(closed_by_principal_type = 'user' "
            "AND closed_by_email_snapshot IS NOT NULL) OR "
            "(closed_by_principal_type = 'system' AND closed_by_user_id IS NULL "
            "AND closed_by_email_snapshot IS NULL)",
            name="ck_temporary_elevations_close_actor",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by_user_id IS NULL "
            "AND decided_by_email_snapshot IS NULL AND decided_at IS NULL "
            "AND decision_reason IS NULL AND grant_started_at IS NULL "
            "AND grant_expires_at IS NULL AND closed_by_user_id IS NULL "
            "AND closed_by_principal_type IS NULL AND closed_at IS NULL "
            "AND close_reason IS NULL) OR "
            "(status = 'approved' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND grant_started_at IS NOT NULL AND grant_expires_at IS NOT NULL "
            "AND closed_by_user_id IS NULL AND closed_by_principal_type IS NULL "
            "AND closed_at IS NULL "
            "AND close_reason IS NULL) OR "
            "(status = 'denied' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND grant_started_at IS NULL AND grant_expires_at IS NULL "
            "AND closed_by_user_id IS NULL AND closed_by_principal_type IS NULL "
            "AND closed_at IS NULL "
            "AND close_reason IS NULL) OR "
            "(status = 'cancelled' AND decided_by_user_id IS NULL "
            "AND decided_by_email_snapshot IS NULL AND decided_at IS NULL "
            "AND decision_reason IS NULL "
            "AND grant_started_at IS NULL AND grant_expires_at IS NULL "
            "AND closed_by_principal_type IS NOT NULL AND closed_at IS NOT NULL "
            "AND close_reason IS NOT NULL) OR "
            "(status = 'revoked' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND grant_started_at IS NOT NULL AND grant_expires_at IS NOT NULL "
            "AND closed_by_principal_type IS NOT NULL AND closed_at IS NOT NULL "
            "AND close_reason IS NOT NULL)",
            name="ck_temporary_elevations_state",
        ),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_temporary_elevations_target_status_expiry",
        "temporary_elevations",
        ["target_user_id", "status", "grant_expires_at"],
    )
    op.create_index(
        "ix_temporary_elevations_status_request_expiry",
        "temporary_elevations",
        ["status", "request_expires_at"],
    )
    op.create_index("ix_temporary_elevations_role", "temporary_elevations", ["role_id"])
    op.create_index(
        "ix_temporary_elevations_requester",
        "temporary_elevations",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_temporary_elevations_decider",
        "temporary_elevations",
        ["decided_by_user_id"],
    )
    op.create_table(
        "temporary_elevation_permissions",
        sa.Column("elevation_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=96), nullable=False),
        sa.CheckConstraint(
            "length(permission) BETWEEN 3 AND 96 AND btrim(permission) = permission",
            name="ck_temporary_elevation_permissions_value",
        ),
        sa.ForeignKeyConstraint(
            ["elevation_id"], ["temporary_elevations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("elevation_id", "permission"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        "temporary_elevations": int(
            bind.scalar(sa.text("SELECT count(*) FROM temporary_elevations")) or 0
        ),
        "governance_operation_receipts": int(
            bind.scalar(sa.text("SELECT count(*) FROM governance_operation_receipts"))
            or 0
        ),
        "audit_logs(authorization_elevation_ids)": int(
            bind.scalar(
                sa.text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE jsonb_array_length(authorization_elevation_ids) > 0"
                )
            )
            or 0
        ),
    }
    populated = [f"{name}={count}" for name, count in counts.items() if count]
    if populated:
        raise RuntimeError(
            "Cannot downgrade temporary-elevation persistence while governance "
            f"history would be lost ({', '.join(populated)}). Export the "
            "elevation audit history, revoke active grants, remove the persisted "
            "rows, and retry the downgrade."
        )

    op.drop_table("temporary_elevation_permissions")
    op.drop_index("ix_temporary_elevations_decider", table_name="temporary_elevations")
    op.drop_index(
        "ix_temporary_elevations_requester", table_name="temporary_elevations"
    )
    op.drop_index("ix_temporary_elevations_role", table_name="temporary_elevations")
    op.drop_index(
        "ix_temporary_elevations_status_request_expiry",
        table_name="temporary_elevations",
    )
    op.drop_index(
        "ix_temporary_elevations_target_status_expiry",
        table_name="temporary_elevations",
    )
    op.drop_table("temporary_elevations")
    op.drop_index(
        "ix_governance_operation_receipts_created",
        table_name="governance_operation_receipts",
    )
    op.drop_index(
        "ix_governance_operation_receipts_resource",
        table_name="governance_operation_receipts",
    )
    op.drop_table("governance_operation_receipts")
    op.drop_index("ix_audit_logs_authorization_elevation_ids", table_name="audit_logs")
    op.drop_constraint(
        "ck_audit_logs_authorization_elevation_ids",
        "audit_logs",
        type_="check",
    )
    op.drop_column("audit_logs", "authorization_elevation_ids")
