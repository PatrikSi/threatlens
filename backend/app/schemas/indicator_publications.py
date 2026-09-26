"""Explicit approval of an exact reviewed export preview."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.exports import ArticleExportFilters, ExportTLPMarking


class PublicationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: ArticleExportFilters = Field(default_factory=ArticleExportFilters)


class PublicationCreate(PublicationPreviewRequest):
    idempotency_key: uuid.UUID
    preview_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    format: Literal["stix", "misp"]
    marking: ExportTLPMarking = "TLP:AMBER"
    misp_distribution: int = Field(default=0, ge=0, le=3)


class ReviewedIndicatorPreview(BaseModel):
    assessment_id: uuid.UUID
    item_id: uuid.UUID
    ioc_id: uuid.UUID
    type: str
    value: str
    title: str
    assessment_version: int
    source_revision: int
    extraction_revision: int
    expires_at: datetime | None
    evidence_count: int


class PublicationPreview(BaseModel):
    fingerprint: str
    indicators: list[ReviewedIndicatorPreview]
    matched_articles: int
    excluded_or_unreviewed: int
    max_articles: int = 100
    max_indicators: int = 250


class PublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    team_id: uuid.UUID
    format: Literal["stix", "misp"]
    marking: str
    status: Literal["active", "partially_withdrawn", "withdrawn"]
    revision: int
    indicator_count: int
    withdrawn_count: int
    created_at: datetime
    updated_at: datetime


class PublicationPage(BaseModel):
    items: list[PublicationResponse]
    next_cursor: str | None
    has_more: bool


class PublicationWithdraw(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
