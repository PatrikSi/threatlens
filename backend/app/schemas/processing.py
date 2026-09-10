from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, Field

ProcessingStage = Literal["article", "classification", "ioc", "tagging"]
ProcessingState = Literal["pending", "queued", "running", "retry_wait", "attention"]


class ProcessingWorkResponse(BaseModel):
    item_id: uuid.UUID
    stage: ProcessingStage
    revision: str
    title: str
    feed_id: uuid.UUID
    feed_name: str
    state: ProcessingState
    reason: str | None
    message: str | None
    first_seen_at: datetime
    age_seconds: int
    attempts: int
    next_retry_at: datetime | None
    can_retry: bool


class ProcessingWorkList(BaseModel):
    items: list[ProcessingWorkResponse]
    next_cursor: str | None
    has_more: bool


class ProcessingSelection(BaseModel):
    item_id: uuid.UUID
    stage: ProcessingStage
    revision: str = Field(min_length=1, max_length=64)


class ProcessingRecoveryRequest(BaseModel):
    idempotency_key: uuid.UUID
    items: list[ProcessingSelection] = Field(min_length=1, max_length=100)


class ProcessingRecoveryCancel(BaseModel):
    expected_version: int = Field(ge=1)


class ProcessingRecoveryItemResponse(BaseModel):
    item_id: uuid.UUID
    stage: ProcessingStage
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    reason: str | None
    message: str | None
    title: str | None
    feed_name: str | None


class ProcessingRecoveryResponse(BaseModel):
    id: uuid.UUID
    version: int
    status: Literal["queued", "running", "succeeded", "partial", "cancelled", "failed"]
    created_at: datetime
    updated_at: datetime
    total_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    can_cancel: bool
    access_limited: bool = False
    items: list[ProcessingRecoveryItemResponse]


class ProcessingRecoveryList(BaseModel):
    items: list[ProcessingRecoveryResponse]
    next_cursor: str | None
    has_more: bool
