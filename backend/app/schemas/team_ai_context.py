"""Bounded, storage-safe team input for AI relevance assessments."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.schemas.storage_text import validate_storage_text


def _trim_storage_text(value: object) -> object:
    return validate_storage_text(value).strip() if isinstance(value, str) else value


ContextEntry = Annotated[
    str,
    Field(min_length=1, max_length=200),
    BeforeValidator(_trim_storage_text),
]


class TeamAIContextFields(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    technology_stack: list[ContextEntry] = Field(max_length=40)
    priorities: list[ContextEntry] = Field(max_length=40)
    available_telemetry: list[ContextEntry] = Field(max_length=40)
    relevance_criteria: str = Field(max_length=4000)

    @field_validator("relevance_criteria", mode="before")
    @classmethod
    def trim_criteria(cls, value: object) -> object:
        return _trim_storage_text(value)

    @field_validator("technology_stack", "priorities", "available_telemetry")
    @classmethod
    def unique_entries(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = value.casefold()
            if key not in seen:
                result.append(value)
                seen.add(key)
        return result


class TeamAIContextUpdate(TeamAIContextFields):
    expected_version: int = Field(ge=0)


class TeamAIContextResponse(TeamAIContextFields):
    team_id: uuid.UUID
    version: int = Field(ge=0)
    can_manage: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
