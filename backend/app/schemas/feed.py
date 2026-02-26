import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeedCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=5, max_length=4000)
    enabled: bool = True
    fetch_interval_seconds: int = Field(default=1800, ge=60, le=86400)


class FeedUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    fetch_interval_seconds: int | None = Field(default=None, ge=60, le=86400)


class FeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    enabled: bool
    fetch_interval_seconds: int
    etag: str | None
    last_modified: str | None
    last_fetch_at: datetime | None
    last_success_at: datetime | None
    error_count: int
    last_error: str | None
    created_at: datetime
