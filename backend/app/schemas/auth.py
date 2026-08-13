import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.rbac import ROLE_VIEWER
from app.schemas.user import ProvisioningSource


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    new_password: str = Field(
        min_length=8,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class TokenResponse(BaseModel):
    token_type: str = "session_cookie"
    csrf_token: str


class RegistrationSettingsResponse(BaseModel):
    allow_self_registration: bool
    ai_enabled: bool


class AppFeaturesResponse(BaseModel):
    ai_enabled: bool
    ai_configured: bool
    ai_summary_enabled: bool
    ai_relevance_enabled: bool
    ai_daily_brief_enabled: bool
    ai_reporting_enabled: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str = ROLE_VIEWER
    is_active: bool
    is_approved: bool
    approved_at: datetime | None
    created_at: datetime
    password_login_enabled: bool = True
    provisioning_source: ProvisioningSource = "local"


class CurrentUserResponse(UserResponse):
    features: AppFeaturesResponse
