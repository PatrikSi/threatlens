"""add registered sensitive action approvals

Revision ID: 0067_action_approvals
Revises: 0066_temporary_elevations
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0067_action_approvals"
down_revision = "0066_temporary_elevations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_governance_operation_receipts_http_status",
        "governance_operation_receipts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_governance_operation_receipts_http_status",
        "governance_operation_receipts",
        "http_status BETWEEN 200 AND 499",
    )
    op.create_table(
        "action_approval_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=96), nullable=False),
        sa.Column("action_label_snapshot", sa.String(length=160), nullable=False),
        sa.Column("audit_action_snapshot", sa.String(length=160), nullable=False),
        sa.Column(
            "requester_permission_snapshot", sa.String(length=96), nullable=False
        ),
        sa.Column("approver_permission_snapshot", sa.String(length=96), nullable=False),
        sa.Column("action_definition_version", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column(
            "target_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_auth_token_version_snapshot", sa.Integer(), nullable=True),
        sa.Column("decided_auth_method_snapshot", sa.String(length=16), nullable=True),
        sa.Column("decided_mfa_method_snapshot", sa.String(length=32), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("cancelled_by_principal_type", sa.String(length=16), nullable=True),
        sa.Column("cancelled_from_status", sa.String(length=16), nullable=True),
        sa.Column("cancelled_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("executed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("executed_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=96), nullable=True),
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
            "status IN ('pending', 'approved', 'denied', 'cancelled', 'invalidated', 'executed')",
            name="ck_action_approval_requests_status",
        ),
        sa.CheckConstraint(
            "target_revision >= 1",
            name="ck_action_approval_requests_target_revision",
        ),
        sa.CheckConstraint(
            "action_definition_version >= 1",
            name="ck_action_approval_requests_definition_version",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_action_approval_requests_revision",
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_action_approval_requests_payload_digest",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(target_snapshot) = 'object'",
            name="ck_action_approval_requests_target_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_json) = 'object'",
            name="ck_action_approval_requests_payload",
        ),
        sa.CheckConstraint(
            "length(request_reason) BETWEEN 10 AND 2000 "
            "AND btrim(request_reason) = request_reason",
            name="ck_action_approval_requests_request_reason",
        ),
        sa.CheckConstraint(
            "decision_reason IS NULL OR (length(decision_reason) BETWEEN 3 AND 2000 "
            "AND btrim(decision_reason) = decision_reason)",
            name="ck_action_approval_requests_decision_reason",
        ),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR (length(cancel_reason) BETWEEN 3 AND 2000 "
            "AND btrim(cancel_reason) = cancel_reason)",
            name="ck_action_approval_requests_cancel_reason",
        ),
        sa.CheckConstraint(
            "invalidation_reason IS NULL OR (length(invalidation_reason) BETWEEN 3 AND 96 "
            "AND btrim(invalidation_reason) = invalidation_reason)",
            name="ck_action_approval_requests_invalidation_reason",
        ),
        sa.CheckConstraint(
            "decided_auth_method_snapshot IS NULL OR "
            "decided_auth_method_snapshot IN ('local', 'oidc')",
            name="ck_action_approval_requests_auth_method",
        ),
        sa.CheckConstraint(
            "decided_mfa_method_snapshot IS NULL OR "
            "decided_mfa_method_snapshot IN ('totp', 'recovery_code', 'external')",
            name="ck_action_approval_requests_mfa_method",
        ),
        sa.CheckConstraint(
            "(decided_by_email_snapshot IS NULL AND decided_at IS NULL "
            "AND decision_reason IS NULL "
            "AND decided_auth_token_version_snapshot IS NULL "
            "AND decided_auth_method_snapshot IS NULL "
            "AND decided_mfa_method_snapshot IS NULL) OR "
            "(decided_by_email_snapshot IS NOT NULL AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND decided_auth_token_version_snapshot IS NOT NULL "
            "AND decided_auth_method_snapshot IS NOT NULL)",
            name="ck_action_approval_requests_decision_evidence",
        ),
        sa.CheckConstraint(
            "expires_at BETWEEN created_at + interval '5 minutes' "
            "AND created_at + interval '1 day'",
            name="ck_action_approval_requests_expiry",
        ),
        sa.CheckConstraint(
            "decided_auth_token_version_snapshot IS NULL OR "
            "decided_auth_token_version_snapshot >= 0",
            name="ck_action_approval_requests_auth_token_version",
        ),
        sa.CheckConstraint(
            "decided_at IS NULL OR (decided_at >= created_at AND decided_at < expires_at)",
            name="ck_action_approval_requests_decided_at",
        ),
        sa.CheckConstraint(
            "cancelled_at IS NULL OR (cancelled_at >= created_at AND cancelled_at < expires_at)",
            name="ck_action_approval_requests_cancelled_at",
        ),
        sa.CheckConstraint(
            "invalidated_at IS NULL OR (decided_at IS NOT NULL "
            "AND invalidated_at >= decided_at AND invalidated_at < expires_at)",
            name="ck_action_approval_requests_invalidated_at",
        ),
        sa.CheckConstraint(
            "executed_at IS NULL OR (decided_at IS NOT NULL "
            "AND executed_at >= decided_at AND executed_at < expires_at)",
            name="ck_action_approval_requests_executed_at",
        ),
        sa.CheckConstraint(
            "decided_by_user_id IS NULL OR requested_by_user_id IS NULL OR "
            "decided_by_user_id <> requested_by_user_id",
            name="ck_action_approval_requests_no_self_decision",
        ),
        sa.CheckConstraint(
            "executed_by_user_id IS NULL OR requested_by_user_id IS NULL OR "
            "executed_by_user_id = requested_by_user_id",
            name="ck_action_approval_requests_requester_executes",
        ),
        sa.CheckConstraint(
            "(cancelled_by_principal_type IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancelled_by_email_snapshot IS NULL "
            "AND cancelled_from_status IS NULL) OR "
            "(cancelled_by_principal_type = 'user' "
            "AND cancelled_by_email_snapshot IS NOT NULL "
            "AND cancelled_from_status IN ('pending', 'approved')) OR "
            "(cancelled_by_principal_type = 'system' AND cancelled_by_user_id IS NULL "
            "AND cancelled_by_email_snapshot IS NULL "
            "AND cancelled_from_status IN ('pending', 'approved'))",
            name="ck_action_approval_requests_cancel_actor",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by_email_snapshot IS NULL "
            "AND decided_by_user_id IS NULL "
            "AND decided_at IS NULL AND decision_reason IS NULL "
            "AND cancelled_by_principal_type IS NULL AND cancelled_at IS NULL "
            "AND cancel_reason IS NULL AND cancelled_from_status IS NULL "
            "AND executed_by_user_id IS NULL AND executed_by_email_snapshot IS NULL "
            "AND executed_at IS NULL AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL) OR "
            "(status = 'approved' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL AND decision_reason IS NOT NULL "
            "AND decided_auth_token_version_snapshot IS NOT NULL "
            "AND decided_auth_method_snapshot IS NOT NULL "
            "AND cancelled_by_principal_type IS NULL AND cancelled_at IS NULL "
            "AND cancel_reason IS NULL AND cancelled_from_status IS NULL "
            "AND executed_by_user_id IS NULL AND executed_by_email_snapshot IS NULL "
            "AND executed_at IS NULL AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL) OR "
            "(status = 'denied' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL AND decision_reason IS NOT NULL "
            "AND decided_auth_token_version_snapshot IS NOT NULL "
            "AND decided_auth_method_snapshot IS NOT NULL "
            "AND cancelled_by_principal_type IS NULL AND cancelled_at IS NULL "
            "AND cancel_reason IS NULL AND cancelled_from_status IS NULL "
            "AND executed_by_user_id IS NULL AND executed_by_email_snapshot IS NULL "
            "AND executed_at IS NULL AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL) OR "
            "(status = 'cancelled' AND cancelled_by_principal_type IS NOT NULL "
            "AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
            "AND cancelled_from_status IN ('pending', 'approved') "
            "AND ((cancelled_from_status = 'pending' "
            "AND decided_by_email_snapshot IS NULL) OR "
            "(cancelled_from_status = 'approved' "
            "AND decided_by_email_snapshot IS NOT NULL)) "
            "AND executed_by_user_id IS NULL AND executed_by_email_snapshot IS NULL "
            "AND executed_at IS NULL "
            "AND invalidated_at IS NULL AND invalidation_reason IS NULL) OR "
            "(status = 'invalidated' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL AND decision_reason IS NOT NULL "
            "AND decided_auth_token_version_snapshot IS NOT NULL "
            "AND decided_auth_method_snapshot IS NOT NULL "
            "AND cancelled_by_principal_type IS NULL AND cancelled_at IS NULL "
            "AND cancel_reason IS NULL AND cancelled_from_status IS NULL "
            "AND executed_by_user_id IS NULL AND executed_by_email_snapshot IS NULL "
            "AND executed_at IS NULL AND invalidated_at IS NOT NULL "
            "AND invalidation_reason IS NOT NULL) OR "
            "(status = 'executed' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL AND decision_reason IS NOT NULL "
            "AND decided_auth_token_version_snapshot IS NOT NULL "
            "AND decided_auth_method_snapshot IS NOT NULL "
            "AND cancelled_by_principal_type IS NULL AND cancelled_at IS NULL "
            "AND cancel_reason IS NULL AND cancelled_from_status IS NULL "
            "AND executed_by_email_snapshot IS NOT NULL "
            "AND executed_at IS NOT NULL AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL)",
            name="ck_action_approval_requests_state",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["executed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_action_approval_requests_status_expiry",
        "action_approval_requests",
        ["status", "expires_at"],
    )
    op.create_index(
        "ix_action_approval_requests_action_created",
        "action_approval_requests",
        ["action_type", "created_at"],
    )
    op.create_index(
        "ix_action_approval_requests_requester",
        "action_approval_requests",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_action_approval_requests_decider",
        "action_approval_requests",
        ["decided_by_user_id"],
    )
    op.create_index(
        "ix_action_approval_requests_created",
        "action_approval_requests",
        ["created_at", "id"],
    )

    op.create_table(
        "action_execution_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=96), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=True),
        sa.Column("requester_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("approver_user_id", sa.Uuid(), nullable=True),
        sa.Column("approver_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("executed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("executed_by_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "result_schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_action_execution_receipts_payload_digest",
        ),
        sa.CheckConstraint(
            "target_revision >= 1",
            name="ck_action_execution_receipts_target_revision",
        ),
        sa.CheckConstraint(
            "result_schema_version >= 1",
            name="ck_action_execution_receipts_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_json) = 'object'",
            name="ck_action_execution_receipts_result",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["action_approval_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_request_id",
            name="uq_action_execution_receipts_approval_request",
        ),
    )
    op.create_index(
        "ix_action_execution_receipts_created",
        "action_execution_receipts",
        ["created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION validate_action_execution_receipt()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        DECLARE
          approval action_approval_requests%ROWTYPE;
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'execution receipts are immutable'
              USING ERRCODE = '23514',
                    CONSTRAINT = 'ck_action_execution_receipts_immutable';
          END IF;
          SELECT * INTO approval
          FROM action_approval_requests
          WHERE id = NEW.approval_request_id
          FOR KEY SHARE;
          IF NOT FOUND OR approval.status <> 'executed'
             OR NEW.action_type IS DISTINCT FROM approval.action_type
             OR NEW.target_type IS DISTINCT FROM approval.target_type
             OR NEW.target_id IS DISTINCT FROM approval.target_id
             OR NEW.target_revision IS DISTINCT FROM approval.target_revision
             OR NEW.payload_digest IS DISTINCT FROM approval.payload_digest
             OR NEW.requester_user_id IS DISTINCT FROM approval.requested_by_user_id
             OR NEW.requester_email_snapshot IS DISTINCT FROM approval.requested_by_email_snapshot
             OR NEW.approver_user_id IS DISTINCT FROM approval.decided_by_user_id
             OR NEW.approver_email_snapshot IS DISTINCT FROM approval.decided_by_email_snapshot
             OR NEW.executed_by_user_id IS DISTINCT FROM approval.executed_by_user_id
             OR NEW.executed_by_email_snapshot IS DISTINCT FROM approval.executed_by_email_snapshot
          THEN
            RAISE EXCEPTION 'execution receipt does not match its executed approval'
              USING ERRCODE = '23514',
                    CONSTRAINT = 'ck_action_execution_receipts_matches_approval';
          END IF;
          RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_action_execution_receipt_validate
        AFTER INSERT OR UPDATE ON action_execution_receipts
        NOT DEFERRABLE
        FOR EACH ROW EXECUTE FUNCTION validate_action_execution_receipt()
        """
    )
    op.add_column(
        "audit_logs",
        sa.Column("authorization_approval_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("execution_receipt_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_audit_logs_authorization_approval",
        "audit_logs",
        ["authorization_approval_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_execution_receipt",
        "audit_logs",
        ["execution_receipt_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    error_receipt_count = int(
        bind.scalar(
            sa.text(
                "SELECT count(*) FROM governance_operation_receipts "
                "WHERE http_status >= 400"
            )
        )
        or 0
    )
    request_count = int(
        bind.scalar(sa.text("SELECT count(*) FROM action_approval_requests")) or 0
    )
    receipt_count = int(
        bind.scalar(sa.text("SELECT count(*) FROM action_execution_receipts")) or 0
    )
    audit_count = int(
        bind.scalar(
            sa.text(
                "SELECT count(*) FROM audit_logs "
                "WHERE authorization_approval_id IS NOT NULL "
                "OR execution_receipt_id IS NOT NULL"
            )
        )
        or 0
    )
    if request_count or receipt_count or audit_count or error_receipt_count:
        raise RuntimeError(
            "Cannot downgrade action-approval persistence while governance history "
            "would be lost "
            f"(requests={request_count}, receipts={receipt_count}, audit_rows={audit_count}, "
            f"error_operation_receipts={error_receipt_count}). "
            "Export the action-approval audit history, remove the persisted rows, and retry."
        )

    op.drop_index("ix_audit_logs_execution_receipt", table_name="audit_logs")
    op.drop_index("ix_audit_logs_authorization_approval", table_name="audit_logs")
    op.drop_column("audit_logs", "execution_receipt_id")
    op.drop_column("audit_logs", "authorization_approval_id")

    op.drop_index(
        "ix_action_execution_receipts_created",
        table_name="action_execution_receipts",
    )
    op.execute(
        "DROP TRIGGER trg_action_execution_receipt_validate "
        "ON action_execution_receipts"
    )
    op.execute("DROP FUNCTION validate_action_execution_receipt()")
    op.drop_table("action_execution_receipts")
    op.drop_index(
        "ix_action_approval_requests_created",
        table_name="action_approval_requests",
    )
    op.drop_index(
        "ix_action_approval_requests_decider",
        table_name="action_approval_requests",
    )
    op.drop_index(
        "ix_action_approval_requests_requester",
        table_name="action_approval_requests",
    )
    op.drop_index(
        "ix_action_approval_requests_action_created",
        table_name="action_approval_requests",
    )
    op.drop_index(
        "ix_action_approval_requests_status_expiry",
        table_name="action_approval_requests",
    )
    op.drop_table("action_approval_requests")
    op.drop_constraint(
        "ck_governance_operation_receipts_http_status",
        "governance_operation_receipts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_governance_operation_receipts_http_status",
        "governance_operation_receipts",
        "http_status BETWEEN 200 AND 299",
    )
