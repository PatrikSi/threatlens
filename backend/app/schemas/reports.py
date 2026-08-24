from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.exports import ArticleExportFilters, ArticleExportPreviewItem, ExportOptionEntry


ReportStatus = Literal["queued", "running", "ready", "error", "skipped"]
ReportTone = Literal["analytical", "concise", "executive", "technical"]
ReportDetailLevel = Literal["brief", "standard", "detailed"]
ReportCadence = Literal["weekly", "monthly"]
ReportWindowType = Literal["previous_complete_week", "rolling_days", "previous_complete_month"]


class ReportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportArticleFilters(ArticleExportFilters):
    @model_validator(mode="after")
    def _reject_private_user_state(self):
        if self.is_read is not None or self.is_starred is not None:
            raise ValueError(
                "report filters cannot use private read or starred state because generated reports are shared"
            )
        return self


class ReportSectionConfig(ReportSchema):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    instructions: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "instructions", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None


class ReportSectionSetError(ValueError):
    pass


def validate_report_section_set(
    sections: list[ReportSectionConfig],
    *,
    allow_empty: bool = False,
) -> None:
    if not sections:
        if allow_empty:
            return
        raise ReportSectionSetError("At least one report section is required.")
    key_counts = Counter(section.key for section in sections)
    duplicate_keys = sorted(key for key, count in key_counts.items() if count > 1)
    if duplicate_keys:
        raise ReportSectionSetError(
            "Report section keys must be unique; duplicate keys: "
            + ", ".join(duplicate_keys)
            + "."
        )
    if not any(section.enabled for section in sections):
        raise ReportSectionSetError("At least one report section must be enabled.")


class ReportPromptConfig(ReportSchema):
    audience: str = Field(default="security_team", min_length=1, max_length=64)
    objective: str = Field(default="Summarize material security developments.", min_length=1, max_length=2000)
    tone: ReportTone = "analytical"
    detail_level: ReportDetailLevel = "standard"
    use_company_context: bool = True
    custom_instructions: str | None = Field(default=None, max_length=4000)
    focus_topics: list[str] = Field(default_factory=list, max_length=50)
    excluded_topics: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("audience", "objective", "custom_instructions", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("focus_topics", "excluded_topics", mode="before")
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(str(entry).strip() for entry in value if str(entry).strip()))


class ReportPreviewRequest(ReportSchema):
    filters: ReportArticleFilters = Field(default_factory=ReportArticleFilters)
    excluded_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    prompt: ReportPromptConfig = Field(default_factory=ReportPromptConfig)
    sections: list[ReportSectionConfig] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _validate_sections(self):
        validate_report_section_set(self.sections, allow_empty=True)
        return self


class ReportContextEstimate(ReportSchema):
    context_window_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    usable_input_tokens: int
    estimated_source_tokens: int
    estimated_fixed_prompt_tokens: int
    estimated_peak_input_tokens: int
    estimated_batches: int
    estimated_model_calls: int
    selected_source_count: int
    omitted_source_count: int
    coverage_percent: float
    warnings: list[str]


class ReportPreviewItem(ArticleExportPreviewItem):
    estimated_tokens: int
    selected: bool
    exclusion_reason: str | None = None


class ReportPreviewResponse(ReportSchema):
    total_matches: int
    articles_with_text: int
    items_with_iocs: int
    items: list[ReportPreviewItem]
    estimate: ReportContextEstimate


class ReportCapabilitiesResponse(ReportSchema):
    reporting_enabled: bool
    ai_configured: bool
    feeds: list[ExportOptionEntry]
    tags: list[ExportOptionEntry]
    classifications: list[str]
    max_sources: int
    preview_limit: int
    context_window_tokens: int
    reserved_output_tokens: int
    source_token_cap: int
    max_model_calls: int
    safety_percent: int


class ReportTemplateCreate(ReportSchema):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    report_type: str = Field(default="custom", min_length=1, max_length=64)
    visibility: Literal["private", "shared"] = "private"
    prompt: ReportPromptConfig = Field(default_factory=ReportPromptConfig)
    sections: list[ReportSectionConfig] = Field(default_factory=list, min_length=1, max_length=20)
    default_filters: ReportArticleFilters = Field(default_factory=ReportArticleFilters)

    @field_validator("name", "description", "report_type", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _validate_sections(self):
        validate_report_section_set(self.sections)
        return self


class ReportTemplateUpdate(ReportTemplateCreate):
    pass


class ReportTemplateResponse(ReportSchema):
    id: uuid.UUID
    owner_user_id: uuid.UUID | None
    builtin_key: str | None
    name: str
    description: str
    report_type: str
    visibility: Literal["private", "shared"]
    prompt: ReportPromptConfig
    sections: list[ReportSectionConfig]
    default_filters: ArticleExportFilters
    created_at: datetime
    updated_at: datetime


class ReportCreateRequest(ReportSchema):
    template_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=255)
    period_start: datetime
    period_end: datetime
    filters: ReportArticleFilters = Field(default_factory=ReportArticleFilters)
    excluded_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    prompt: ReportPromptConfig = Field(default_factory=ReportPromptConfig)
    sections: list[ReportSectionConfig] = Field(default_factory=list, min_length=1, max_length=20)
    deliver_when_ready: bool = False
    delivery_mode: Literal["link", "summary", "full"] = "summary"

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("period_start", "period_end")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_period(self):
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be earlier than period_end")
        validate_report_section_set(self.sections)
        return self


class ReportListItem(ReportSchema):
    id: uuid.UUID
    template_id: uuid.UUID | None
    schedule_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    title: str
    report_type: str
    status: ReportStatus
    trigger_source: Literal["manual", "scheduled", "retry"]
    generation_stage: str
    period_start: datetime
    period_end: datetime
    source_count: int
    included_source_count: int
    model_calls: int
    provider: str | None
    model: str | None
    error_code: str | None
    error: str | None
    generated_at: datetime | None
    created_at: datetime


class ReportSourceResponse(ReportSchema):
    citation_key: str
    item_id: uuid.UUID | None
    included: bool
    rank: int
    exclusion_reason: str | None
    title: str
    feed_name: str
    url: str
    classification: str | None
    relevance_score: float | None
    relevance_label: str | None
    published_at: datetime | None
    first_seen_at: datetime
    tags: list[str]
    iocs: list[dict]
    estimated_tokens: int


class ReportSectionResponse(ReportSchema):
    key: str
    title: str
    position: int
    status: str
    body_markdown: str
    key_points: list[str]
    citations: list[str]
    error: str | None


class ReportDetailResponse(ReportListItem):
    filters: ArticleExportFilters
    prompt: ReportPromptConfig
    sections_config: list[ReportSectionConfig]
    metrics: dict
    coverage: dict
    summary_text: str | None
    estimated_input_tokens: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    context_window_tokens: int
    generation_batches: int
    delivery_requested: bool
    delivery_mode: Literal["link", "summary", "full"]
    sections: list[ReportSectionResponse]
    sources: list[ReportSourceResponse]


class ReportScheduleCreate(ReportSchema):
    template_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    cadence: ReportCadence = "weekly"
    day_of_week: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=28)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    window_type: ReportWindowType = "previous_complete_week"
    rolling_days: int = Field(default=7, ge=1, le=365)
    filters: ReportArticleFilters = Field(default_factory=ReportArticleFilters)
    custom_instructions: str | None = Field(default=None, max_length=4000)
    delivery_enabled: bool = False
    delivery_mode: Literal["link", "summary", "full"] = "summary"
    skip_empty: bool = True
    missed_run_policy: Literal["latest", "skip", "all"] = "latest"

    @field_validator("name", "timezone", "custom_instructions", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA time zone") from exc
        return value

    @model_validator(mode="after")
    def _validate_schedule_shape(self):
        if self.cadence == "weekly" and self.window_type == "previous_complete_month":
            raise ValueError("weekly schedules cannot use the previous complete month window")
        if self.cadence == "monthly" and self.window_type == "previous_complete_week":
            raise ValueError("monthly schedules cannot use the previous complete week window")
        return self


class ReportScheduleUpdate(ReportScheduleCreate):
    pass


class ReportScheduleResponse(ReportScheduleCreate):
    id: uuid.UUID
    owner_user_id: uuid.UUID | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    failure_state: Literal["healthy", "retrying", "exhausted", "quarantined"]
    failure_count: int
    consecutive_failure_count: int
    last_error_code: str | None
    last_error: str | None
    last_error_at: datetime | None
    retry_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReportQueueResponse(ReportSchema):
    report_id: uuid.UUID
    task_run_id: uuid.UUID
    celery_task_id: str | None
    status: str
