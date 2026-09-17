"""Closed, bounded contracts for the read-only MCP retrieval tools."""

from datetime import datetime, timezone
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArticlesArguments(ReadArguments):
    q: str | None = Field(default=None, max_length=200, strict=True)
    feed_id: uuid.UUID | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=20, ge=1, le=50, strict=True)
    cursor: str | None = Field(default=None, max_length=2048, strict=True)

    @field_validator("since", "until", mode="before")
    @classmethod
    def reject_numeric_dates(cls, value):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Use an ISO 8601 timestamp with a timezone.") from exc
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Use an ISO 8601 timestamp with a timezone.")
        return value

    @field_validator("since", "until")
    @classmethod
    def require_timezone(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Timestamps must include a timezone.")
            try:
                return value.astimezone(timezone.utc)
            except OverflowError as exc:
                raise ValueError("Timestamp is outside the supported UTC range.") from exc
        return value

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must not be later than until")
        return self


class ArticleEvidenceArguments(ReadArguments):
    item_id: uuid.UUID
    text_limit: int = Field(default=12000, ge=1, le=16000, strict=True)


class TeamAssessmentArguments(ReadArguments):
    item_id: uuid.UUID
    team_id: uuid.UUID


class InvestigationArguments(ReadArguments):
    investigation_id: uuid.UUID
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class ReportArguments(ReadArguments):
    report_id: uuid.UUID
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class ReadTruncation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    truncated: bool = False
    fields: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class MCPReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any]
    provenance: dict[str, Any]
    freshness: dict[str, Any]
    canonical_link: str
    truncation: ReadTruncation = Field(default_factory=ReadTruncation)
    next_cursor: str | None = None
