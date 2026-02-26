import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.item import ItemListEntry


class AlertInterestCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=64)
    keywords: list[str] = Field(min_length=1, max_length=64)
    enabled: bool = True


class AlertInterestUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    keywords: list[str] | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class AlertInterestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    category: str
    keywords: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertMatchReference(BaseModel):
    alert_id: uuid.UUID
    alert_name: str
    category: str
    matched_keywords: list[str]


class AlertMatchEntry(ItemListEntry):
    matches: list[AlertMatchReference]


class AlertMatchListResponse(BaseModel):
    items: list[AlertMatchEntry]
    total: int
    page: int
    page_size: int
