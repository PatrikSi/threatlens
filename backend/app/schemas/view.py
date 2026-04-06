import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    query_json: dict


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    query_json: dict | None = None


class SavedViewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    query_json: dict
    created_at: datetime
