import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.rbac import ROLE_VIEWER


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    csrf_token: str | None = None


class RegistrationSettingsResponse(BaseModel):
    allow_self_registration: bool
    ai_enabled: bool


class AppFeaturesResponse(BaseModel):
    ai_enabled: bool
    ai_configured: bool
    ai_summary_enabled: bool
    ai_relevance_enabled: bool
    ai_daily_brief_enabled: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str = ROLE_VIEWER
    is_active: bool
    is_approved: bool
    approved_at: datetime | None
    created_at: datetime


class CurrentUserResponse(UserResponse):
    features: AppFeaturesResponse
