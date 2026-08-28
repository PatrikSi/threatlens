import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.rbac import ROLE_ANALYST, ROLE_VIEWER

RoleValue = Literal["admin", "analyst", "viewer"]
ProvisioningSource = Literal["local", "oidc"]
AuthenticationMethod = Literal["password", "oidc"]
ManagementSource = Literal["local", "oidc"]
OIDCIdentityStatus = Literal["not_linked", "linked_available", "linked_unavailable"]


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    role: RoleValue = ROLE_VIEWER
    is_active: bool = True
    is_approved: bool = True


class UserUpdateRequest(BaseModel):
    expected_security_version: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional optimistic-concurrency precondition. Legacy requests may omit it; "
            "when supplied, it must match security_version from the latest user-directory "
            "response and is enforced for every update."
        ),
    )
    email: EmailStr | None = None
    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
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
    authentication_methods: list[AuthenticationMethod] = Field(
        default_factory=lambda: ["password"]
    )
    oidc_provider_name: str | None = None
    oidc_linked_at: datetime | None = None
    oidc_last_login_at: datetime | None = None
    identity_linked: bool = False
    sso_sign_in_available: bool = False
    oidc_identity_status: OIDCIdentityStatus = "not_linked"
    credential_management_source: ManagementSource = "local"
    password_managed_by: ManagementSource = "local"
    role_managed_by: ManagementSource = "local"
    mfa_enabled: bool = False
    mfa_confirmed_at: datetime | None = None
    active_session_count: int = Field(default=0, ge=0)
    security_version: int = Field(default=0, ge=0)
    credentials_rotated: bool = False
    revoked_api_tokens: int = Field(default=0, ge=0)
    revoked_auth_sessions: int = Field(default=0, ge=0)


class UserDirectoryResponse(BaseModel):
    users: list[UserAdminResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool = False
