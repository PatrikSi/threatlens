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

    minute_request_budget: int = Field(default=0, ge=0, le=1_000_000)
    minute_token_budget: int = Field(default=0, ge=0, le=1_000_000_000_000)
    team_hourly_token_budgets: dict[str, int] = Field(
        default_factory=dict, max_length=1000
    )

    @field_validator("team_hourly_token_budgets")
    @classmethod
    def allocations(cls, values: dict[str, int]) -> dict[str, int]:
        result = {}
        for key, value in values.items():
            canonical = (
                "shared"
                if key == "shared"
                else f"team:{uuid.UUID(key.removeprefix('team:'))}"
            )
            if value < 0 or value > 1_000_000_000_000:
                raise ValueError(
                    "Team token allocations must be between 0 and 1000000000000"
                )
            if canonical in result:
                raise ValueError("A team allocation may occur only once")
            result[canonical] = value
        return result

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


class AIQuotaUsage(BaseModel):
    team_key: str
    active_requests: int
    requests_last_minute: int
    reserved_tokens_last_hour: int
    reported_tokens_last_hour: int
    conservative_tokens_last_hour: int
    waiting_seconds: int | None = None
    deferral_reason: str | None = None


class AIQuotaUtilization(BaseModel):
    group_id: uuid.UUID
    totals: AIQuotaUsage
    teams: list[AIQuotaUsage]
    teams_truncated: bool
    oldest_wait_seconds: int | None
