"""Public evidence and review contracts; confidence dimensions never substitute."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.storage_text import StorageTextInput

IndicatorVerdict = Literal[
    "unreviewed", "malicious", "benign", "reference", "example", "retracted"
]
IndicatorType = Literal[
    "hash_sha256",
    "hash_sha1",
    "hash_md5",
    "ipv4",
    "ipv6",
    "domain",
    "url",
    "email",
    "cve",
    "vendor",
    "program",
]


class IndicatorEvidence(BaseModel):
    source: str
    raw: str
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    transformations: list[str] = Field(default_factory=list)


class IndicatorAI(BaseModel):
    role: str
    assertion: str
    evidence: list[dict]
    maliciousness_confidence: float | None = None


class AssessmentResponse(BaseModel):
    version: int
    verdict: IndicatorVerdict
    reason: str
    source_revision: int
    extraction_revision: int
    expires_at: datetime | None
    expired: bool
    current: bool
    updated_at: datetime


class IndicatorResponse(BaseModel):
    id: UUID
    type: str
    value: str
    raw: str
    extraction_confidence: float
    occurrences: int = 1
    evidence_truncated: bool = False
    evidence: list[IndicatorEvidence]
    ai: IndicatorAI | None = None
    ai_current: bool = False
    assessment: AssessmentResponse | None = None
    excluded: bool
    exclusion_reasons: list[str]
    suppressed: bool


class IndicatorPage(BaseModel):
    items: list[IndicatorResponse]
    total: int
    page: int
    page_size: int
    source_revision: int
    extraction_revision: int
    extraction_current: bool = False
    can_review: bool = False
    can_manage_suppressions: bool = False


class ExpiringCommand(StorageTextInput):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    expires_at: datetime | None = None

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required.")
        return value.strip()

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value <= datetime.now(timezone.utc)
        ):
            raise ValueError("Expiry must be a future timestamp with a timezone.")
        return value


class AssessmentCommand(ExpiringCommand):
    expected_version: int = Field(ge=0)
    source_revision: int = Field(ge=1)
    extraction_revision: int = Field(ge=0)
    verdict: IndicatorVerdict


class SuppressionCreate(ExpiringCommand):
    ioc_type: IndicatorType
    value: str = Field(min_length=1, max_length=4096)
    active: bool = True


class SuppressionUpdate(ExpiringCommand):
    expected_version: int = Field(ge=1)
    active: bool


class SuppressionResponse(BaseModel):
    id: UUID
    team_id: UUID
    ioc_type: str
    value: str
    version: int
    reason: str
    active: bool
    expires_at: datetime | None
    expired: bool
    updated_at: datetime


class SuppressionPage(BaseModel):
    items: list[SuppressionResponse]
    total: int
    page: int
    page_size: int
    can_manage: bool


class IndicatorHistoryEntry(BaseModel):
    version: int
    snapshot: dict
    actor_user_id: UUID | None
    created_at: datetime


class IndicatorHistoryPage(BaseModel):
    items: list[IndicatorHistoryEntry]
    total: int
    page: int
    page_size: int
