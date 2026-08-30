import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


ACTION_APPROVAL_STORED_STATUSES = frozenset(
    {"pending", "approved", "denied", "cancelled", "invalidated", "executed"}
)


class ActionApprovalRequest(Base):
    __tablename__ = "action_approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled', 'invalidated', 'executed')",
            name="ck_action_approval_requests_status",
        ),
        CheckConstraint(
            "target_revision >= 1",
            name="ck_action_approval_requests_target_revision",
        ),
        CheckConstraint(
            "action_definition_version >= 1",
            name="ck_action_approval_requests_definition_version",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_action_approval_requests_revision",
        ),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_action_approval_requests_payload_digest",
        ),
        CheckConstraint(
            "jsonb_typeof(target_snapshot) = 'object'",
            name="ck_action_approval_requests_target_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(payload_json) = 'object'",
            name="ck_action_approval_requests_payload",
        ),
        CheckConstraint(
            "length(request_reason) BETWEEN 10 AND 2000 "
            "AND btrim(request_reason) = request_reason",
            name="ck_action_approval_requests_request_reason",
        ),
        CheckConstraint(
            "decision_reason IS NULL OR (length(decision_reason) BETWEEN 3 AND 2000 "
            "AND btrim(decision_reason) = decision_reason)",
            name="ck_action_approval_requests_decision_reason",
        ),
        CheckConstraint(
            "cancel_reason IS NULL OR (length(cancel_reason) BETWEEN 3 AND 2000 "
            "AND btrim(cancel_reason) = cancel_reason)",
            name="ck_action_approval_requests_cancel_reason",
        ),
        CheckConstraint(
            "invalidation_reason IS NULL OR (length(invalidation_reason) BETWEEN 3 AND 96 "
            "AND btrim(invalidation_reason) = invalidation_reason)",
            name="ck_action_approval_requests_invalidation_reason",
        ),
        CheckConstraint(
            "decided_auth_method_snapshot IS NULL OR "
            "decided_auth_method_snapshot IN ('local', 'oidc')",
            name="ck_action_approval_requests_auth_method",
        ),
        CheckConstraint(
            "decided_mfa_method_snapshot IS NULL OR "
            "decided_mfa_method_snapshot IN ('totp', 'recovery_code', 'external')",
            name="ck_action_approval_requests_mfa_method",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "expires_at BETWEEN created_at + interval '5 minutes' "
            "AND created_at + interval '1 day'",
            name="ck_action_approval_requests_expiry",
        ),
        CheckConstraint(
            "decided_auth_token_version_snapshot IS NULL OR "
            "decided_auth_token_version_snapshot >= 0",
            name="ck_action_approval_requests_auth_token_version",
        ),
        CheckConstraint(
            "decided_at IS NULL OR (decided_at >= created_at AND decided_at < expires_at)",
            name="ck_action_approval_requests_decided_at",
        ),
        CheckConstraint(
            "cancelled_at IS NULL OR (cancelled_at >= created_at AND cancelled_at < expires_at)",
            name="ck_action_approval_requests_cancelled_at",
        ),
        CheckConstraint(
            "invalidated_at IS NULL OR (decided_at IS NOT NULL "
            "AND invalidated_at >= decided_at AND invalidated_at < expires_at)",
            name="ck_action_approval_requests_invalidated_at",
        ),
        CheckConstraint(
            "executed_at IS NULL OR (decided_at IS NOT NULL "
            "AND executed_at >= decided_at AND executed_at < expires_at)",
            name="ck_action_approval_requests_executed_at",
        ),
        CheckConstraint(
            "decided_by_user_id IS NULL OR requested_by_user_id IS NULL OR "
            "decided_by_user_id <> requested_by_user_id",
            name="ck_action_approval_requests_no_self_decision",
        ),
        CheckConstraint(
            "executed_by_user_id IS NULL OR requested_by_user_id IS NULL OR "
            "executed_by_user_id = requested_by_user_id",
            name="ck_action_approval_requests_requester_executes",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        Index(
            "ix_action_approval_requests_status_expiry",
            "status",
            "expires_at",
        ),
        Index(
            "ix_action_approval_requests_action_created",
            "action_type",
            "created_at",
        ),
        Index("ix_action_approval_requests_requester", "requested_by_user_id"),
        Index("ix_action_approval_requests_decider", "decided_by_user_id"),
        Index("ix_action_approval_requests_created", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action_type: Mapped[str] = mapped_column(String(96), nullable=False)
    action_label_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    audit_action_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    requester_permission_snapshot: Mapped[str] = mapped_column(
        String(96), nullable=False
    )
    approver_permission_snapshot: Mapped[str] = mapped_column(
        String(96), nullable=False
    )
    action_definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    target_snapshot: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    payload_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_email_snapshot: Mapped[str] = mapped_column(
        String(320), nullable=False
    )
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_auth_token_version_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    decided_auth_method_snapshot: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    decided_mfa_method_snapshot: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_by_principal_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    cancelled_from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cancelled_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    executed_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalidation_reason: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ActionExecutionReceipt(Base):
    __tablename__ = "action_execution_receipts"
    __table_args__ = (
        UniqueConstraint(
            "approval_request_id",
            name="uq_action_execution_receipts_approval_request",
        ),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_action_execution_receipts_payload_digest",
        ),
        CheckConstraint(
            "target_revision >= 1",
            name="ck_action_execution_receipts_target_revision",
        ),
        CheckConstraint(
            "result_schema_version >= 1",
            name="ck_action_execution_receipts_schema_version",
        ),
        CheckConstraint(
            "jsonb_typeof(result_json) = 'object'",
            name="ck_action_execution_receipts_result",
        ),
        Index("ix_action_execution_receipts_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("action_approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(96), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    requester_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    approver_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    executed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    executed_by_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    result_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    result_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ACTION_APPROVAL_STORED_STATUSES",
    "ActionApprovalRequest",
    "ActionExecutionReceipt",
]
