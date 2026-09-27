"""Explicit subscriptions and idempotent opaque acknowledgements."""

from datetime import datetime
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator


from app.schemas.storage_text import validate_storage_text


class ConsumerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f]+$")
    expires_days: int = Field(default=90, ge=1, le=365)
    idempotency_key: uuid.UUID

    @field_validator("name")
    @classmethod
    def storage_safe_name(cls, value: str) -> str:
        value = validate_storage_text(value).strip()
        if not value:
            raise ValueError("Consumer name must not be blank")
        return value


class ConsumerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    principal_id: uuid.UUID | None
    expires_at: datetime
    retired_at: datetime | None
    revoked_at: datetime | None
    sequence: int
    replay_floor: int
    generation: int
    last_poll_at: datetime | None


class ConsumerCreated(ConsumerResponse):
    token: str


class SubscriptionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_id: uuid.UUID


class ConsumerAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=1)
    change_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class ConsumerReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_generation: int = Field(ge=1)
    discarded_previous_publications: bool
