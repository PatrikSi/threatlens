"""Team assessment API contracts; provider output has a separate contract."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ai_extraction import EvidenceSource, ExtractionCoverage
from app.schemas.storage_text import validate_storage_text


class TeamAssessmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: uuid.UUID
    expected_version: int = Field(ge=0)


class HuntReviewCommand(TeamAssessmentCommand):
    status: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def storage_safe_note(cls, value: object) -> object:
        return validate_storage_text(value).strip() if isinstance(value, str) else value


class HuntEvidenceResponse(BaseModel):
    source: EvidenceSource
    quote: str


class HuntSuggestionResponse(BaseModel):
    id: str
    title: str
    hypothesis: str
    rationale: str
    required_logs: list[str]
    benign_explanations: list[str]
    information_gaps: list[str]
    evidence: list[HuntEvidenceResponse]
    attack_technique_ids: list[str]
    detection_strategy_ids: list[str]
    review_status: Literal["suggested", "accepted", "rejected"] = "suggested"
    review_note: str | None = None
    investigation_id: uuid.UUID | None = None
    reviewed_by_user_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None


class SelectedEvidenceRange(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class AssessmentEvidenceSelection(BaseModel):
    selection: Literal["article_prefix", "verified_section_passages"]
    selected_passages: list[SelectedEvidenceRange] = Field(default_factory=list, max_length=576)
    source_hash: str | None = None
    source_version: int | None = None
    coverage: ExtractionCoverage | None = None


class TeamAssessmentResultResponse(BaseModel):
    relevance_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    relevance_reasons: list[str]
    information_gaps: list[str]
    hunts: list[HuntSuggestionResponse]
    evidence_selection: AssessmentEvidenceSelection | None = None


class TeamAssessmentResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    item_id: uuid.UUID
    version: int
    status: str
    stale: bool
    error: str | None
    generated_at: datetime | None
    context_version: int
    result: TeamAssessmentResultResponse | None


class TeamAssessmentEnvelope(BaseModel):
    assessment: TeamAssessmentResponse | None
    ai_enabled: bool
    configured: bool
    hunt_suggestions_enabled: bool
    can_generate: bool
