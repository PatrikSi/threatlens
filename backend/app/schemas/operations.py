from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SystemOperationType = Literal["backup", "verify", "restore_drill", "restore", "diagnostics"]
SystemOperationStatus = Literal["running", "succeeded", "failed"]
OperationsStatus = Literal["healthy", "degraded", "critical", "unavailable", "unknown"]
OperationsIssueSeverity = Literal["warning", "critical"]
OperationsMetricValue = bool | int | float | str | None | list[str]
WorkerProbeName = Literal[
    "ping",
    "active_queues",
    "stats",
    "active",
    "reserved",
    "scheduled",
]
WorkerProbeQuality = Literal[
    "complete",
    "partial",
    "no_replies",
    "failed",
    "invalid",
]
WorkerTopologyReason = Literal[
    "healthy",
    "no_replies",
    "probe_failed",
    "queue_inventory_unavailable",
    "partial_inventory",
    "missing_consumers",
    "canary_dispatch_unavailable",
    "execution_evidence_missing",
    "execution_stalled",
    "saturated",
]
CanaryDispatchReason = Literal[
    "healthy",
    "missing",
    "stale",
    "invalid",
    "future",
    "redis_unavailable",
]
QueueExecutionReason = Literal[
    "fresh",
    "missing",
    "stale",
    "invalid",
    "future",
    "redis_unavailable",
]
HealthHistoryWindow = Literal["1h", "6h", "24h", "7d", "30d"]
HealthHistoryDownsamplingStrategy = Literal[
    "none",
    "transition_anomaly_preserving",
]
HealthHistoryGapKind = Literal["leading", "internal", "trailing", "window"]


class SystemOperationRunResponse(BaseModel):
    id: uuid.UUID
    operation_type: SystemOperationType
    status: SystemOperationStatus
    initiated_by: str
    source: str
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None
    error_message: str | None


class SystemOperationRunListResponse(BaseModel):
    runs: list[SystemOperationRunResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=1_000_000)
    page_size: int = Field(ge=1, le=100)


class OperationsApplicationInfo(BaseModel):
    version: str
    schema_revision: str | None
    expected_schema_revision: str
    schema_current: bool | None


class OperationsComponentCheck(BaseModel):
    key: str
    label: str
    status: OperationsStatus
    summary: str
    checked_at: datetime
    metrics: dict[str, OperationsMetricValue] = Field(default_factory=dict)


class OperationsWorkerProbeEvidence(BaseModel):
    probe: WorkerProbeName
    quality: WorkerProbeQuality
    responder_count: int = Field(default=0, ge=0, le=64)
    observed_responder_count: int = Field(default=0, ge=0, le=1_000_000)
    responses_truncated: bool = False
    missing_responder_count: int = Field(default=0, ge=0, le=64)
    invalid_response_count: int = Field(default=0, ge=0, le=64)
    duration_ms: int = Field(default=0, ge=0, le=120_000)


class OperationsWorkerNode(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    queues: list[str] = Field(default_factory=list, max_length=16)
    responded_to: list[WorkerProbeName] = Field(default_factory=list, max_length=6)
    missing_responses: list[WorkerProbeName] = Field(default_factory=list, max_length=6)
    ping_ok: bool | None = None
    capacity: int | None = Field(default=None, ge=0, le=1_000_000)
    active_count: int | None = Field(default=None, ge=0, le=1_000_000)
    reserved_count: int | None = Field(default=None, ge=0, le=1_000_000)
    scheduled_count: int | None = Field(default=None, ge=0, le=1_000_000)
    processed_total: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    uptime_seconds: int | None = Field(default=None, ge=0, le=1_000_000_000)
    saturated: bool = False


class OperationsWorkerQueue(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    service_hint: str = Field(min_length=1, max_length=64)
    required: bool
    status: OperationsStatus
    consumers: list[str] = Field(default_factory=list, max_length=64)
    consumer_count: int = Field(default=0, ge=0, le=64)
    capacity: int | None = Field(default=None, ge=0, le=1_000_000)
    active_count: int | None = Field(default=None, ge=0, le=1_000_000)
    reserved_count: int | None = Field(default=None, ge=0, le=1_000_000)
    scheduled_count: int | None = Field(default=None, ge=0, le=1_000_000)
    saturated: bool = False
    execution_reason: QueueExecutionReason
    execution_heartbeat_at: datetime | None = None
    execution_age_seconds: int | None = Field(default=None, ge=0, le=1_000_000_000)
    execution_worker: str | None = Field(default=None, max_length=255)


class OperationsWorkerTopologyResponse(BaseModel):
    generated_at: datetime
    status: OperationsStatus
    reason: WorkerTopologyReason
    timeout_seconds: float = Field(gt=0, le=8)
    duration_ms: int = Field(default=0, ge=0, le=600_000)
    responding_worker_count: int = Field(default=0, ge=0, le=64)
    observed_worker_count: int = Field(default=0, ge=0, le=1_000_000)
    worker_inventory_truncated: bool = False
    total_capacity: int | None = Field(default=None, ge=0, le=1_000_000)
    active_count: int | None = Field(default=None, ge=0, le=1_000_000)
    reserved_count: int | None = Field(default=None, ge=0, le=1_000_000)
    scheduled_count: int | None = Field(default=None, ge=0, le=1_000_000)
    missing_queues: list[str] = Field(default_factory=list, max_length=16)
    stale_execution_queues: list[str] = Field(default_factory=list, max_length=16)
    missing_execution_evidence_queues: list[str] = Field(
        default_factory=list,
        max_length=16,
    )
    canary_dispatch_ok: bool
    canary_dispatch_reason: CanaryDispatchReason
    canary_dispatch_heartbeat_at: datetime | None = None
    canary_dispatch_age_seconds: int | None = Field(
        default=None,
        ge=0,
        le=1_000_000_000,
    )
    probes: list[OperationsWorkerProbeEvidence] = Field(
        default_factory=list,
        max_length=6,
    )
    workers: list[OperationsWorkerNode] = Field(default_factory=list, max_length=64)
    queues: list[OperationsWorkerQueue] = Field(default_factory=list, max_length=16)


class OperationsStorageIndicator(BaseModel):
    key: str
    label: str
    status: OperationsStatus
    used_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    available_bytes: int | None = Field(default=None, ge=0)
    percent_used: float | None = Field(default=None, ge=0, le=100)


class OperationsBacklogSnapshot(BaseModel):
    key: str
    label: str
    status: OperationsStatus
    pending_count: int = Field(default=0, ge=0)
    active_count: int = Field(default=0, ge=0)
    stale_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    oldest_pending_age_seconds: int | None = Field(default=None, ge=0)
    degraded_after_seconds: int = Field(ge=1)


class OperationsRecoverySnapshot(BaseModel):
    latest_backup: SystemOperationRunResponse | None = None
    latest_verify: SystemOperationRunResponse | None = None
    latest_restore_drill: SystemOperationRunResponse | None = None
    latest_restore: SystemOperationRunResponse | None = None


class OperationsIssue(BaseModel):
    code: str
    severity: OperationsIssueSeverity
    component: str
    summary: str
    effect: str
    recommended_action: str


class OperationsOverviewResponse(BaseModel):
    generated_at: datetime
    overall_status: OperationsStatus
    application: OperationsApplicationInfo
    components: list[OperationsComponentCheck]
    storage: list[OperationsStorageIndicator]
    backlogs: list[OperationsBacklogSnapshot]
    recovery: OperationsRecoverySnapshot
    issues: list[OperationsIssue]


class OperationsHealthHistoryPoint(BaseModel):
    sampled_at: datetime
    overall_status: OperationsStatus
    component_statuses: dict[str, OperationsStatus] = Field(
        default_factory=dict,
        max_length=16,
    )
    worker_status: OperationsStatus
    worker_reason: WorkerTopologyReason
    responding_worker_count: int = Field(default=0, ge=0, le=64)
    observed_worker_count: int = Field(default=0, ge=0, le=1_000_000)
    worker_inventory_truncated: bool = False
    total_capacity: int | None = Field(default=None, ge=0, le=1_000_000)
    active_count: int | None = Field(default=None, ge=0, le=1_000_000)
    reserved_count: int | None = Field(default=None, ge=0, le=1_000_000)
    scheduled_count: int | None = Field(default=None, ge=0, le=1_000_000)
    missing_queues: list[str] = Field(default_factory=list, max_length=16)
    stale_execution_queues: list[str] = Field(default_factory=list, max_length=16)
    backlog_pending_count: int = Field(default=0, ge=0, le=2_000_000_000)
    backlog_stale_count: int = Field(default=0, ge=0, le=2_000_000_000)
    critical_issue_count: int = Field(default=0, ge=0, le=2_000_000_000)
    warning_issue_count: int = Field(default=0, ge=0, le=2_000_000_000)
    issue_codes: list[str] = Field(default_factory=list, max_length=32)


class OperationsHealthHistoryGapInterval(BaseModel):
    start_at: datetime
    end_at: datetime
    duration_seconds: int = Field(ge=0, le=3_200_000)
    kind: HealthHistoryGapKind


class OperationsHealthHistoryCoverage(BaseModel):
    requested_start: datetime
    requested_end: datetime
    first_sample_at: datetime | None = None
    last_sample_at: datetime | None = None
    expected_sample_count: int = Field(default=0, ge=0)
    actual_sample_count: int = Field(default=0, ge=0)
    returned_sample_count: int = Field(default=0, ge=0, le=720)
    missing_sample_count: int = Field(default=0, ge=0)
    coverage_percent: float = Field(default=0, ge=0, le=100)
    gap_count: int = Field(default=0, ge=0)
    returned_gap_interval_count: int = Field(default=0, ge=0, le=720)
    gap_intervals: list[OperationsHealthHistoryGapInterval] = Field(
        default_factory=list,
        max_length=720,
    )
    gap_intervals_truncated: bool = False
    largest_gap_seconds: int | None = Field(default=None, ge=0)
    collection_stale: bool = True
    source_anomaly_sample_count: int = Field(default=0, ge=0)
    returned_anomaly_sample_count: int = Field(default=0, ge=0, le=720)
    source_transition_count: int = Field(default=0, ge=0)
    returned_transition_count: int = Field(default=0, ge=0, le=720)
    anomaly_evidence_truncated: bool = False
    transition_evidence_truncated: bool = False


class OperationsHealthHistoryResponse(BaseModel):
    generated_at: datetime
    window: HealthHistoryWindow
    sample_interval_seconds: Literal[300] = 300
    effective_resolution_seconds: int = Field(default=300, ge=300)
    downsampling_strategy: HealthHistoryDownsamplingStrategy = "none"
    retention_days: int = Field(ge=1, le=3650)
    coverage: OperationsHealthHistoryCoverage
    samples: list[OperationsHealthHistoryPoint] = Field(
        default_factory=list,
        max_length=720,
    )


class OperationsDiagnosticsResponse(BaseModel):
    schema_version: Literal[2] = 2
    generated_at: datetime
    overview: OperationsOverviewResponse
    worker_topology: OperationsWorkerTopologyResponse
    health_history: OperationsHealthHistoryResponse
    recent_runs: list[SystemOperationRunResponse]
    recent_runs_truncated: bool
