from datetime import datetime

from pydantic import BaseModel, Field


class AIFeatureStatistics(BaseModel):
    feature: str
    requests: int
    successful: int
    failed: int
    known_usage_requests: int
    unknown_usage_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    not_sent: int
    ambiguous: int
    deadline_failures: int
    timeout_failures: int
    truncated_outputs: int
    budget_rejections: int
    latency_samples: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    p99_latency_ms: float | None


class AIQueueStatistics(BaseModel):
    feature: str
    queued: int
    running: int
    oldest_queued_at: datetime | None
    oldest_running_at: datetime | None


class AIStatisticsResponse(BaseModel):
    since: datetime
    until: datetime
    days: int
    features: list[AIFeatureStatistics]
    queues: list[AIQueueStatistics]
    provider_retry_attempts: int
    recovered_pre_io_failures: int
    latency_histogram: dict[str, int] = Field(default_factory=dict)
