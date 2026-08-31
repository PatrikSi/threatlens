from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ElevationStoredStatus = Literal["pending", "approved", "denied", "cancelled", "revoked"]
ElevationEffectiveStatus = Literal[
    "pending", "approved", "denied", "cancelled", "revoked", "expired"
]


class ElevationRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: uuid.UUID
    expected_role_revision: int = Field(ge=1)
    target_user_id: uuid.UUID | None = None
    duration_seconds: int = Field(ge=300, le=86_400)
    reason: str = Field(min_length=10, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Explain why this temporary access is required.")
        return normalized


class ElevationDecisionRequest(BaseModel):
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


class ElevationCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Provide a cancellation or revocation reason.")
        return normalized


class TemporaryElevationResponse(BaseModel):
    id: uuid.UUID
    target_user_id: uuid.UUID | None
    target_email: str
    target_current_email: str | None
    role_id: uuid.UUID | None
    role_key: str
    role_name: str
    role_revision_snapshot: int
    permission_snapshot: list[str]
    requested_by_user_id: uuid.UUID | None
    requested_by_email: str | None
    requested_by_current_email: str | None
    requested_duration_seconds: int
    request_reason: str
    request_expires_at: datetime
    stored_status: ElevationStoredStatus
    status: ElevationEffectiveStatus
    revision: int
    decided_by_user_id: uuid.UUID | None
    decided_by_email: str | None
    decided_by_current_email: str | None
    decided_at: datetime | None
    decision_reason: str | None
    grant_started_at: datetime | None
    grant_expires_at: datetime | None
    closed_by_user_id: uuid.UUID | None
    closed_by_principal_type: Literal["user", "system"] | None
    closed_by_email: str | None
    closed_by_current_email: str | None
    closed_at: datetime | None
    close_reason: str | None
    created_at: datetime
    updated_at: datetime


class TemporaryElevationListResponse(BaseModel):
    elevations: list[TemporaryElevationResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


__all__ = [
    "ElevationCloseRequest",
    "ElevationDecisionRequest",
    "ElevationEffectiveStatus",
    "ElevationRequestCreate",
    "ElevationStoredStatus",
    "TemporaryElevationListResponse",
    "TemporaryElevationResponse",
]
