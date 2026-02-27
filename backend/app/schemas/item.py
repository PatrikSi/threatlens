import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemTagDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    source: str
    confidence: float
    rules_version: str | None


class ItemTagSuggestionResponse(BaseModel):
    name: str
    source: str
    confidence: float
    rules_version: str | None


class ItemListEntry(BaseModel):
    id: uuid.UUID
    feed_id: uuid.UUID
    feed_name: str
    url: str
    canonical_url: str | None
    title: str
    summary: str | None
    published_at: datetime | None
    first_seen_at: datetime
    status: str
    classification: str | None
    is_read: bool
    is_starred: bool
    tags: list[str]
    tag_details: list[ItemTagDetailResponse] = Field(default_factory=list)


class ItemListResponse(BaseModel):
    items: list[ItemListEntry]
    total: int
    page: int
    page_size: int


class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    final_url: str
    retrieved_at: datetime
    http_status: int
    content_type: str | None
    title_extracted: str | None
    text: str | None
    extraction_method: str | None
    language: str | None
    word_count: int | None
    fetch_ms: int | None
    error: str | None


class ItemStateResponse(BaseModel):
    is_read: bool
    is_starred: bool
    note: str | None
    updated_at: datetime | None


class ItemClassificationResponse(BaseModel):
    primary_category: str
    secondary_categories: list[str]
    confidence: float
    scores: dict[str, float]
    rules_version: str
    classified_at: datetime


class ItemGraphNodeResponse(BaseModel):
    id: str
    type: str
    label: str
    metadata: dict[str, Any]


class ItemGraphEdgeResponse(BaseModel):
    source: str
    target: str
    relation: str
    weight: float


class ItemGraphResponse(BaseModel):
    nodes: list[ItemGraphNodeResponse]
    edges: list[ItemGraphEdgeResponse]
    focus_node_id: str | None = None
    root_item_id: str | None = None


class ItemDetailResponse(BaseModel):
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
    classification: ItemClassificationResponse | None
    last_error: str | None
    tags: list[str]
    tag_details: list[ItemTagDetailResponse] = Field(default_factory=list)
    tag_suggestions: list[ItemTagSuggestionResponse] = Field(default_factory=list)
    article: ArticleResponse | None
    state: ItemStateResponse


class ReadUpdateRequest(BaseModel):
    is_read: bool


class StarUpdateRequest(BaseModel):
    is_starred: bool


class NoteUpdateRequest(BaseModel):
    note: str | None


class ItemTagsUpdateRequest(BaseModel):
    tag_ids: list[uuid.UUID]

    @field_validator("tag_ids")
    @classmethod
    def validate_tag_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if not value:
            return value

        seen: set[uuid.UUID] = set()
        duplicates: list[str] = []
        for tag_id in value:
            if tag_id in seen:
                duplicates.append(str(tag_id))
                continue
            seen.add(tag_id)

        if duplicates:
            unique_duplicates = sorted(set(duplicates))
            raise ValueError(f"Duplicate tag IDs are not allowed: {', '.join(unique_duplicates)}")
        return value


class ItemTagSuggestionListResponse(BaseModel):
    item_id: uuid.UUID
    suggestions: list[ItemTagSuggestionResponse]
