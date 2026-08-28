import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AuthSessionResponse(BaseModel):
    id: uuid.UUID
    current: bool
    auth_method: Literal["local", "oidc"]
    mfa_method: Literal["totp", "recovery_code", "external"] | None
    client_ip: str | None
    user_agent: str | None
    authenticated_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None


class AuthSessionListResponse(BaseModel):
    sessions: list[AuthSessionResponse]
    active_count: int = Field(ge=0)
    active_truncated: bool = False
    history_truncated: bool = False


class TOTPStatusResponse(BaseModel):
    local_mfa_available: bool = True
    managed_by: Literal["local", "identity_provider"] = "local"
    enabled: bool
    confirmed_at: datetime | None
    recovery_codes_remaining: int = Field(ge=0)


class TOTPEnrollmentStartRequest(BaseModel):
    current_password: str = Field(
        min_length=1,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class TOTPEnrollmentStartResponse(BaseModel):
    secret: str = Field(json_schema_extra={"writeOnly": True})
    provisioning_uri: str = Field(json_schema_extra={"writeOnly": True})


class TOTPEnrollmentCancelResponse(BaseModel):
    status: Literal["ok"] = "ok"
    cancelled: bool


class TOTPConfirmRequest(BaseModel):
    code: str = Field(
        min_length=6, max_length=64, json_schema_extra={"writeOnly": True}
    )


class TOTPRecoveryCodesResponse(BaseModel):
    recovery_codes: list[str] = Field(json_schema_extra={"writeOnly": True})
    generated_at: datetime


class TOTPSensitiveActionRequest(BaseModel):
    current_password: str = Field(
        min_length=1,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    code: str = Field(
        min_length=6, max_length=64, json_schema_extra={"writeOnly": True}
    )


class MFALoginVerifyRequest(BaseModel):
    code: str = Field(
        min_length=6, max_length=64, json_schema_extra={"writeOnly": True}
    )


class AdminMFAResetRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    current_password: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    code: str | None = Field(
        default=None, min_length=6, max_length=64, json_schema_extra={"writeOnly": True}
    )

    @field_validator("reason")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return normalized


class AdminMFAResetResponse(BaseModel):
    status: Literal["ok"] = "ok"
    disabled: bool
    revoked_api_tokens: int = Field(ge=0)
    revoked_auth_sessions: int = Field(ge=0)


class SessionRevocationResponse(BaseModel):
    status: Literal["ok"] = "ok"
    revoked: bool
    current_session_revoked: bool = False
    revoked_session_count: int = Field(default=0, ge=0)
    other_sessions_revoked: int = Field(default=0, ge=0)
    auth_generation_rotated: bool = False


class SessionBulkRevocationResponse(BaseModel):
    status: Literal["ok"] = "ok"
    revoked_count: int = Field(ge=0)


class TOTPDisableResponse(BaseModel):
    status: Literal["ok"] = "ok"
    disabled: bool
    revoked_sessions: int = Field(ge=0)


class RecentAuthenticationRequest(BaseModel):
    current_password: str = Field(
        min_length=1,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    code: str | None = Field(
        default=None,
        min_length=6,
        max_length=64,
        description="Required when local MFA is enabled. Recovery codes are not accepted.",
        json_schema_extra={"writeOnly": True},
    )


class RecentAuthenticationResponse(BaseModel):
    status: Literal["ok"] = "ok"
    auth_method: Literal["local"] = "local"
    verification_method: Literal["password", "password_totp"]
    session_id: uuid.UUID
    authenticated_at: datetime
    valid_until: datetime
    session_rotated: bool = True
