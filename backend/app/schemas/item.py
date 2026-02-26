import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
    is_read: bool
    is_starred: bool
    tags: list[str]


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
    last_error: str | None
    tags: list[str]
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
