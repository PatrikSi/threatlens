import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.item import ItemListEntry


class AlertInterestCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=64)
    keywords: list[str] = Field(min_length=1, max_length=64)
    enabled: bool = True
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    suppression_until: datetime | None = None
    suppression_reason: str | None = Field(default=None, max_length=500)

    @field_validator("suppression_until")
    @classmethod
    def normalize_suppression_until(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_suppression(self):
        _validate_suppression_pair(self.suppression_until, self.suppression_reason)
        return self


class AlertInterestUpdate(BaseModel):
    # expected_revision remains as a compatibility alias for expected_row_version.
    expected_revision: int | None = Field(default=None, ge=1)
    expected_row_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    keywords: list[str] | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    suppression_until: datetime | None = None
    suppression_reason: str | None = Field(default=None, max_length=500)

    @field_validator("suppression_until")
    @classmethod
    def normalize_suppression_until(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_expected_versions(self):
        if (
            self.expected_revision is not None
            and self.expected_row_version is not None
            and self.expected_revision != self.expected_row_version
        ):
            raise ValueError(
                "expected_revision and expected_row_version must match when both are supplied."
            )
        return self


class AlertInterestPreviewRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    category: str = Field(min_length=1, max_length=64)
    keywords: list[str] = Field(min_length=1, max_length=64)
    limit: int = Field(default=5, ge=1, le=25)


class AlertInterestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    category: str
    keywords: list[str]
    enabled: bool
    severity: Literal["low", "medium", "high", "critical"]
    revision: int
    row_version: int
    durable_since: datetime | None
    suppression_until: datetime | None
    suppression_reason: str | None
    created_at: datetime
    updated_at: datetime


class AlertMatchReference(BaseModel):
    alert_id: uuid.UUID
    alert_name: str
    category: str
    matched_keywords: list[str]


class AlertMatchEntry(ItemListEntry):
    matches: list[AlertMatchReference]


class AlertMatchListResponse(BaseModel):
    items: list[AlertMatchEntry]
    total: int
    page: int
    page_size: int


AlertOccurrenceState = Literal["new", "acknowledged", "investigating", "closed"]
AlertSeverity = Literal["low", "medium", "high", "critical"]
AlertClosureDisposition = Literal[
    "true_positive",
    "false_positive",
    "benign",
    "duplicate",
    "informational",
    "other",
]


class AlertOccurrenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alert_interest_id: uuid.UUID | None
    rule_id_snapshot: uuid.UUID
    owner_user_id: uuid.UUID
    item_id: uuid.UUID | None
    item_id_snapshot: uuid.UUID
    integration_event_id: uuid.UUID | None
    rule_revision: int
    item_content_hash: str
    alert_name_snapshot: str
    alert_category_snapshot: str
    alert_keywords_snapshot: list[str]
    matched_keywords: list[str]
    source_snapshot_json: dict
    severity_snapshot: AlertSeverity
    lifecycle_state: AlertOccurrenceState
    is_suppressed: bool
    suppressed_at: datetime | None
    suppression_reason: str | None
    is_snoozed: bool
    snoozed_until: datetime | None
    snooze_reason: str | None
    closure_disposition: str | None
    acknowledged_at: datetime | None
    acknowledged_by_user_id: uuid.UUID | None
    investigating_at: datetime | None
    investigating_by_user_id: uuid.UUID | None
    closed_at: datetime | None
    closed_by_user_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class AlertOccurrenceListResponse(BaseModel):
    items: list[AlertOccurrenceResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class AlertOccurrenceActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurrence_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    details_json: dict
    created_at: datetime


class AlertOccurrenceActivityListResponse(BaseModel):
    items: list[AlertOccurrenceActivityResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class AlertOccurrenceLifecycleUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    state: AlertOccurrenceState
    disposition: AlertClosureDisposition | None = None

    @model_validator(mode="after")
    def require_closed_disposition(self):
        if self.state == "closed" and self.disposition is None:
            raise ValueError(
                "A disposition is required when closing an alert occurrence."
            )
        if self.state != "closed" and self.disposition is not None:
            raise ValueError(
                "A disposition can be supplied only when closing an alert occurrence."
            )
        return self


class AlertOccurrenceSnoozeUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    snoozed_until: datetime | None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("snoozed_until")
    @classmethod
    def normalize_snoozed_until(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_snooze(self):
        if self.snoozed_until is None:
            if self.reason is not None:
                raise ValueError(
                    "A snooze reason cannot be retained when clearing a snooze."
                )
            return self
        if self.snoozed_until <= datetime.now(timezone.utc):
            raise ValueError("snoozed_until must be in the future.")
        if not (self.reason or "").strip():
            raise ValueError("A reason is required when snoozing an alert occurrence.")
        return self


class AlertOccurrenceBulkItem(BaseModel):
    occurrence_id: uuid.UUID
    expected_version: int = Field(ge=1)


class AlertOccurrenceBulkUpdate(BaseModel):
    items: list[AlertOccurrenceBulkItem] = Field(min_length=1, max_length=100)
    disposition: AlertClosureDisposition | None = None

    @model_validator(mode="after")
    def reject_duplicate_ids(self):
        ids = [entry.occurrence_id for entry in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "Bulk occurrence requests cannot contain duplicate occurrence IDs."
            )
        return self


class AlertOccurrenceBulkResponse(BaseModel):
    items: list[AlertOccurrenceResponse]
    updated: int = Field(ge=0, le=100)


class AlertBackfillRequest(BaseModel):
    since: datetime
    until: datetime
    limit: int = Field(default=100, ge=1, le=500)
    cursor_first_seen_at: datetime | None = None
    cursor_item_id: uuid.UUID | None = None

    @field_validator("since", "until", "cursor_first_seen_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return _as_utc(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def validate_window(self):
        if self.since > self.until:
            raise ValueError("since must be earlier than or equal to until.")
        if self.until - self.since > timedelta(days=90):
            raise ValueError("Backfill windows cannot exceed 90 days.")
        if (self.cursor_first_seen_at is None) != (self.cursor_item_id is None):
            raise ValueError(
                "cursor_first_seen_at and cursor_item_id must be supplied together."
            )
        if self.cursor_first_seen_at is not None and not (
            self.since <= self.cursor_first_seen_at <= self.until
        ):
            raise ValueError("The backfill cursor must be within the requested window.")
        return self


class AlertBackfillCandidate(BaseModel):
    item_id: uuid.UUID
    content_hash: str
    title: str
    first_seen_at: datetime


class AlertBackfillPreviewResponse(BaseModel):
    preview_token: uuid.UUID
    expires_at: datetime
    candidates: list[AlertBackfillCandidate]
    matched_count: int = Field(ge=0)
    returned_count: int = Field(ge=0, le=500)
    truncated: bool
    has_more: bool
    next_cursor_first_seen_at: datetime | None = None
    next_cursor_item_id: uuid.UUID | None = None
    notifications_enabled: Literal[False] = False


class AlertBackfillApplyRequest(BaseModel):
    preview_token: uuid.UUID


class AlertBackfillApplyResponse(BaseModel):
    accepted: int = Field(ge=0, le=500)
    existing: int = Field(ge=0, le=500)
    skipped: int = Field(default=0, ge=0, le=500)
    enqueue_failed: bool
    has_more: bool
    next_cursor_first_seen_at: datetime | None = None
    next_cursor_item_id: uuid.UUID | None = None
    notifications_enabled: Literal[False] = False


AlertEvaluationState = Literal[
    "pending", "processing", "retry_wait", "succeeded", "dead_letter"
]
AlertEvaluationSource = Literal["live", "reconciliation", "backfill", "replay"]


class AlertEvaluationRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    item_content_hash: str
    state: AlertEvaluationState
    source: Literal["live", "reconciliation", "backfill"]
    active_source: AlertEvaluationSource
    notify: bool
    respect_rule_cutover: bool
    attempt_count: int
    max_attempts: int
    dispatch_attempt_count: int
    dispatch_failure_count: int
    version: int
    accepted_rule_count: int
    accepted_match_count: int
    degraded_owner_count: int
    degraded_owners_json: list[dict]
    evaluated_rule_count: int
    occurrence_count: int
    backfill_count: int
    accepted_at: datetime
    available_at: datetime
    dispatch_claimed_at: datetime | None
    last_dispatch_failed_at: datetime | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    completed_at: datetime | None
    last_backfill_at: datetime | None
    last_replayed_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class AlertEvaluationRequestListResponse(BaseModel):
    items: list[AlertEvaluationRequestResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class AlertEvaluationActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    details_json: dict
    created_at: datetime


class AlertEvaluationActivityListResponse(BaseModel):
    items: list[AlertEvaluationActivityResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class AlertEvaluationReplayRequest(BaseModel):
    expected_version: int = Field(ge=1)


class AlertEvaluationReplayResponse(BaseModel):
    request: AlertEvaluationRequestResponse
    enqueue_failed: bool


class AlertOccurrenceMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bucket_start: datetime
    owner_user_id: uuid.UUID
    severity: AlertSeverity
    lifecycle_state: AlertOccurrenceState
    suppressed: bool
    occurrence_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class AlertOccurrenceMetricListResponse(BaseModel):
    items: list[AlertOccurrenceMetricResponse]
    truncated: bool = False


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_suppression_pair(until: datetime | None, reason: str | None) -> None:
    if until is None:
        if reason is not None:
            raise ValueError("A suppression reason requires suppression_until.")
        return
    if until <= datetime.now(timezone.utc):
        raise ValueError("suppression_until must be in the future.")
    if not (reason or "").strip():
        raise ValueError("A reason is required when suppressing alert notifications.")
