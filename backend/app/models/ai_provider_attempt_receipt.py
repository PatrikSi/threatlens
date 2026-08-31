import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


AI_PROVIDER_ATTEMPT_STATES = (
    "reserved",
    "voided",
    "failed",
    "succeeded",
    "ambiguous",
)
AI_PROVIDER_IO_OUTCOMES = (
    "reserved",
    "not_sent",
    "response_received",
    "ambiguous",
)
AI_PROVIDER_DATA_POLICY_MODES = (
    "disabled",
    "audit",
    "enforced",
    "bypass",
)
AI_PROVIDER_RECONCILIATION_ACTIONS = (
    "confirmed_not_sent",
    "acknowledged_may_have_sent",
)


class AIProviderAttemptReceipt(Base):
    __tablename__ = "ai_provider_attempt_receipts"
    __table_args__ = (
        CheckConstraint(
            "attempt_number >= 1 AND max_attempts >= 1 "
            "AND attempt_number <= max_attempts",
            name="ck_ai_provider_attempt_receipts_attempt_bounds",
        ),
        CheckConstraint(
            "requested_max_tokens >= 1 AND "
            "(next_max_tokens IS NULL OR next_max_tokens >= 1)",
            name="ck_ai_provider_attempt_receipts_token_bounds",
        ),
        CheckConstraint(
            "iam_revision >= 1",
            name="ck_ai_provider_attempt_receipts_iam_revision",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_ai_provider_attempt_receipts_revision",
        ),
        CheckConstraint(
            "reservation_generation >= 1",
            name="ck_ai_provider_attempt_receipts_reservation_generation",
        ),
        CheckConstraint(
            "pre_io_failure_count >= 0 AND "
            "((pre_io_failure_count = 0 AND last_pre_io_failure_at IS NULL) OR "
            "(pre_io_failure_count >= 1 AND last_pre_io_failure_at IS NOT NULL)) "
            "AND ((state = 'voided' "
            "AND reservation_generation = pre_io_failure_count) OR "
            "(state <> 'voided' "
            "AND reservation_generation = pre_io_failure_count + 1))",
            name="ck_ai_provider_attempt_receipts_pre_io_failures",
        ),
        CheckConstraint(
            "data_policy_revision >= 1",
            name="ck_ai_provider_attempt_receipts_policy_revision",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_ai_provider_attempt_receipts_fingerprint",
        ),
        CheckConstraint(
            "data_policy_mode IN ('disabled', 'audit', 'enforced', 'bypass')",
            name="ck_ai_provider_attempt_receipts_policy_mode",
        ),
        CheckConstraint(
            "state IN ('reserved', 'voided', 'failed', 'succeeded', 'ambiguous')",
            name="ck_ai_provider_attempt_receipts_state",
        ),
        CheckConstraint(
            "io_outcome IN "
            "('reserved', 'not_sent', 'response_received', 'ambiguous')",
            name="ck_ai_provider_attempt_receipts_io_outcome",
        ),
        CheckConstraint(
            "(state = 'reserved' AND io_outcome = 'reserved' "
            "AND retryable IS NULL AND settled_at IS NULL "
            "AND next_max_tokens IS NULL) OR "
            "(state = 'voided' AND io_outcome = 'not_sent' "
            "AND retryable IS TRUE AND settled_at IS NOT NULL "
            "AND next_max_tokens IS NULL) OR "
            "(state = 'failed' "
            "AND io_outcome IN ('not_sent', 'response_received') "
            "AND retryable IS NOT NULL AND settled_at IS NOT NULL "
            "AND ((retryable AND next_max_tokens IS NOT NULL) "
            "OR (NOT retryable AND next_max_tokens IS NULL))) OR "
            "(state = 'succeeded' AND io_outcome = 'response_received' "
            "AND retryable IS FALSE AND settled_at IS NOT NULL "
            "AND next_max_tokens IS NULL) OR "
            "(state = 'ambiguous' AND io_outcome = 'ambiguous' "
            "AND retryable IS FALSE AND settled_at IS NOT NULL "
            "AND next_max_tokens IS NULL)",
            name="ck_ai_provider_attempt_receipts_lifecycle",
        ),
        CheckConstraint(
            "(reconciliation_action IS NULL "
            "AND reconciled_from_state IS NULL "
            "AND reconciled_from_io_outcome IS NULL "
            "AND reconciled_by_user_id_snapshot IS NULL "
            "AND reconciled_at IS NULL) OR "
            "(reconciliation_action IN "
            "('confirmed_not_sent', 'acknowledged_may_have_sent') "
            "AND reconciled_from_state IN ('reserved', 'ambiguous') "
            "AND reconciled_from_io_outcome IN ('reserved', 'ambiguous') "
            "AND ((reconciled_from_state = 'reserved' "
            "AND reconciled_from_io_outcome = 'reserved') OR "
            "(reconciled_from_state = 'ambiguous' "
            "AND reconciled_from_io_outcome = 'ambiguous')) "
            "AND reconciled_by_user_id_snapshot IS NOT NULL "
            "AND reconciled_at IS NOT NULL "
            "AND settled_at IS NOT NULL AND settled_at <= reconciled_at "
            "AND ((reconciliation_action = 'confirmed_not_sent' "
            "AND state = 'failed' AND io_outcome = 'not_sent' "
            "AND ((retryable IS TRUE AND next_max_tokens IS NOT NULL "
            "AND attempt_number < max_attempts) OR "
            "(retryable IS FALSE AND next_max_tokens IS NULL "
            "AND attempt_number = max_attempts))) OR "
            "(reconciliation_action = 'acknowledged_may_have_sent' "
            "AND state = 'ambiguous' AND io_outcome = 'ambiguous' "
            "AND retryable IS FALSE AND next_max_tokens IS NULL)))",
            name="ck_ai_provider_attempt_receipts_reconciliation",
        ),
        UniqueConstraint(
            "operation_id",
            "attempt_number",
            name="uq_ai_provider_attempt_receipts_operation_attempt",
        ),
        Index(
            "ix_ai_provider_attempt_receipts_task_run_snapshot",
            "task_run_id_snapshot",
        ),
        Index(
            "ix_ai_provider_attempt_receipts_resource",
            "resource_type",
            "resource_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    task_run_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    feature_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reservation_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    pre_io_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_pre_io_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    iam_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data_policy_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reserved", server_default="reserved"
    )
    io_outcome: Mapped[str] = mapped_column(
        String(24), nullable=False, default="reserved", server_default="reserved"
    )
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    next_max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_action: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    reconciled_from_state: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    reconciled_from_io_outcome: Mapped[str | None] = mapped_column(
        String(24), nullable=True
    )
    reconciled_by_user_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "AI_PROVIDER_ATTEMPT_STATES",
    "AI_PROVIDER_DATA_POLICY_MODES",
    "AI_PROVIDER_IO_OUTCOMES",
    "AI_PROVIDER_RECONCILIATION_ACTIONS",
    "AIProviderAttemptReceipt",
]
