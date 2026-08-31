from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AccessReviewStatus = Literal[
    "open", "closed", "applying", "applied", "cancelled", "quarantined"
]
AccessReviewItemType = Literal[
    "direct_user_role",
    "legacy_user_role",
    "group_membership",
    "service_account_role",
    "oidc_role_mapping",
    "oidc_group_mapping",
    "live_elevation",
]
AccessReviewAssignmentSource = Literal["local", "legacy", "oidc", "temporary"]
AccessReviewDecisionValue = Literal["retain", "revoke"]
AccessReviewApplyOutcome = Literal[
    "retained",
    "revoked",
    "already_absent",
    "manual_action_required",
    "superseded",
    "drifted",
    "failed",
]

MAX_ACCESS_REVIEW_PRINCIPALS = 500
MAX_ACCESS_REVIEW_BATCH_DECISIONS = 100


def _normalize_reason(value: str) -> str:
    normalized = value.strip()
    if len(normalized) < 3:
        raise ValueError("Provide a reason with at least three characters.")
    return normalized


class AccessReviewCampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2_000)
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    service_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    include_oidc_mappings: bool = True
    include_live_elevations: bool = True
    due_in_seconds: int = Field(
        default=14 * 24 * 60 * 60,
        ge=60 * 60,
        le=90 * 24 * 60 * 60,
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Campaign names must contain at least three characters.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("user_ids", "service_account_ids")
    @classmethod
    def reject_duplicate_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Principal selections cannot contain duplicate IDs.")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> AccessReviewCampaignCreate:
        principal_count = len(self.user_ids) + len(self.service_account_ids)
        if principal_count > MAX_ACCESS_REVIEW_PRINCIPALS:
            raise ValueError(
                f"A campaign can select at most {MAX_ACCESS_REVIEW_PRINCIPALS} principals."
            )
        if principal_count == 0 and not self.include_oidc_mappings:
            raise ValueError(
                "Select at least one user or service account, or include OIDC mappings."
            )
        return self

    def scope_snapshot(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "user_ids": sorted(str(value) for value in self.user_ids),
            "service_account_ids": sorted(
                str(value) for value in self.service_account_ids
            ),
            "include_oidc_mappings": self.include_oidc_mappings,
            "include_live_elevations": self.include_live_elevations,
        }


class AccessReviewDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    decision: AccessReviewDecisionValue
    reason: str = Field(min_length=3, max_length=2_000)

    _normalize_decision_reason = field_validator("reason")(_normalize_reason)


class AccessReviewDecisionBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    decisions: list[AccessReviewDecisionInput] = Field(
        min_length=1,
        max_length=MAX_ACCESS_REVIEW_BATCH_DECISIONS,
    )

    @field_validator("decisions")
    @classmethod
    def reject_duplicate_items(
        cls, value: list[AccessReviewDecisionInput]
    ) -> list[AccessReviewDecisionInput]:
        item_ids = [decision.item_id for decision in value]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("A decision batch can contain each review item only once.")
        return value


class AccessReviewTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=2_000)

    _normalize_transition_reason = field_validator("reason")(_normalize_reason)


class AccessReviewBeginApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class AccessReviewApplyItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expected_item_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class AccessReviewResolveItemRequest(AccessReviewApplyItemRequest):
    expected_receipt_attempt: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=2_000)

    _normalize_resolution_reason = field_validator("reason")(_normalize_reason)


class AccessReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    item_id: uuid.UUID
    item_fingerprint: str
    sequence: int = Field(ge=1)
    decision: AccessReviewDecisionValue
    decided_by_user_id: uuid.UUID | None
    decided_by_email_snapshot: str
    reason: str
    decided_at: datetime


class AccessReviewApplyReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    item_id: uuid.UUID
    item_fingerprint: str
    decision_id: uuid.UUID
    apply_run_id: uuid.UUID
    attempt: int = Field(ge=1)
    outcome: AccessReviewApplyOutcome
    expected_assignment_revision: int | None
    observed_assignment_revision: int | None
    expected_target_revision: int = Field(ge=1)
    observed_target_revision: int | None
    observed_fingerprint: str | None
    mutation_performed: bool
    detail_code: str
    detail: str
    result_snapshot: dict[str, object]
    applied_by_user_id: uuid.UUID | None
    applied_by_email_snapshot: str
    created_at: datetime


class AccessReviewItemResponse(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    ordinal: int = Field(ge=1, le=10_000)
    item_type: AccessReviewItemType
    assignment_id: uuid.UUID
    assignment_source: AccessReviewAssignmentSource
    assignment_revision_snapshot: int | None
    assignment_fingerprint: str
    principal_type: Literal["user", "service_account", "oidc_provider"]
    principal_id_snapshot: uuid.UUID
    principal_label_snapshot: str
    target_type: Literal["role", "group"]
    target_id_snapshot: uuid.UUID
    target_key_snapshot: str
    target_label_snapshot: str
    target_revision_snapshot: int = Field(ge=1)
    permissions_snapshot: list[str]
    provenance_snapshot: dict[str, object]
    assignment_created_at_snapshot: datetime
    access_expires_at_snapshot: datetime | None
    created_at: datetime
    latest_decision: AccessReviewDecisionResponse | None = None
    latest_apply_receipt: AccessReviewApplyReceiptResponse | None = None


class AccessReviewCampaignResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    scope_snapshot: dict[str, object]
    scope_digest: str
    snapshot_at: datetime
    review_due_at: datetime
    is_overdue: bool
    item_count: int = Field(ge=1, le=10_000)
    decided_item_count: int = Field(ge=0)
    revoke_item_count: int = Field(ge=0)
    apply_terminal_item_count: int = Field(ge=0)
    created_by_user_id: uuid.UUID | None
    created_by_email_snapshot: str
    status: AccessReviewStatus
    revision: int = Field(ge=1)
    closed_by_user_id: uuid.UUID | None
    closed_by_email_snapshot: str | None
    closed_at: datetime | None
    close_reason: str | None
    apply_started_by_user_id: uuid.UUID | None
    apply_started_by_email_snapshot: str | None
    apply_started_at: datetime | None
    apply_run_id: uuid.UUID | None
    applied_by_user_id: uuid.UUID | None
    applied_by_email_snapshot: str | None
    applied_at: datetime | None
    cancelled_by_user_id: uuid.UUID | None
    cancelled_by_principal_type: Literal["user", "system"] | None
    cancelled_by_email_snapshot: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    quarantined_by_user_id: uuid.UUID | None
    quarantined_by_principal_type: Literal["user", "system"] | None
    quarantined_by_email_snapshot: str | None
    quarantined_at: datetime | None
    quarantine_reason: str | None
    created_at: datetime
    updated_at: datetime


class AccessReviewCampaignListResponse(BaseModel):
    campaigns: list[AccessReviewCampaignResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class AccessReviewItemListResponse(BaseModel):
    items: list[AccessReviewItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


__all__ = [
    "MAX_ACCESS_REVIEW_BATCH_DECISIONS",
    "MAX_ACCESS_REVIEW_PRINCIPALS",
    "AccessReviewAssignmentSource",
    "AccessReviewApplyItemRequest",
    "AccessReviewApplyOutcome",
    "AccessReviewApplyReceiptResponse",
    "AccessReviewBeginApplyRequest",
    "AccessReviewCampaignCreate",
    "AccessReviewCampaignListResponse",
    "AccessReviewCampaignResponse",
    "AccessReviewDecisionBatchRequest",
    "AccessReviewDecisionInput",
    "AccessReviewDecisionResponse",
    "AccessReviewDecisionValue",
    "AccessReviewItemListResponse",
    "AccessReviewItemResponse",
    "AccessReviewItemType",
    "AccessReviewResolveItemRequest",
    "AccessReviewStatus",
    "AccessReviewTransitionRequest",
]
