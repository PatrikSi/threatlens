from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")


class ServiceAccountRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceAccountCreateRequest(ServiceAccountRequestModel):
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KEY_PATTERN.fullmatch(normalized):
            raise ValueError(
                "Service-account key must contain lowercase letters, numbers, or "
                "hyphens and start with a letter."
            )
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Service-account name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class ServiceAccountUpdateRequest(ServiceAccountRequestModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Service-account name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.description is None:
            raise ValueError("At least one service-account field must be provided.")
        return self


class ServiceAccountRevisionRequest(ServiceAccountRequestModel):
    expected_revision: int = Field(ge=1)


class ServiceAccountResponse(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    is_active: bool
    revision: int = Field(ge=1)
    role_ids: list[uuid.UUID]
    effective_permissions: list[str]
    credential_count: int
    active_credential_count: int
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ServiceAccountListResponse(BaseModel):
    items: list[ServiceAccountResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ServiceAccountRoleAssignmentRequest(ServiceAccountRequestModel):
    role_id: uuid.UUID
    expected_service_account_revision: int = Field(ge=1)
    expected_role_revision: int | None = Field(default=None, ge=1)


class ServiceAccountRoleAssignmentResponse(BaseModel):
    id: uuid.UUID
    service_account_id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    role_name: str
    role_revision: int = Field(ge=1)
    created_at: datetime


class ServiceAccountCredentialIssueRequest(ServiceAccountRequestModel):
    expected_service_account_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=64)
    expires_in_days: int = Field(default=30, ge=1, le=365)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Credential name cannot be blank.")
        return normalized

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, values: list[str]) -> list[str]:
        normalized = sorted(
            {value.strip().lower() for value in values if value.strip()}
        )
        if not normalized:
            raise ValueError("At least one nonblank credential scope is required.")
        return normalized


class ServiceAccountCredentialResponse(BaseModel):
    id: uuid.UUID
    service_account_id: uuid.UUID
    rotated_from_credential_id: uuid.UUID | None
    name: str
    token_prefix: str
    scopes: list[str]
    expires_at: datetime
    original_expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str | None
    last_used_user_agent: str | None
    created_at: datetime


class ServiceAccountCredentialListResponse(BaseModel):
    items: list[ServiceAccountCredentialResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ServiceAccountCredentialIssueResponse(BaseModel):
    token: str
    credential: ServiceAccountCredentialResponse


class ServiceAccountCredentialRotateResponse(ServiceAccountCredentialIssueResponse):
    previous_credential_id: uuid.UUID
    previous_credential_revoked: bool = False
    previous_credential_expires_at: datetime
