import uuid
from datetime import datetime
from typing import Literal

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, model_validator

FeedFetchMode = Literal["interval", "schedule"]


class FeedCreate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str = Field(min_length=5, max_length=4000)
    description: str | None = None
    site_url: str | None = None
    language: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    fetch_mode: FeedFetchMode = "interval"
    fetch_interval_seconds: int | None = Field(default=1800, ge=60, le=86400)
    schedule_cron: str | None = None

    @model_validator(mode="after")
    def validate_fetch_settings(self):
        if self.fetch_mode == "interval":
            if self.fetch_interval_seconds is None:
                raise ValueError("fetch_interval_seconds is required for interval mode")
            self.schedule_cron = None
            return self

        if not self.schedule_cron:
            raise ValueError("schedule_cron is required for schedule mode")
        if not croniter.is_valid(self.schedule_cron):
            raise ValueError("schedule_cron must be a valid cron expression")
        return self


class FeedUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=5, max_length=4000)
    description: str | None = None
    site_url: str | None = None
    language: str | None = Field(default=None, max_length=64)
    fetch_mode: FeedFetchMode | None = None
    enabled: bool | None = None
    fetch_interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    schedule_cron: str | None = None

    @model_validator(mode="after")
    def validate_fetch_settings(self):
        if self.fetch_mode == "interval":
            self.schedule_cron = None
            return self

        if self.fetch_mode == "schedule":
            if not self.schedule_cron:
                raise ValueError("schedule_cron is required for schedule mode")
            if not croniter.is_valid(self.schedule_cron):
                raise ValueError("schedule_cron must be a valid cron expression")
        elif self.schedule_cron is not None and self.schedule_cron.strip():
            if not croniter.is_valid(self.schedule_cron):
                raise ValueError("schedule_cron must be a valid cron expression")
        return self


class FeedMetadataRequest(BaseModel):
    url: str = Field(min_length=5, max_length=4000)


class FeedMetadataResponse(BaseModel):
    name: str | None
    description: str | None
    site_url: str | None
    language: str | None
    etag: str | None
    last_modified: str | None
    resolved_url: str | None
    feed_type: str | None


class FeedImportEntry(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str = Field(min_length=5, max_length=4000)
    description: str | None = None
    site_url: str | None = None
    language: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    fetch_mode: FeedFetchMode = "interval"
    fetch_interval_seconds: int | None = Field(default=1800, ge=60, le=86400)
    schedule_cron: str | None = None

    @model_validator(mode="after")
    def validate_fetch_settings(self):
        if self.fetch_mode == "interval":
            if self.fetch_interval_seconds is None:
                raise ValueError("fetch_interval_seconds is required for interval mode")
            self.schedule_cron = None
            return self

        if not self.schedule_cron:
            raise ValueError("schedule_cron is required for schedule mode")
        if not croniter.is_valid(self.schedule_cron):
            raise ValueError("schedule_cron must be a valid cron expression")
        return self


class FeedImportRequest(BaseModel):
    feeds: list[FeedImportEntry] = Field(default_factory=list)
    overwrite_existing: bool = False


class FeedImportResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str]


class FeedExportResponse(BaseModel):
    exported_at: datetime
    export_type: Literal["sanitized", "backup"] = "sanitized"
    includes_sensitive_urls: bool = False
    feeds: list[FeedImportEntry]
    warnings: list[str] = Field(default_factory=list)


class FeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    description: str | None
    site_url: str | None
    language: str | None
    enabled: bool
    fetch_mode: FeedFetchMode
    fetch_interval_seconds: int
    schedule_cron: str | None
    etag: str | None
    last_modified: str | None
    last_fetch_at: datetime | None
    last_success_at: datetime | None
    error_count: int
    last_error: str | None
    has_unreadable_url: bool = False
    created_at: datetime
