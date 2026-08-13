import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ExportTag:
    id: uuid.UUID
    name: str
    source: str
    confidence: float
    rules_version: str | None


@dataclass(frozen=True)
class ExportIOC:
    id: uuid.UUID
    type: str
    value: str
    source_section: str
    occurrences: int
    confidence: float
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class ExportClassification:
    primary_category: str
    secondary_categories: list[str]
    confidence: float
    scores: dict[str, float]
    matched_terms: dict[str, list[str]]
    rules_version: str
    classified_at: datetime


@dataclass(frozen=True)
class ExportAIInsight:
    status: str
    summary: str | None
    relevance_score: float | None
    relevance_label: str | None
    relevance_reasons: list[str]
    provider: str | None
    model: str | None
    generated_at: datetime | None
    error: str | None


@dataclass(frozen=True)
class ExportArticleContent:
    final_url: str
    retrieved_at: datetime
    http_status: int
    content_type: str | None
    title: str | None
    text: str | None
    extraction_method: str | None
    language: str | None
    word_count: int | None
    error: str | None


@dataclass(frozen=True)
class ExportUserState:
    is_read: bool
    is_starred: bool
    note: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ExportRecord:
    id: uuid.UUID
    feed_id: uuid.UUID
    feed_name: str
    source_guid: str | None
    url: str
    canonical_url: str | None
    title: str
    summary: str | None
    published_at: datetime | None
    first_seen_at: datetime
    status: str
    classification: ExportClassification | None
    ai: ExportAIInsight | None
    article: ExportArticleContent | None
    state: ExportUserState
    tags: list[ExportTag] = field(default_factory=list)
    iocs: list[ExportIOC] = field(default_factory=list)
