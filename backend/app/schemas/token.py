import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    scopes: list[str] = Field(default_factory=list)


class ApiTokenCreateResponse(BaseModel):
    token: str
    token_prefix: str
    expires_at: datetime | None


class ApiTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    token_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
