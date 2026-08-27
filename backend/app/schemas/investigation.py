import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


InvestigationStatus = Literal["open", "monitoring", "closed", "archived"]
InvestigationSeverity = Literal["low", "medium", "high", "critical"]
InvestigationVisibility = Literal["private", "team"]
InvestigationMemberRole = Literal["owner", "editor", "viewer"]
InvestigationEvidenceType = Literal["item", "ioc", "report", "alert_occurrence"]


class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10_000)
    severity: InvestigationSeverity = "medium"
    visibility: InvestigationVisibility = "private"
    assignee_user_id: uuid.UUID | None = None


class InvestigationUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    status: InvestigationStatus | None = None
    severity: InvestigationSeverity | None = None
    visibility: InvestigationVisibility | None = None
    disposition: str | None = Field(default=None, max_length=64)
    assignee_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_change(self):
        changed_fields = self.model_fields_set - {"expected_version"}
        if not changed_fields:
            raise ValueError("At least one investigation field must be provided.")
        return self


class InvestigationMemberAdd(BaseModel):
    user_id: uuid.UUID
    role: InvestigationMemberRole = "viewer"
    expected_version: int = Field(ge=1)


class InvestigationMemberUpdate(BaseModel):
    role: InvestigationMemberRole
    expected_version: int = Field(ge=1)


class InvestigationVersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class InvestigationEvidenceAdd(BaseModel):
    source_type: InvestigationEvidenceType
    source_id: uuid.UUID
    note: str | None = Field(default=None, max_length=2_000)
    expected_version: int = Field(ge=1)


class InvestigationNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    expected_version: int = Field(ge=1)


class InvestigationNoteUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    expected_note_version: int = Field(ge=1)
    expected_investigation_version: int = Field(ge=1)


class InvestigationMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: str
    role: InvestigationMemberRole
    created_at: datetime


class InvestigationMemberCandidate(BaseModel):
    id: uuid.UUID
    email: str
    account_role: str


class InvestigationMemberCandidateListResponse(BaseModel):
    users: list[InvestigationMemberCandidate]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)


class InvestigationEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: InvestigationEvidenceType
    source_id: uuid.UUID
    title_snapshot: str
    description_snapshot: str | None
    url_snapshot: str | None
    metadata_snapshot: dict
    note: str | None
    added_by_user_id: uuid.UUID | None
    created_at: datetime


class InvestigationNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_user_id: uuid.UUID | None
    author_email: str | None
    body: str
    version: int
    created_at: datetime
    updated_at: datetime


class InvestigationActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    details: dict
    created_at: datetime


class InvestigationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    status: InvestigationStatus
    severity: InvestigationSeverity
    visibility: InvestigationVisibility
    disposition: str | None
    assignee_user_id: uuid.UUID | None
    assignee_email: str | None
    current_user_role: InvestigationMemberRole | None
    evidence_count: int
    member_count: int
    note_count: int
    version: int
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    archived_at: datetime | None


class InvestigationDetailResponse(InvestigationSummaryResponse):
    members: list[InvestigationMemberResponse]
    evidence: list[InvestigationEvidenceResponse]
    notes: list[InvestigationNoteResponse]
    notes_truncated: bool


class InvestigationListResponse(BaseModel):
    investigations: list[InvestigationSummaryResponse]
    total: int
    page: int
    page_size: int


class InvestigationActivityListResponse(BaseModel):
    activities: list[InvestigationActivityResponse]
    total: int
    page: int
    page_size: int
