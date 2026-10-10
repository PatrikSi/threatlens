"""Bounded and storage-safe receiver protocol."""

from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExecutionStatus = Literal["accepted", "running", "completed", "failed", "unknown"]


class ExecutionCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback_id: uuid.UUID
    sequence: int = Field(ge=1, le=2_147_483_647)
    external_job_id: str = Field(min_length=1, max_length=256)
    status: ExecutionStatus
    findings: str | None = Field(default=None, max_length=8000)

    @field_validator("external_job_id", "findings")
    @classmethod
    def storage_safe(cls, value: str | None) -> str | None:
        if value is not None:
            if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError(
                    "Text contains unsupported control or Unicode characters"
                )
            if not value.strip():
                raise ValueError("Text must not be blank")
        return value


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    webhook_id: uuid.UUID
    action_id: str
    event_id: uuid.UUID
    external_job_id: str | None
    status: ExecutionStatus
    sequence: int
    findings: str | None
    investigation_note_id: uuid.UUID | None
    policy_state: str
    policy_revision: int
    policy_acknowledged_revision: int
    created_at: datetime
    updated_at: datetime


class AttachFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: uuid.UUID
    expected_investigation_version: int = Field(ge=1)
    expected_sequence: int = Field(ge=1)


class PolicyUpdateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    execution_id: uuid.UUID
    revision: int
    event_type: str
    reason: str
    replacement_action_id: str | None
    acknowledged_at: datetime | None
    created_at: datetime
