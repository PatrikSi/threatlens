import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.storage_text import validate_storage_text


class TeamText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def storage_safe_text(cls, value):
        if isinstance(value, str):
            return validate_storage_text(value).strip()
        return value


class TeamCreate(TeamText):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    membership_group_id: uuid.UUID
    manager_group_id: uuid.UUID | None = None


class TeamUpdate(TeamText):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)


class TeamBindingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    membership_group_id: uuid.UUID
    manager_group_id: uuid.UUID | None = None
    active: bool


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    name: str
    description: str
    membership_group_id: uuid.UUID
    manager_group_id: uuid.UUID | None
    active: bool
    revision: int
    can_manage: bool = False
    created_at: datetime
    updated_at: datetime


class TeamListResponse(BaseModel):
    items: list[TeamResponse]
    total: int
    page: int
    page_size: int


class TeamMemberResponse(BaseModel):
    id: uuid.UUID
    email: str
    account_role: str
    is_manager: bool


class TeamMemberListResponse(BaseModel):
    items: list[TeamMemberResponse]
    total: int
    page: int
    page_size: int
