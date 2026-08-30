from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ActionApprovalStoredStatus = Literal[
    "pending", "approved", "denied", "cancelled", "invalidated", "executed"
]
ActionApprovalEffectiveStatus = Literal[
    "pending",
    "approved",
    "denied",
    "cancelled",
    "invalidated",
    "executed",
    "expired",
]


class ActionDefinitionResponse(BaseModel):
    key: str
    label: str
    description: str
    target_type: str
    requester_permission: str
    approver_permission: str
    risk: Literal["elevated", "critical"]
    version: int = Field(ge=1)
    payload_fields: list[str]


class ActionApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(
        min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_.-]+$"
    )
    target_id: str = Field(min_length=1, max_length=255)
    target_revision: int = Field(ge=1)
    payload: dict[str, object] = Field(default_factory=dict)
    expires_in_seconds: int = Field(default=3_600, ge=300, le=86_400)
    reason: str = Field(min_length=10, max_length=2_000)

    @field_validator("target_id")
    @classmethod
    def normalize_target_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be blank.")
        return normalized

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError(
                "Provide a reason of at least 10 non-whitespace characters."
            )
        return normalized

    @field_validator("payload")
    @classmethod
    def bound_payload(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Payload must contain JSON-compatible values.") from exc
        if len(encoded) > 16_384:
            raise ValueError("Payload cannot exceed 16 KiB after JSON encoding.")
        return value


class ActionApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    approve: bool
    reason: str = Field(min_length=3, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Provide a decision reason.")
        return normalized


class ActionApprovalCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Provide a cancellation reason.")
        return normalized


class ActionApprovalExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class ActionApprovalResponse(BaseModel):
    id: uuid.UUID
    action_type: str
    action_label: str
    audit_action: str
    target_type: str
    target_id: str
    target_revision: int
    target_snapshot: dict[str, object]
    payload: dict[str, object]
    payload_digest: str
    requester_permission: str
    approver_permission: str
    action_definition_version: int
    requested_by_user_id: uuid.UUID | None
    requested_by_email: str
    requested_by_current_email: str | None
    request_reason: str
    expires_at: datetime
    stored_status: ActionApprovalStoredStatus
    status: ActionApprovalEffectiveStatus
    revision: int
    decided_by_user_id: uuid.UUID | None
    decided_by_email: str | None
    decided_by_current_email: str | None
    decided_at: datetime | None
    decision_reason: str | None
    decided_auth_token_version: int | None
    decided_auth_method: Literal["local", "oidc"] | None
    decided_mfa_method: Literal["totp", "recovery_code", "external"] | None
    cancelled_by_user_id: uuid.UUID | None
    cancelled_by_principal_type: Literal["user", "system"] | None
    cancelled_from_status: Literal["pending", "approved"] | None
    cancelled_by_email: str | None
    cancelled_by_current_email: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    executed_by_user_id: uuid.UUID | None
    executed_by_email: str | None
    executed_by_current_email: str | None
    executed_at: datetime | None
    invalidated_at: datetime | None
    invalidation_reason: str | None
    created_at: datetime
    updated_at: datetime


class ActionApprovalListResponse(BaseModel):
    approvals: list[ActionApprovalResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ActionExecutionReceiptResponse(BaseModel):
    id: uuid.UUID
    approval_request_id: uuid.UUID
    action_type: str
    target_type: str
    target_id: str
    target_revision: int
    payload_digest: str
    requester_email: str
    approver_email: str
    executed_by_email: str
    result: dict[str, object]
    result_schema_version: int
    created_at: datetime


class ActionApprovalExecutionResponse(BaseModel):
    approval: ActionApprovalResponse
    receipt: ActionExecutionReceiptResponse


__all__ = [
    "ActionApprovalCancelRequest",
    "ActionApprovalCreateRequest",
    "ActionApprovalDecisionRequest",
    "ActionApprovalEffectiveStatus",
    "ActionApprovalExecuteRequest",
    "ActionApprovalExecutionResponse",
    "ActionApprovalListResponse",
    "ActionApprovalResponse",
    "ActionApprovalStoredStatus",
    "ActionDefinitionResponse",
    "ActionExecutionReceiptResponse",
]
