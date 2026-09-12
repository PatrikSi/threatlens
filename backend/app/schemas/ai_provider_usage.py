from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AIProviderUsageRow(BaseModel):
    provider_id: uuid.UUID | None
    provider_version: int | None
    provider_name: str
    model: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    success_rate_pct: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    unknown_token_requests: int
    average_latency_ms: float | None
    p95_latency_ms: float | None
    not_sent_requests: int
    ambiguous_requests: int
    deadline_failures: int
    failure_categories: dict[str, int]
    last_request_at: datetime


class AIProviderUsageResponse(BaseModel):
    items: list[AIProviderUsageRow]
    total: int
    days: int
    limit: int
    offset: int
