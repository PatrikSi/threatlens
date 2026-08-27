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


class OperationsDiagnosticsResponse(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    overview: OperationsOverviewResponse
    recent_runs: list[SystemOperationRunResponse]
    recent_runs_truncated: bool
