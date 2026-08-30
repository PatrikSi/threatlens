import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExportFormat = Literal["csv", "jsonl", "threat_bundle", "stix", "misp", "pdf_bundle"]
ExportDateBasis = Literal["first_seen_at", "published_at_or_first_seen_at"]
ExportSort = Literal[
    "published_at_desc", "published_at_asc", "first_seen_desc", "first_seen_asc"
]
ExportTagsMode = Literal["any", "all"]
ExportTLPMarking = Literal["none", "TLP:WHITE", "TLP:GREEN", "TLP:AMBER", "TLP:RED"]


class ExportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArticleExportFilters(ExportSchema):
    q: str | None = Field(default=None, max_length=500)
    feed_ids: list[uuid.UUID] = Field(default_factory=list, max_length=250)
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=250)
    tags_mode: ExportTagsMode = "any"
    classifications: list[str] = Field(default_factory=list, max_length=100)
    ai_relevance_labels: list[Literal["low", "medium", "high"]] = Field(
        default_factory=list, max_length=3
    )
    ai_score_min: float | None = Field(default=None, ge=0, le=1)
    ai_score_max: float | None = Field(default=None, ge=0, le=1)
    is_read: bool | None = None
    is_starred: bool | None = None
    has_article_text: bool | None = None
    since: datetime | None = None
    until: datetime | None = None
    date_basis: ExportDateBasis = "published_at_or_first_seen_at"
    sort: ExportSort = "published_at_desc"

    @field_validator("q", mode="before")
    @classmethod
    def _normalize_query(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("feed_ids", "tag_ids")
    @classmethod
    def _deduplicate_ids(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(values))

    @field_validator("classifications", mode="before")
    @classmethod
    def _normalize_classifications(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        normalized = [
            str(entry).strip().lower() for entry in value if str(entry).strip()
        ]
        return list(dict.fromkeys(normalized))

    @field_validator("since", "until")
    @classmethod
    def _normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("ai_relevance_labels")
    @classmethod
    def _deduplicate_relevance_labels(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _validate_ranges(self):
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must be earlier than or equal to until")
        if self.ai_score_min is not None and self.ai_score_max is not None:
            if self.ai_score_min > self.ai_score_max:
                raise ValueError(
                    "ai_score_min must be less than or equal to ai_score_max"
                )
        return self


class ArticleExportOptions(ExportSchema):
    include_article_text: bool = True
    csv_include_article_text: bool = False
    include_ai_details: bool = True
    include_tag_metadata: bool = True
    include_iocs: bool = True
    include_ioc_csv: bool = True
    include_user_state: bool = False
    include_user_notes: bool = False
    pdf_include_article_text: bool = True
    stix_marking: ExportTLPMarking = "TLP:WHITE"
    misp_distribution: int = Field(default=0, ge=0, le=3)
    filename_prefix: str | None = Field(default=None, max_length=80)

    @field_validator("filename_prefix", mode="before")
    @classmethod
    def _normalize_filename_prefix(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_state_options(self):
        if self.include_user_notes and not self.include_user_state:
            raise ValueError("include_user_notes requires include_user_state")
        return self


class ArticleExportPreviewRequest(ExportSchema):
    filters: ArticleExportFilters = Field(default_factory=ArticleExportFilters)


class ArticleExportRequest(ExportSchema):
    format: ExportFormat
    filters: ArticleExportFilters = Field(default_factory=ArticleExportFilters)
    options: ArticleExportOptions = Field(default_factory=ArticleExportOptions)


class ExportOptionEntry(ExportSchema):
    id: uuid.UUID
    name: str


class ExportFormatCapability(ExportSchema):
    id: ExportFormat
    label: str
    extension: str
    media_type: str
    description: str
    supports_article_text: bool
    supports_iocs: bool
    supports_user_state: bool


class ArticleExportCapabilitiesResponse(ExportSchema):
    formats: list[ExportFormatCapability]
    feeds: list[ExportOptionEntry]
    tags: list[ExportOptionEntry]
    classifications: list[str]
    max_items: int
    max_pdf_items: int
    max_uncompressed_bytes: int
    preview_limit: int


class ArticleExportPreviewItem(ExportSchema):
    id: uuid.UUID
    title: str
    url: str
    feed_name: str
    published_at: datetime | None
    first_seen_at: datetime
    classification: str | None
    ai_relevance_score: float | None
    ai_relevance_label: Literal["low", "medium", "high"] | None
    tags: list[str]
    is_read: bool
    is_starred: bool
    personal_state_available: bool = True
    has_article_text: bool
    ioc_count: int


class ArticleExportPreviewResponse(ExportSchema):
    total_matches: int
    articles_with_text: int
    items_with_iocs: int
    preview_limit: int
    exceeds_export_limit: bool
    exceeds_pdf_limit: bool
    items: list[ArticleExportPreviewItem]
