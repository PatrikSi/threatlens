from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

IntegrationType = Literal["smtp"]
IntegrationDirection = Literal["destination"]
IntegrationHealthStatus = Literal["unknown", "healthy", "warning", "error"]
IntegrationRunStatus = Literal["succeeded", "failed"]
SMTPSecurityMode = Literal["starttls", "ssl_tls", "none"]


class IntegrationConnectorResponse(BaseModel):
    integration_type: IntegrationType
    direction: IntegrationDirection
    display_name: str
    description: str
    config_schema_version: int
    supports_test: bool
    capabilities: list[str] = Field(default_factory=list)


class IntegrationSummaryResponse(BaseModel):
    id: uuid.UUID
    name: str
    integration_type: IntegrationType
    direction: IntegrationDirection
    enabled: bool
    configured: bool
    health_status: IntegrationHealthStatus
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    updated_at: datetime


class SMTPSettingsUpdate(BaseModel):
    enabled: bool = False
    host: str | None = Field(default=None, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: SMTPSecurityMode = "starttls"
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=4096)
    clear_password: bool = False
    from_email: EmailStr | None = None
    from_name: str | None = Field(default=None, max_length=255)
    timeout_seconds: int = Field(default=10, ge=1, le=60)

    @field_validator("host", "username", "password", "from_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None):
        if value is None:
            return value
        if any(char.isspace() for char in value):
            raise ValueError("host must not contain whitespace")
        return value

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.password and not self.username:
            raise ValueError("username is required when password is provided")
        if self.clear_password and self.password:
            raise ValueError("clear_password cannot be combined with password")
        if self.enabled:
            if not self.host:
                raise ValueError("host is required when SMTP is enabled")
            if self.from_email is None:
                raise ValueError("from_email is required when SMTP is enabled")
        return self


class SMTPSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    integration_type: IntegrationType
    direction: IntegrationDirection
    enabled: bool
    configured: bool
    schema_version: int
    host: str | None
    port: int
    security: SMTPSecurityMode
    username: str | None
    password_configured: bool
    has_unreadable_secret: bool
    from_email: EmailStr | None
    from_name: str | None
    timeout_seconds: int
    health_status: IntegrationHealthStatus
    last_test_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    last_test_duration_ms: int | None
    created_at: datetime
    updated_at: datetime


class SMTPTestRequest(BaseModel):
    recipient_email: EmailStr | None = None
    settings: SMTPSettingsUpdate | None = None


class SMTPTestResponse(BaseModel):
    success: bool
    action: Literal["connection", "send"]
    duration_ms: int | None
    recipient_email: EmailStr | None
    error_code: str | None
    error: str | None
    server_message: str | None
    tested_at: datetime
    used_unsaved_settings: bool
