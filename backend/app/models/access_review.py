from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


ACCESS_REVIEW_STATUSES = frozenset(
    {"open", "closed", "applying", "applied", "cancelled", "quarantined"}
)
ACCESS_REVIEW_ITEM_TYPES = frozenset(
    {
        "direct_user_role",
        "legacy_user_role",
        "group_membership",
        "service_account_role",
        "oidc_role_mapping",
        "oidc_group_mapping",
        "live_elevation",
    }
)
ACCESS_REVIEW_DECISIONS = frozenset({"retain", "revoke"})
ACCESS_REVIEW_APPLY_OUTCOMES = frozenset(
    {
        "retained",
        "revoked",
        "already_absent",
        "manual_action_required",
        "superseded",
        "drifted",
        "failed",
    }
)
ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES = frozenset(
    {"retained", "revoked", "already_absent", "superseded"}
)


@dataclass(frozen=True)
class AccessReviewAssignmentSnapshot:
    item_type: str
    assignment_id: uuid.UUID
    assignment_source: str
    assignment_revision: int | None
    principal_type: str
    principal_id: uuid.UUID
    principal_label: str
    target_type: str
    target_id: uuid.UUID
    target_key: str
    target_label: str
    target_revision: int
    permissions: tuple[str, ...]
    provenance: dict[str, object]
    assignment_created_at: datetime
    access_expires_at: datetime | None
    fingerprint: str

    def matches_item(self, item: AccessReviewItem) -> bool:
        target_revision_matches = (
            self.item_type in {"group_membership", "oidc_group_mapping"}
            or self.target_revision == item.target_revision_snapshot
        )
        return (
            self.fingerprint == item.assignment_fingerprint
            and self.assignment_revision == item.assignment_revision_snapshot
            and target_revision_matches
        )


class AccessReviewCampaign(Base):
    __tablename__ = "access_review_campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed', 'applying', 'applied', "
            "'cancelled', 'quarantined')",
            name="ck_access_review_campaigns_status",
        ),
        CheckConstraint(
            "name = btrim(name) AND length(name) BETWEEN 3 AND 160 "
            "AND name !~ '[[:cntrl:]]'",
            name="ck_access_review_campaigns_name",
        ),
        CheckConstraint(
            "description = btrim(description) AND length(description) <= 2000",
            name="ck_access_review_campaigns_description",
        ),
        CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'object' "
            "AND octet_length(scope_snapshot::text) <= 65536",
            name="ck_access_review_campaigns_scope",
        ),
        CheckConstraint(
            "scope_digest ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_campaigns_scope_digest",
        ),
        CheckConstraint(
            "item_count BETWEEN 1 AND 10000",
            name="ck_access_review_campaigns_item_count",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_access_review_campaigns_revision",
        ),
        CheckConstraint(
            "review_due_at > snapshot_at",
            name="ck_access_review_campaigns_due_after_snapshot",
        ),
        CheckConstraint(
            "(closed_at IS NULL AND closed_by_user_id IS NULL "
            "AND closed_by_email_snapshot IS NULL AND close_reason IS NULL) OR "
            "(closed_at IS NOT NULL AND closed_by_email_snapshot IS NOT NULL "
            "AND close_reason IS NOT NULL)",
            name="ck_access_review_campaigns_close_bundle",
        ),
        CheckConstraint(
            "(apply_started_at IS NULL AND apply_started_by_user_id IS NULL "
            "AND apply_started_by_email_snapshot IS NULL AND apply_run_id IS NULL) OR "
            "(apply_started_at IS NOT NULL "
            "AND apply_started_by_email_snapshot IS NOT NULL "
            "AND apply_run_id IS NOT NULL)",
            name="ck_access_review_campaigns_apply_bundle",
        ),
        CheckConstraint(
            "(applied_at IS NULL AND applied_by_user_id IS NULL "
            "AND applied_by_email_snapshot IS NULL) OR "
            "(applied_at IS NOT NULL AND applied_by_email_snapshot IS NOT NULL)",
            name="ck_access_review_campaigns_applied_bundle",
        ),
        CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancelled_by_principal_type IS NULL "
            "AND cancelled_by_email_snapshot IS NULL AND cancel_reason IS NULL) OR "
            "(cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL AND "
            "((cancelled_by_principal_type = 'user' "
            "AND cancelled_by_email_snapshot IS NOT NULL) OR "
            "(cancelled_by_principal_type = 'system' "
            "AND cancelled_by_user_id IS NULL "
            "AND cancelled_by_email_snapshot IS NULL)))",
            name="ck_access_review_campaigns_cancel_bundle",
        ),
        CheckConstraint(
            "(quarantined_at IS NULL AND quarantined_by_user_id IS NULL "
            "AND quarantined_by_principal_type IS NULL "
            "AND quarantined_by_email_snapshot IS NULL "
            "AND quarantine_reason IS NULL) OR "
            "(quarantined_at IS NOT NULL AND quarantine_reason IS NOT NULL AND "
            "((quarantined_by_principal_type = 'user' "
            "AND quarantined_by_email_snapshot IS NOT NULL) OR "
            "(quarantined_by_principal_type = 'system' "
            "AND quarantined_by_user_id IS NULL "
            "AND quarantined_by_email_snapshot IS NULL)))",
            name="ck_access_review_campaigns_quarantine_bundle",
        ),
        CheckConstraint(
            "close_reason IS NULL OR (length(close_reason) BETWEEN 3 AND 2000 "
            "AND close_reason = btrim(close_reason))",
            name="ck_access_review_campaigns_close_reason",
        ),
        CheckConstraint(
            "cancel_reason IS NULL OR (length(cancel_reason) BETWEEN 3 AND 2000 "
            "AND cancel_reason = btrim(cancel_reason))",
            name="ck_access_review_campaigns_cancel_reason",
        ),
        CheckConstraint(
            "quarantine_reason IS NULL OR "
            "(length(quarantine_reason) BETWEEN 3 AND 2000 "
            "AND quarantine_reason = btrim(quarantine_reason))",
            name="ck_access_review_campaigns_quarantine_reason",
        ),
        CheckConstraint(
            "(status = 'open' AND closed_at IS NULL "
            "AND apply_started_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL "
            "AND apply_started_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'applying' AND closed_at IS NOT NULL "
            "AND apply_started_at IS NOT NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'applied' AND closed_at IS NOT NULL "
            "AND apply_started_at IS NOT NULL AND applied_at IS NOT NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'cancelled' AND closed_at IS NULL "
            "AND apply_started_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NOT NULL AND quarantined_at IS NULL) OR "
            "(status = 'quarantined' AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NOT NULL "
            "AND (apply_started_at IS NULL OR closed_at IS NOT NULL))",
            name="ck_access_review_campaigns_state",
        ),
        UniqueConstraint(
            "id",
            "apply_run_id",
            name="uq_access_review_campaigns_apply_run_owner",
        ),
        Index(
            "ix_access_review_campaigns_status_due",
            "status",
            "review_due_at",
        ),
        Index(
            "ix_access_review_campaigns_created",
            "created_at",
            "id",
        ),
        Index(
            "ix_access_review_campaigns_creator",
            "created_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    scope_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    review_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    apply_started_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    apply_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    apply_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    applied_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_by_principal_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    cancelled_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarantined_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    quarantined_by_principal_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    quarantined_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AccessReviewItem(Base):
    __tablename__ = "access_review_items"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ('direct_user_role', 'legacy_user_role', "
            "'group_membership', "
            "'service_account_role', 'oidc_role_mapping', "
            "'oidc_group_mapping', 'live_elevation')",
            name="ck_access_review_items_type",
        ),
        CheckConstraint(
            "principal_type IN ('user', 'service_account', 'oidc_provider')",
            name="ck_access_review_items_principal_type",
        ),
        CheckConstraint(
            "target_type IN ('role', 'group')",
            name="ck_access_review_items_target_type",
        ),
        CheckConstraint(
            "assignment_source IN ('local', 'legacy', 'oidc', 'temporary')",
            name="ck_access_review_items_source",
        ),
        CheckConstraint(
            "(item_type IN ('direct_user_role', 'legacy_user_role', "
            "'group_membership', "
            "'live_elevation') AND principal_type = 'user') OR "
            "(item_type = 'service_account_role' "
            "AND principal_type = 'service_account') OR "
            "(item_type IN ('oidc_role_mapping', 'oidc_group_mapping') "
            "AND principal_type = 'oidc_provider')",
            name="ck_access_review_items_principal_matches_type",
        ),
        CheckConstraint(
            "(item_type IN ('direct_user_role', 'legacy_user_role', "
            "'service_account_role', 'oidc_role_mapping', 'live_elevation') "
            "AND target_type = 'role') OR "
            "(item_type IN ('group_membership', 'oidc_group_mapping') "
            "AND target_type = 'group')",
            name="ck_access_review_items_target_matches_type",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 10000",
            name="ck_access_review_items_ordinal",
        ),
        CheckConstraint(
            "assignment_revision_snapshot IS NULL OR assignment_revision_snapshot >= 1",
            name="ck_access_review_items_assignment_revision",
        ),
        CheckConstraint(
            "target_revision_snapshot >= 1",
            name="ck_access_review_items_target_revision",
        ),
        CheckConstraint(
            "assignment_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_items_fingerprint",
        ),
        CheckConstraint(
            "principal_label_snapshot = btrim(principal_label_snapshot) "
            "AND length(principal_label_snapshot) BETWEEN 1 AND 320",
            name="ck_access_review_items_principal_label",
        ),
        CheckConstraint(
            "target_key_snapshot = btrim(target_key_snapshot) "
            "AND length(target_key_snapshot) BETWEEN 1 AND 255",
            name="ck_access_review_items_target_key",
        ),
        CheckConstraint(
            "target_label_snapshot = btrim(target_label_snapshot) "
            "AND length(target_label_snapshot) BETWEEN 1 AND 320",
            name="ck_access_review_items_target_label",
        ),
        CheckConstraint(
            "jsonb_typeof(permissions_snapshot) = 'array' "
            "AND octet_length(permissions_snapshot::text) <= 32768 "
            "AND NOT jsonb_path_exists(permissions_snapshot, "
            "'$[*] ? (@.type() != \"string\")')",
            name="ck_access_review_items_permissions",
        ),
        CheckConstraint(
            "jsonb_typeof(provenance_snapshot) = 'object' "
            "AND octet_length(provenance_snapshot::text) <= 65536",
            name="ck_access_review_items_provenance",
        ),
        UniqueConstraint(
            "campaign_id",
            "ordinal",
            name="uq_access_review_items_campaign_ordinal",
        ),
        UniqueConstraint(
            "campaign_id",
            "item_type",
            "assignment_id",
            name="uq_access_review_items_campaign_assignment",
        ),
        UniqueConstraint(
            "id",
            "campaign_id",
            "assignment_fingerprint",
            name="uq_access_review_items_decision_owner",
        ),
        Index(
            "ix_access_review_items_campaign_type_ordinal",
            "campaign_id",
            "item_type",
            "ordinal",
        ),
        Index(
            "ix_access_review_items_principal",
            "principal_type",
            "principal_id_snapshot",
        ),
        Index(
            "ix_access_review_items_target",
            "target_type",
            "target_id_snapshot",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("access_review_campaigns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    assignment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(16), nullable=False)
    assignment_revision_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    assignment_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    principal_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    principal_label_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    target_key_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    target_label_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    target_revision_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    permissions_snapshot: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    provenance_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    assignment_created_at_snapshot: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    access_expires_at_snapshot: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccessReviewDecision(Base):
    __tablename__ = "access_review_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('retain', 'revoke')",
            name="ck_access_review_decisions_value",
        ),
        CheckConstraint(
            "sequence >= 1",
            name="ck_access_review_decisions_sequence",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 3 AND 2000 AND reason = btrim(reason)",
            name="ck_access_review_decisions_reason",
        ),
        CheckConstraint(
            "item_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_decisions_fingerprint",
        ),
        ForeignKeyConstraint(
            ["item_id", "campaign_id", "item_fingerprint"],
            [
                "access_review_items.id",
                "access_review_items.campaign_id",
                "access_review_items.assignment_fingerprint",
            ],
            ondelete="RESTRICT",
            name="fk_access_review_decisions_item_snapshot",
        ),
        UniqueConstraint(
            "item_id",
            "sequence",
            name="uq_access_review_decisions_item_sequence",
        ),
        UniqueConstraint(
            "id",
            "campaign_id",
            "item_id",
            "item_fingerprint",
            name="uq_access_review_decisions_receipt_owner",
        ),
        Index(
            "ix_access_review_decisions_campaign_item_sequence",
            "campaign_id",
            "item_id",
            "sequence",
        ),
        Index(
            "ix_access_review_decisions_decider",
            "decided_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AccessReviewApplyReceipt(Base):
    __tablename__ = "access_review_apply_receipts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('retained', 'revoked', 'already_absent', "
            "'manual_action_required', 'superseded', 'drifted', 'failed')",
            name="ck_access_review_apply_receipts_outcome",
        ),
        CheckConstraint(
            "attempt >= 1",
            name="ck_access_review_apply_receipts_attempt",
        ),
        CheckConstraint(
            "item_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_apply_receipts_item_fingerprint",
        ),
        CheckConstraint(
            "observed_fingerprint IS NULL OR observed_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_apply_receipts_observed_fingerprint",
        ),
        CheckConstraint(
            "expected_assignment_revision IS NULL OR expected_assignment_revision >= 1",
            name="ck_access_review_apply_receipts_expected_assignment_revision",
        ),
        CheckConstraint(
            "observed_assignment_revision IS NULL OR observed_assignment_revision >= 1",
            name="ck_access_review_apply_receipts_observed_assignment_revision",
        ),
        CheckConstraint(
            "expected_target_revision >= 1",
            name="ck_access_review_apply_receipts_expected_target_revision",
        ),
        CheckConstraint(
            "observed_target_revision IS NULL OR observed_target_revision >= 1",
            name="ck_access_review_apply_receipts_observed_target_revision",
        ),
        CheckConstraint(
            "(outcome = 'revoked' AND mutation_performed) OR "
            "(outcome <> 'revoked' AND NOT mutation_performed)",
            name="ck_access_review_apply_receipts_mutation_outcome",
        ),
        CheckConstraint(
            "detail_code ~ '^[a-z][a-z0-9_]{2,63}$'",
            name="ck_access_review_apply_receipts_detail_code",
        ),
        CheckConstraint(
            "length(detail) BETWEEN 3 AND 2000 AND detail = btrim(detail)",
            name="ck_access_review_apply_receipts_detail",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object' "
            "AND octet_length(result_snapshot::text) <= 65536",
            name="ck_access_review_apply_receipts_result",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "apply_run_id"],
            [
                "access_review_campaigns.id",
                "access_review_campaigns.apply_run_id",
            ],
            ondelete="RESTRICT",
            name="fk_access_review_apply_receipts_campaign_run",
        ),
        ForeignKeyConstraint(
            ["decision_id", "campaign_id", "item_id", "item_fingerprint"],
            [
                "access_review_decisions.id",
                "access_review_decisions.campaign_id",
                "access_review_decisions.item_id",
                "access_review_decisions.item_fingerprint",
            ],
            ondelete="RESTRICT",
            name="fk_access_review_apply_receipts_decision",
        ),
        UniqueConstraint(
            "item_id",
            "attempt",
            name="uq_access_review_apply_receipts_item_attempt",
        ),
        Index(
            "ix_access_review_apply_receipts_campaign_item_attempt",
            "campaign_id",
            "item_id",
            "attempt",
        ),
        Index(
            "ix_access_review_apply_receipts_run",
            "apply_run_id",
            "created_at",
        ),
        Index(
            "ix_access_review_apply_receipts_actor",
            "applied_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    item_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    apply_run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_assignment_revision: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    observed_assignment_revision: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    expected_target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_target_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mutation_performed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    detail_code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    applied_by_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


def build_access_review_assignment_snapshot(
    assignment: tuple[str, uuid.UUID, str, int | None],
    principal: tuple[str, uuid.UUID, str],
    target: tuple[str, uuid.UUID, str, str, int],
    permissions: tuple[str, ...],
    provenance: dict[str, object],
    assignment_created_at: datetime,
    access_expires_at: datetime | None,
) -> AccessReviewAssignmentSnapshot:
    item_type, assignment_id, assignment_source, assignment_revision = assignment
    principal_type, principal_id, principal_label = principal
    target_type, target_id, target_key, target_label, target_revision = target
    normalized_permissions = tuple(sorted(set(permissions)))
    fingerprint_provenance = _access_review_fingerprint_provenance(
        item_type, provenance
    )
    fingerprint_target_revision = (
        None
        if item_type in {"group_membership", "oidc_group_mapping"}
        else target_revision
    )
    fingerprint_expiry = None if assignment_source == "oidc" else access_expires_at
    fingerprint = access_review_snapshot_digest(
        {
            "assignment": (
                item_type,
                str(assignment_id),
                assignment_source,
                assignment_revision,
            ),
            "principal": (principal_type, str(principal_id)),
            "target": (target_type, str(target_id), fingerprint_target_revision),
            "permissions": normalized_permissions,
            "provenance": fingerprint_provenance,
            "assignment_created_at": access_review_snapshot_datetime(
                assignment_created_at
            ),
            "access_expires_at": access_review_snapshot_datetime(fingerprint_expiry),
        }
    )
    return AccessReviewAssignmentSnapshot(
        item_type=item_type,
        assignment_id=assignment_id,
        assignment_source=assignment_source,
        assignment_revision=assignment_revision,
        principal_type=principal_type,
        principal_id=principal_id,
        principal_label=principal_label.strip(),
        target_type=target_type,
        target_id=target_id,
        target_key=target_key.strip(),
        target_label=target_label.strip(),
        target_revision=target_revision,
        permissions=normalized_permissions,
        provenance=provenance,
        assignment_created_at=assignment_created_at,
        access_expires_at=access_expires_at,
        fingerprint=fingerprint,
    )


def _access_review_fingerprint_provenance(
    item_type: str, provenance: dict[str, object]
) -> dict[str, object]:
    if item_type in {"oidc_role_mapping", "oidc_group_mapping"}:
        semantic_keys = {
            "schema_version",
            "mapping_set_id",
            "mapping_set_enabled",
            "claim_path",
            "missing_claim_behavior",
            "claim_value",
            "source_key",
            "access_policy_id",
            "access_policy_enabled",
            "provider_enabled",
            "group_roles",
        }
        value = {
            key: _without_fingerprint_metadata(child, {"audit"})
            for key, child in provenance.items()
            if key in semantic_keys
        }
        return value
    excluded_keys = {"audit"}
    if item_type == "group_membership":
        excluded_keys.add("group_revision")
    elif item_type == "service_account_role":
        excluded_keys.add("service_account_revision")
    value = _without_fingerprint_metadata(provenance, excluded_keys)
    if not isinstance(value, dict):
        raise TypeError("Access-review provenance must remain an object.")
    return value


def _without_fingerprint_metadata(value: object, excluded_keys: set[str]) -> object:
    if isinstance(value, dict):
        return {
            key: _without_fingerprint_metadata(child, excluded_keys)
            for key, child in value.items()
            if key not in excluded_keys
        }
    if isinstance(value, list):
        return [_without_fingerprint_metadata(child, excluded_keys) for child in value]
    if isinstance(value, tuple):
        return tuple(
            _without_fingerprint_metadata(child, excluded_keys) for child in value
        )
    return value


def access_review_item_from_snapshot(
    campaign_id: uuid.UUID,
    ordinal: int,
    snapshot: AccessReviewAssignmentSnapshot,
    now: datetime,
) -> AccessReviewItem:
    return AccessReviewItem(
        campaign_id=campaign_id,
        ordinal=ordinal,
        item_type=snapshot.item_type,
        assignment_id=snapshot.assignment_id,
        assignment_source=snapshot.assignment_source,
        assignment_revision_snapshot=snapshot.assignment_revision,
        assignment_fingerprint=snapshot.fingerprint,
        principal_type=snapshot.principal_type,
        principal_id_snapshot=snapshot.principal_id,
        principal_label_snapshot=snapshot.principal_label,
        target_type=snapshot.target_type,
        target_id_snapshot=snapshot.target_id,
        target_key_snapshot=snapshot.target_key,
        target_label_snapshot=snapshot.target_label,
        target_revision_snapshot=snapshot.target_revision,
        permissions_snapshot=list(snapshot.permissions),
        provenance_snapshot=snapshot.provenance,
        assignment_created_at_snapshot=snapshot.assignment_created_at,
        access_expires_at_snapshot=snapshot.access_expires_at,
        created_at=now,
    )


def access_review_snapshot_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def access_review_snapshot_digest(value: object) -> str:
    return hashlib.sha256(
        access_review_snapshot_json(value).encode("utf-8")
    ).hexdigest()


def access_review_snapshot_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def access_review_snapshot_uuid(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


__all__ = [
    "ACCESS_REVIEW_APPLY_OUTCOMES",
    "ACCESS_REVIEW_DECISIONS",
    "ACCESS_REVIEW_ITEM_TYPES",
    "ACCESS_REVIEW_STATUSES",
    "ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES",
    "AccessReviewAssignmentSnapshot",
    "AccessReviewApplyReceipt",
    "AccessReviewCampaign",
    "AccessReviewDecision",
    "AccessReviewItem",
    "access_review_item_from_snapshot",
    "access_review_snapshot_digest",
    "access_review_snapshot_datetime",
    "access_review_snapshot_json",
    "access_review_snapshot_uuid",
    "build_access_review_assignment_snapshot",
]
