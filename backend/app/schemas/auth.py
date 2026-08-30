import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.rbac import ROLE_VIEWER
from app.schemas.user import ProvisioningSource
from app.schemas.iam import EffectiveAccessResponse


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=1,
        max_length=256,
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
        min_length=1,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    new_password: str = Field(
        min_length=8,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class ChangePasswordResponse(BaseModel):
    status: Literal["ok"] = "ok"
    sign_in_required: bool = True
    revoked_api_tokens: int = Field(ge=0)
    revoked_auth_sessions: int = Field(ge=0)


class TokenResponse(BaseModel):
    token_type: str = "session_cookie"
    csrf_token: str | None = None
    mfa_required: bool | None = None


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


class CurrentAuthenticationResponse(BaseModel):
    credential_kind: Literal["opaque_session", "legacy_session", "api_token"]
    session_id: uuid.UUID | None = None
    session_auth_method: Literal["local", "oidc"] | None = None
    mfa_method: Literal["totp", "recovery_code", "external"] | None = None
    recently_authenticated: bool = False
    recent_authentication_valid: bool = False
    recent_authentication_expires_at: datetime | None = None
    identity_provider_mfa_asserted: bool = False
    reauthentication_endpoint: str | None = None
    security_actions_supported: bool = False


class CurrentUserResponse(UserResponse):
    features: AppFeaturesResponse
    authentication: CurrentAuthenticationResponse
    access: EffectiveAccessResponse
