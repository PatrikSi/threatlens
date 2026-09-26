import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.team_assessments import HuntSuggestionResponse

HuntWorklistStatus = Literal["pending", "stale", "accepted", "rejected"]


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


class HuntWorklistPage(BaseModel):
    items: list[HuntWorklistEntry]
    next_cursor: str | None
    has_more: bool
    limit: int
