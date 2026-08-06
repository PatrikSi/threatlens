import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.rbac import ROLE_ANALYST, ROLE_VIEWER

RoleValue = Literal["admin", "analyst", "viewer"]
ProvisioningSource = Literal["local", "oidc"]
AuthenticationMethod = Literal["password", "oidc"]
ManagementSource = Literal["local", "oidc"]


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    role: RoleValue = ROLE_VIEWER
    is_active: bool = True
    is_approved: bool = True


class UserUpdateRequest(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: RoleValue | None = None
    is_active: bool | None = None
    is_approved: bool | None = None


class UserAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str = ROLE_ANALYST
    is_active: bool
    is_approved: bool
    approved_at: datetime | None
    created_at: datetime
    password_login_enabled: bool = True
    provisioning_source: ProvisioningSource = "local"
    authentication_methods: list[AuthenticationMethod] = Field(default_factory=lambda: ["password"])
    oidc_provider_name: str | None = None
    oidc_linked_at: datetime | None = None
    oidc_last_login_at: datetime | None = None
    password_managed_by: ManagementSource = "local"
    role_managed_by: ManagementSource = "local"
