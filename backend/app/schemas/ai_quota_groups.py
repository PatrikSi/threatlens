"""Explicit account membership: credentials are never inspected or inferred."""

import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.storage_text import validate_storage_text


class AIQuotaGroupFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    provider_keys: list[str] = Field(default_factory=list, max_length=100)
    max_concurrent_requests: int = Field(default=0, ge=0, le=1000)
    hourly_token_budget: int = Field(default=0, ge=0, le=1_000_000_000_000)
    max_concurrent_per_team: int = Field(default=1, ge=0, le=1000)

    @field_validator("name", mode="before")
    @classmethod
    def safe_name(cls, value: object) -> object:
        return validate_storage_text(value).strip() if isinstance(value, str) else value

    @field_validator("provider_keys")
    @classmethod
    def canonical_keys(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            if value == "legacy":
                normalized.append(value)
            elif value.startswith("profile:"):
                normalized.append(f"profile:{uuid.UUID(value[8:])}")
            else:
                raise ValueError(
                    "Use legacy or profile:<provider UUID> for quota membership"
                )
        if len(normalized) != len(set(normalized)):
            raise ValueError("Each provider may occur only once")
        return sorted(normalized)


class AIQuotaGroupCreate(AIQuotaGroupFields):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)


class AIQuotaGroupUpdate(AIQuotaGroupFields):
    version: int = Field(ge=1)


class AIQuotaGroupResponse(AIQuotaGroupFields):
    id: uuid.UUID
    version: int


class AIQuotaGroupPage(BaseModel):
    items: list[AIQuotaGroupResponse]
    total: int
    limit: int
    offset: int
