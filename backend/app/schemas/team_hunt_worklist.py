import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.storage_text import validate_storage_text

from app.schemas.team_assessments import HuntSuggestionResponse

HuntWorklistStatus = Literal["pending", "stale", "accepted", "rejected"]
HuntOrder = Literal["newest", "oldest", "due"]
HuntPriority = Literal["low", "normal", "high", "urgent"]


class HuntReviewSchedule(BaseModel):
    version: int = 0
    priority: HuntPriority = "normal"
    due_at: datetime | None = None
    overdue: bool = False
    reminded_at: datetime | None = None
    reminder_acknowledged_at: datetime | None = None


class HuntReviewScheduleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    expected_assessment_version: int = Field(ge=1)
    priority: HuntPriority = "normal"
    due_at: datetime | None = None

    @field_validator("due_at")
    @classmethod
    def aware_due(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.year < 2000 or value.year > 2100
        ):
            raise ValueError(
                "Review deadlines require a timezone and a year between 2000 and 2100"
            )
        return value


class HuntReminderAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_assessment_version: int = Field(ge=1)


class HuntViewFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: HuntWorklistStatus | None = None
    ownership: Literal["all", "mine", "unclaimed"] = "all"
    order: HuntOrder = "oldest"
    priority: HuntPriority | None = None
    overdue: bool = False


class HuntViewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(ge=0)
    filters: HuntViewFilters

    @field_validator("name", mode="before")
    @classmethod
    def safe_name(cls, value: object) -> object:
        return validate_storage_text(value).strip() if isinstance(value, str) else value


class HuntViewResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: int
    filters: HuntViewFilters


class HuntViewPage(BaseModel):
    items: list[HuntViewResponse]
    can_manage: bool


class HuntClaimCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    expected_assessment_version: int = Field(ge=1)
    action: Literal["claim", "unclaim"]


class HuntClaimResponse(BaseModel):
    version: int
    owner_user_id: uuid.UUID | None


class HuntInvestigationOutcome(BaseModel):
    id: uuid.UUID
    status: str
    disposition: str | None
    assignee_user_id: uuid.UUID | None


class HuntWorklistEntry(BaseModel):
    assessment_id: uuid.UUID
    assessment_version: int
    item_id: uuid.UUID
    item_title: str
    team_id: uuid.UUID
    status: HuntWorklistStatus
    generated_at: datetime | None
    evidence_age_seconds: int | None
    hunt: HuntSuggestionResponse
    claim: HuntClaimResponse
    owner_name: str | None
    reviewer_name: str | None
    reviewed_at: datetime | None
    can_claim: bool
    can_release: bool
    investigation: HuntInvestigationOutcome | None
    review_schedule: HuntReviewSchedule = Field(default_factory=HuntReviewSchedule)
    can_schedule: bool = False


class HuntWorklistPage(BaseModel):
    items: list[HuntWorklistEntry]
    next_cursor: str | None
    has_more: bool
    limit: int
