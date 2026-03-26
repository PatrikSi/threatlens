import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AIProviderType = Literal["openai_compatible"]
AIRelevanceLabel = Literal["low", "medium", "high"]
AIUsageFeatureType = Literal["item_enrichment", "daily_brief", "connection_test"]


def _normalize_string_list(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = values.split(",")
    elif isinstance(values, list):
        raw_values = values
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = str(raw).strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


class AISettingsUpdate(BaseModel):
    provider_type: AIProviderType = "openai_compatible"
    base_url: str | None = Field(default=None, max_length=4000)
    model: str | None = Field(default=None, max_length=255)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_completion_tokens: int = Field(default=700, ge=128, le=8192)
    request_timeout_seconds: int = Field(default=60, ge=5, le=300)
    summary_enabled: bool = True
    relevance_enabled: bool = True
    daily_brief_enabled: bool = True
    auto_enrich_new_items: bool = True
    daily_brief_window_hours: int = Field(default=24, ge=6, le=168)
    daily_brief_max_items: int = Field(default=20, ge=5, le=100)
    daily_brief_history_limit: int = Field(default=7, ge=1, le=90)
    relevance_medium_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    relevance_high_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    company_name: str | None = Field(default=None, max_length=255)
    company_industry: str | None = Field(default=None, max_length=255)
    company_regions: list[str] = Field(default_factory=list)
    company_stack: list[str] = Field(default_factory=list)
    company_priority_topics: list[str] = Field(default_factory=list)
    company_keywords: list[str] = Field(default_factory=list)
    company_exclusions: list[str] = Field(default_factory=list)
    company_profile_text: str | None = Field(default=None, max_length=4000)
    item_enrichment_system_prompt: str | None = Field(default=None, max_length=4000)
    daily_brief_system_prompt: str | None = Field(default=None, max_length=4000)
    global_instructions: str | None = Field(default=None, max_length=4000)
    item_summary_instructions: str | None = Field(default=None, max_length=4000)
    relevance_instructions: str | None = Field(default=None, max_length=4000)
    daily_brief_instructions: str | None = Field(default=None, max_length=4000)

    @field_validator(
        "company_regions",
        "company_stack",
        "company_priority_topics",
        "company_keywords",
        "company_exclusions",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        return _normalize_string_list(value)

    @field_validator(
        "base_url",
        "model",
        "company_name",
        "company_industry",
        "company_profile_text",
        "item_enrichment_system_prompt",
        "daily_brief_system_prompt",
        "global_instructions",
        "item_summary_instructions",
        "relevance_instructions",
        "daily_brief_instructions",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _validate_thresholds(self):
        if self.relevance_high_threshold <= self.relevance_medium_threshold:
            raise ValueError("relevance_high_threshold must be greater than relevance_medium_threshold")
        return self


class AISettingsResponse(BaseModel):
    id: uuid.UUID
    ai_enabled: bool
    ai_configured: bool
    api_key_configured: bool
    provider_type: AIProviderType
    base_url: str | None
    model: str | None
    temperature: float
    max_completion_tokens: int
    request_timeout_seconds: int
    summary_enabled: bool
    relevance_enabled: bool
    daily_brief_enabled: bool
    auto_enrich_new_items: bool
    daily_brief_window_hours: int
    daily_brief_max_items: int
    daily_brief_history_limit: int
    relevance_medium_threshold: float
    relevance_high_threshold: float
    company_name: str | None
    company_industry: str | None
    company_regions: list[str]
    company_stack: list[str]
    company_priority_topics: list[str]
    company_keywords: list[str]
    company_exclusions: list[str]
    company_profile_text: str | None
    item_enrichment_system_prompt: str | None
    daily_brief_system_prompt: str | None
    global_instructions: str | None
    item_summary_instructions: str | None
    relevance_instructions: str | None
    daily_brief_instructions: str | None
    created_at: datetime
    updated_at: datetime
    prompt_previews: "AIPromptPreviews"


class AIPromptPreview(BaseModel):
    label: str
    system_prompt: str
    notes: list[str]


class AIPromptPreviews(BaseModel):
    item_enrichment: AIPromptPreview
    daily_brief: AIPromptPreview


class AITestConnectionResponse(BaseModel):
    success: bool
    latency_ms: int | None
    provider: AIProviderType
    model: str | None
    error: str | None


class AIUsageFeatureSummary(BaseModel):
    feature_type: AIUsageFeatureType
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_tokens: int
    average_latency_ms: float
    last_request_at: datetime | None


class AIUsageSummaryResponse(BaseModel):
    total_requests: int
    successful_requests: int
    failed_requests: int
    success_rate_pct: float
    requests_last_24h: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    average_latency_ms: float
    last_request_at: datetime | None
    features: list[AIUsageFeatureSummary]


class AIDailyBriefItemResponse(BaseModel):
    id: uuid.UUID
    title: str
    feed_name: str
    url: str
    published_at: datetime | None
    relevance_score: float | None
    relevance_label: AIRelevanceLabel | None


class AIDailyBriefResponse(BaseModel):
    id: uuid.UUID
    brief_date: date
    status: str
    window_start: datetime
    window_end: datetime
    title: str | None
    brief_text: str | None
    key_points: list[str]
    recommended_actions: list[str]
    item_count: int
    items: list[AIDailyBriefItemResponse]
    model: str | None
    generated_at: datetime | None
    error: str | None


class AIReprocessRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)


class AIReprocessResponse(BaseModel):
    task_id: str
    queued: bool
