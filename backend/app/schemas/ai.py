import uuid
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import get_settings
from app.services.url_utils import is_fetchable_url, normalize_url

AIProviderType = Literal["openai_compatible"]
AIRelevanceLabel = Literal["low", "medium", "high"]
AIUsageFeatureType = Literal[
    "item_enrichment", "daily_brief", "report", "connection_test"
]
AITaskType = Literal[
    "item_enrichment", "daily_brief", "report", "connection_test", "reprocess"
]
AITriggerSource = Literal["auto", "manual", "scheduled"]
AITaskStatus = Literal["queued", "running", "ready", "error", "skipped"]
_SHARED_AI_API_KEY_ALLOWED_HOSTS = frozenset({"api.openai.com"})


def _sanitize_required_public_url(value: object) -> str:
    normalized = normalize_url(str(value).strip() if value is not None else None)
    return normalized


def _sanitize_optional_public_url(value: object) -> str | None:
    if value is None:
        return None
    normalized = normalize_url(str(value).strip())
    return normalized or None


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
    max_completion_tokens: int = Field(default=5000, ge=128, le=8192)
    request_timeout_seconds: int = Field(default=300, ge=5, le=300)
    request_max_retries: int = Field(default=3, ge=0, le=5)
    summary_enabled: bool = True
    relevance_enabled: bool = True
    daily_brief_enabled: bool = True
    reporting_enabled: bool = True
    auto_enrich_new_items: bool = True
    daily_brief_window_hours: int = Field(default=24, ge=6, le=168)
    daily_brief_max_items: int = Field(default=20, ge=5, le=100)
    daily_brief_history_limit: int = Field(default=7, ge=1, le=90)
    daily_brief_schedule_hour_utc: int = Field(default=9, ge=0, le=23)
    daily_brief_schedule_minute_utc: int = Field(default=0, ge=0, le=59)
    report_context_window_tokens: int = Field(default=8192, ge=2048, le=1_000_000)
    report_reserved_output_tokens: int = Field(default=1200, ge=256, le=65_536)
    report_source_token_cap: int = Field(default=700, ge=128, le=32_768)
    report_max_sources: int = Field(default=100, ge=1, le=1000)
    report_max_model_calls: int = Field(default=20, ge=2, le=200)
    report_context_safety_percent: int = Field(default=15, ge=5, le=40)
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

    @field_validator("base_url", mode="before")
    @classmethod
    def _validate_base_url(cls, value: object) -> str | None:
        if value is None:
            return None
        base_url = str(value).strip()
        if not base_url:
            return None

        try:
            parsed = urlsplit(base_url)
        except ValueError as exc:
            raise ValueError("base_url must be a valid URL") from exc
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("base_url must be a valid URL") from exc

        settings = get_settings()
        allow_private_network = bool(settings.allow_private_network_ai)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        if settings.ai_api_key:
            hostname = (parsed.hostname or "").lower().rstrip(".")
            if (
                parsed.scheme.lower() != "https"
                or hostname not in _SHARED_AI_API_KEY_ALLOWED_HOSTS
                or port not in (None, 443)
            ):
                raise ValueError(
                    "base_url must target https://api.openai.com when the server AI_API_KEY is configured"
                )
        if parsed.scheme.lower() != "https" and not allow_private_network:
            raise ValueError(
                "base_url must use https unless ALLOW_PRIVATE_NETWORK_AI is enabled"
            )
        if (
            parsed.scheme.lower() == "http"
            and allow_private_network
            and is_fetchable_url(base_url, allow_private_network=False)
        ):
            raise ValueError(
                "base_url must use https for publicly routable hosts; plain http is only allowed for private-network AI endpoints"
            )
        if parsed.username or parsed.password:
            raise ValueError("base_url must not include embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not include query parameters or fragments")
        if "{{" in parsed.scheme or "{{" in parsed.netloc:
            raise ValueError(
                "base_url must not contain templates in the scheme or host"
            )
        if not is_fetchable_url(base_url, allow_private_network=allow_private_network):
            raise ValueError("base_url is not allowed for outbound fetch")
        return base_url

    @model_validator(mode="after")
    def _validate_thresholds(self):
        if self.relevance_high_threshold <= self.relevance_medium_threshold:
            raise ValueError(
                "relevance_high_threshold must be greater than relevance_medium_threshold"
            )
        return self

    @model_validator(mode="after")
    def _validate_report_context_budget(self):
        reserved = self.report_reserved_output_tokens
        safety = (
            self.report_context_window_tokens
            * self.report_context_safety_percent
            // 100
        )
        if reserved + safety + 512 >= self.report_context_window_tokens:
            raise ValueError(
                "report context window must leave at least 512 tokens after the output reserve and safety margin"
            )
        if (
            self.report_source_token_cap
            >= self.report_context_window_tokens - reserved - safety
        ):
            raise ValueError(
                "report source token cap must fit inside the usable report context budget"
            )
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
    request_max_retries: int
    summary_enabled: bool
    relevance_enabled: bool
    daily_brief_enabled: bool
    reporting_enabled: bool
    auto_enrich_new_items: bool
    daily_brief_window_hours: int
    daily_brief_max_items: int
    daily_brief_history_limit: int
    daily_brief_schedule_hour_utc: int
    daily_brief_schedule_minute_utc: int
    report_context_window_tokens: int
    report_reserved_output_tokens: int
    report_source_token_cap: int
    report_max_sources: int
    report_max_model_calls: int
    report_context_safety_percent: int
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
    skipped: bool = False
    skip_reason: str | None = None
    running_task_count: int = 0
    queued_task_count: int = 0


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

    @field_validator("url", mode="before")
    @classmethod
    def _sanitize_url(cls, value: object) -> str:
        return _sanitize_required_public_url(value)


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
    days: int | None = Field(default=7, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)
    start_time: datetime | None = None
    end_time: datetime | None = None
    feed_ids: list[uuid.UUID] = Field(default_factory=list)
    item_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_reprocess_scope(self):
        self.feed_ids = list(dict.fromkeys(self.feed_ids))
        self.item_ids = list(dict.fromkeys(self.item_ids))
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        if self.item_ids:
            return self
        if self.start_time or self.end_time:
            return self
        if self.days is None:
            self.days = 7
        return self


class AIQueuedTaskResponse(BaseModel):
    task_id: str
    queued: bool
    run_id: uuid.UUID | None = None
    celery_task_id: str | None = None


class AIDailyBriefBackfillRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


class AIDailyBriefBackfillResponse(AIQueuedTaskResponse):
    days: int


class AIReprocessResponse(AIQueuedTaskResponse):
    pass


class AITaskRunResponse(BaseModel):
    id: uuid.UUID
    task_type: AITaskType
    trigger_source: AITriggerSource
    status: AITaskStatus
    reason: str | None
    celery_task_id: str | None
    worker_name: str | None
    actor_user_id: uuid.UUID | None
    actor_email: str | None = None
    item_id: uuid.UUID | None
    item_title: str | None = None
    item_url: str | None = None
    feed_name: str | None = None
    item_first_seen_at: datetime | None = None
    item_published_at: datetime | None = None
    daily_brief_id: uuid.UUID | None
    report_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    duration_ms: int | None
    prompt_char_count: int | None
    response_char_count: int | None
    input_text_chars: int | None
    error: str | None
    metadata: dict[str, object]
    target_count: int | None
    processed_count: int
    success_count: int
    error_count: int
    skipped_count: int
    skipped_unchanged_count: int
    skipped_ineligible_count: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("item_url", mode="before")
    @classmethod
    def _sanitize_item_url(cls, value: object) -> str | None:
        return _sanitize_optional_public_url(value)


class AITaskRunListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AITaskRunResponse]


class AITaskEventResponse(BaseModel):
    id: uuid.UUID
    task_run_id: uuid.UUID
    event_type: str
    message: str | None
    payload: dict[str, object]
    created_at: datetime


class AITaskRunDetailResponse(BaseModel):
    run: AITaskRunResponse
    events: list[AITaskEventResponse]


class AILiveTaskResponse(BaseModel):
    worker_name: str
    celery_task_id: str | None
    task_name: str
    state: Literal["active", "reserved", "scheduled"]
    run_id: uuid.UUID | None = None
    item_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    eta: str | None = None
    received_at: str | None = None
    raw_name: str | None = None


class AILiveStatusResponse(BaseModel):
    worker_count: int
    workers: list[str]
    active_tasks: list[AILiveTaskResponse]
    reserved_tasks: list[AILiveTaskResponse]
    scheduled_tasks: list[AILiveTaskResponse]
    active_count: int
    reserved_count: int
    scheduled_count: int
    queued_count: int
    oldest_queued_age_seconds: int | None


class AIOverviewKpiResponse(BaseModel):
    total_requests: int
    success_rate_pct: float
    total_tokens: int
    average_latency_ms: float
    p95_latency_ms: float
    active_runs: int
    queued_runs: int
    last_successful_run_at: datetime | None


class AIOverviewPerModelResponse(BaseModel):
    model: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    success_rate_pct: float
    total_tokens: int
    average_latency_ms: float
    last_request_at: datetime | None


class AITimeSeriesPointResponse(BaseModel):
    bucket: str
    requests: int
    failures: int
    total_tokens: int
    average_latency_ms: float
    p95_latency_ms: float
    daily_brief_successes: int
    daily_brief_failures: int
    daily_brief_skips: int


class AITokenEfficiencyResponse(BaseModel):
    average_prompt_tokens: float
    average_completion_tokens: float
    average_total_tokens: float
    prompt_to_completion_ratio: float
    top_expensive_feature: str | None
    top_expensive_feature_avg_tokens: float


class AIRelevanceFeedResponse(BaseModel):
    feed_name: str
    total_items: int
    high_count: int
    medium_count: int
    low_count: int
    average_score: float


class AIRelevanceDistributionResponse(BaseModel):
    high_count: int
    medium_count: int
    low_count: int
    average_score: float
    by_feed: list[AIRelevanceFeedResponse]


class AICoverageStatsResponse(BaseModel):
    eligible_items: int
    enriched_items: int
    pending_items: int
    failed_items: int
    skipped_no_article_count: int
    skipped_ai_disabled_count: int
    skipped_not_configured_count: int
    skipped_auto_enrich_disabled_count: int
    skipped_unchanged_count: int
    oldest_pending_at: datetime | None
    last_successful_enrichment_at: datetime | None
    last_successful_daily_brief_at: datetime | None
    last_ai_run_at: datetime | None


class AIFailureGroupResponse(BaseModel):
    task_type: str | None
    feature_type: str | None
    model: str | None
    error: str
    count: int
    last_seen_at: datetime | None


class AIEndpointHealthResponse(BaseModel):
    last_success_at: datetime | None
    last_error_at: datetime | None
    rolling_failure_rate_pct: float
    median_latency_ms: float
    timeout_failures: int
    last_auth_error: str | None
    last_provider_error: str | None


class AIFeatureHealthRowResponse(BaseModel):
    feature_key: str
    enabled: bool
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_status: str | None


class AIStorageStatsResponse(BaseModel):
    retained_daily_briefs: int
    daily_brief_history_limit: int
    enrichment_rows: int
    usage_event_rows: int
    task_history_rows: int
    growth_last_7d: int
    growth_last_30d: int


class AICacheStatsResponse(BaseModel):
    reused_count: int
    recomputed_count: int
    no_op_rate_pct: float


class AIOpsOverviewResponse(BaseModel):
    kpis: AIOverviewKpiResponse
    live: AILiveStatusResponse
    per_model: list[AIOverviewPerModelResponse]
    time_series: list[AITimeSeriesPointResponse]
    token_efficiency: AITokenEfficiencyResponse
    relevance_distribution: AIRelevanceDistributionResponse
    coverage: AICoverageStatsResponse
    failures: list[AIFailureGroupResponse]
    endpoint_health: AIEndpointHealthResponse
    feature_health: list[AIFeatureHealthRowResponse]
    storage: AIStorageStatsResponse
    cache: AICacheStatsResponse


class AIDailyBriefSourceItemResponse(BaseModel):
    id: uuid.UUID
    daily_brief_id: uuid.UUID
    item_id: uuid.UUID | None
    included: bool
    rank: int
    exclusion_reason: str | None
    title_snapshot: str
    feed_name_snapshot: str | None
    url_snapshot: str | None
    classification_snapshot: str | None
    relevance_score_snapshot: float | None
    relevance_label_snapshot: str | None
    published_at_snapshot: datetime | None
    first_seen_at_snapshot: datetime | None
    created_at: datetime


class AIAuditEntryResponse(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None = None
    action: str
    resource_type: str
    resource_id: str | None
    success: bool
    metadata: dict[str, object]
    created_at: datetime
