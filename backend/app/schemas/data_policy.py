from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


DataPolicyMode = Literal["disabled", "audit", "enforced"]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataPolicyStateResponse(BaseModel):
    mode: DataPolicyMode
    revision: int
    coverage_version: int
    required_coverage_version: int
    enforced_at: datetime | None
    enforced_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None
    updated_at: datetime


class HandlingLabelResponse(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    color: str
    is_unrestricted: bool
    is_system: bool
    is_active: bool
    revision: int
    role_ids: list[uuid.UUID]
    assigned_feed_count: int
    created_at: datetime
    updated_at: datetime


class HandlingLabelMutationResponse(BaseModel):
    label: HandlingLabelResponse
    policy_revision: int
    changed: bool


class DataPolicyBlockerResponse(BaseModel):
    code: str
    detail: str
    count: int | None = None


class DataPolicyPreflightResponse(BaseModel):
    ready_for_audit: bool
    ready_for_enforcement: bool
    current_coverage_version: int
    required_coverage_version: int
    blockers: list[DataPolicyBlockerResponse]


class DataPolicyOverviewResponse(BaseModel):
    state: DataPolicyStateResponse
    labels: list[HandlingLabelResponse]
    preflight: DataPolicyPreflightResponse


class HandlingLabelCreateRequest(_StrictRequest):
    expected_policy_revision: int = Field(ge=1)
    key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*([._-][a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    color: str = Field(default="#64748B", pattern=r"^#[0-9A-Fa-f]{6}$")
    role_ids: list[uuid.UUID] = Field(default_factory=list, max_length=256)

    @field_validator("key", "name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("role_ids")
    @classmethod
    def unique_roles(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(values) != len(set(values)):
            raise ValueError("role_ids cannot contain duplicates")
        return values


class HandlingLabelUpdateRequest(_StrictRequest):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("name", "description")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("name", "description", "color", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Explicit null is not supported; omit unchanged fields.")
        return value


class HandlingLabelRoleGrantsRequest(_StrictRequest):
    expected_revision: int = Field(ge=1)
    role_ids: list[uuid.UUID] = Field(max_length=256)

    @field_validator("role_ids")
    @classmethod
    def unique_roles(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(values) != len(set(values)):
            raise ValueError("role_ids cannot contain duplicates")
        return values


class HandlingLabelStatusRequest(_StrictRequest):
    expected_revision: int = Field(ge=1)
    active: bool


class FeedHandlingLabelAssignmentRequest(_StrictRequest):
    expected_policy_revision: int = Field(ge=1)
    handling_label_id: uuid.UUID


class FeedHandlingLabelAssignmentResponse(BaseModel):
    feed_id: uuid.UUID
    previous_handling_label_id: uuid.UUID
    handling_label_id: uuid.UUID
    policy_revision: int
    changed: bool


class DataPolicyModeUpdateRequest(_StrictRequest):
    expected_revision: int = Field(ge=1)
    mode: DataPolicyMode
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Provide a reason with at least three characters.")
        return normalized


class DataPolicyModeUpdateResponse(BaseModel):
    state: DataPolicyStateResponse
    changed: bool
    preflight: DataPolicyPreflightResponse


__all__ = [
    "DataPolicyBlockerResponse",
    "DataPolicyMode",
    "DataPolicyModeUpdateRequest",
    "DataPolicyModeUpdateResponse",
    "DataPolicyOverviewResponse",
    "DataPolicyPreflightResponse",
    "DataPolicyStateResponse",
    "FeedHandlingLabelAssignmentRequest",
    "FeedHandlingLabelAssignmentResponse",
    "HandlingLabelCreateRequest",
    "HandlingLabelMutationResponse",
    "HandlingLabelResponse",
    "HandlingLabelRoleGrantsRequest",
    "HandlingLabelStatusRequest",
    "HandlingLabelUpdateRequest",
]
