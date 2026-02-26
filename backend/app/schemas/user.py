import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.rbac import ROLE_ANALYST, ROLE_VIEWER

RoleValue = Literal["admin", "analyst", "viewer"]


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    role: RoleValue = ROLE_VIEWER
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: RoleValue | None = None
    is_active: bool | None = None


class UserAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: RoleValue = ROLE_ANALYST
    is_active: bool
    created_at: datetime
