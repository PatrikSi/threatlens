from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LifecycleTargetKey = Literal[
    "article_content",
    "audit_logs",
    "action_approval_history",
    "ai_task_history",
    "ai_usage_history",
    "tag_feedback_history",
    "integration_run_history",
    "inactive_auth_sessions",
    "system_health_samples",
    "integration_delivery_history",
    "integration_event_history",
    "integration_metrics",
    "closed_alert_history",
    "alert_activity_history",
    "alert_evaluation_history",
    "alert_metrics",
]
LifecycleCategory = Literal[
    "intelligence",
    "detection",
    "integrations",
    "security",
    "governance",
    "system",
]
LifecycleScheduleCadence = Literal["daily", "weekly"]
LifecycleRunTrigger = Literal["manual", "scheduled"]
LifecycleRunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
]


LifecycleOptionKey = Literal[
    "protect_starred",
    "protect_notes",
    "protect_investigations",
    "protect_reports",
    "protect_active_alerts",
]


class LifecyclePolicyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    retention_days: int = Field(ge=1, le=3650)
    schedule_cadence: LifecycleScheduleCadence
    schedule_hour_utc: int = Field(ge=0, le=23)
    schedule_weekday: int | None = Field(default=None, ge=0, le=6)
    max_records_per_run: int = Field(ge=100, le=100_000)
    options: dict[LifecycleOptionKey, bool] = Field(default_factory=dict, max_length=5)

    @model_validator(mode="after")
    def _validate_schedule_shape(self):
        if self.schedule_cadence == "weekly" and self.schedule_weekday is None:
            raise ValueError("schedule_weekday is required for a weekly schedule")
        if self.schedule_cadence == "daily" and self.schedule_weekday is not None:
            raise ValueError("schedule_weekday must be omitted for a daily schedule")
        return self


class LifecyclePolicyUpdateRequest(LifecyclePolicyDraft):
    expected_revision: int = Field(ge=1)
    preview_id: uuid.UUID | None = None
    confirmation: Literal["PURGE"] | None = None
    reason: str | None = Field(default=None, min_length=10, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LifecyclePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_key: LifecycleTargetKey
    expected_revision: int = Field(ge=1)
    draft: LifecyclePolicyDraft


class LifecycleRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_key: LifecycleTargetKey
    expected_revision: int = Field(ge=1)
    preview_id: uuid.UUID
    confirmation: Literal["PURGE"]
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LifecycleRunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LifecycleSafeguardResponse(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    default_enabled: bool


class LifecyclePolicyResponse(BaseModel):
    target_key: LifecycleTargetKey
    enabled: bool
    retention_days: int = Field(ge=1, le=3650)
    schedule_cadence: LifecycleScheduleCadence
    schedule_hour_utc: int = Field(ge=0, le=23)
    schedule_weekday: int | None = Field(default=None, ge=0, le=6)
    max_records_per_run: int = Field(ge=100, le=100_000)
    options: dict[str, bool] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_status: LifecycleRunStatus | None
    configuration_updated_at: datetime
    updated_at: datetime
    updated_by: str | None = Field(default=None, max_length=320)


class LifecyclePreviewResponse(BaseModel):
    id: uuid.UUID
    target_key: LifecycleTargetKey
    policy_revision: int = Field(ge=1)
    cutoff_at: datetime
    eligible_count: int = Field(ge=0)
    protected_count: int = Field(ge=0)
    protected_counts: dict[str, int] = Field(default_factory=dict)
    oldest_candidate_at: datetime | None
    eligible_bytes: int | None = Field(default=None, ge=0)
    count_is_lower_bound: bool
    is_partial: bool
    generated_at: datetime
    expires_at: datetime
    observed_at: datetime


class LifecycleTargetResponse(BaseModel):
    key: LifecycleTargetKey
    label: str = Field(min_length=1, max_length=160)
    category: LifecycleCategory
    description: str = Field(min_length=1, max_length=1_000)
    action_description: str = Field(min_length=1, max_length=1_000)
    cutoff_description: str = Field(min_length=1, max_length=500)
    min_retention_days: int = Field(ge=1, le=3650)
    max_retention_days: int = Field(ge=1, le=3650)
    default_retention_days: int = Field(ge=1, le=3650)
    safeguards: list[LifecycleSafeguardResponse] = Field(default_factory=list)
    policy: LifecyclePolicyResponse
    latest_preview: LifecyclePreviewResponse | None = None


class LifecycleOverviewResponse(BaseModel):
    generated_at: datetime
    targets: list[LifecycleTargetResponse]


LifecycleCatalogResponse = LifecycleOverviewResponse


class LifecycleRunResponse(BaseModel):
    id: uuid.UUID
    target_key: LifecycleTargetKey
    trigger_source: LifecycleRunTrigger
    status: LifecycleRunStatus
    policy_revision: int = Field(ge=1)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    cutoff_at: datetime
    scheduled_for: datetime | None
    max_records: int = Field(ge=100, le=100_000)
    reason: str | None
    requested_by: str | None = Field(default=None, max_length=320)
    evaluated_count: int = Field(ge=0)
    affected_count: int = Field(ge=0)
    protected_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    batch_count: int = Field(ge=0)
    remaining_count: int | None = Field(default=None, ge=0)
    affected_bytes: int | None = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str | None
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    cancellation_requested_at: datetime | None
    cancellation_requested_by: str | None = Field(default=None, max_length=320)
    cancellation_reason: str | None = Field(default=None, max_length=500)
    queued_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


LifecycleRunRequest = LifecycleRunCreateRequest


class LifecycleRunListResponse(BaseModel):
    runs: list[LifecycleRunResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=1_000_000)
    page_size: int = Field(ge=1, le=100)
