from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.permissions import (
    PERMISSION_DEFINITIONS,
    RESERVED_CUSTOM_ROLE_PERMISSION_IDS,
    is_known_permission,
)


_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")


class IAMRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PermissionResponse(BaseModel):
    id: str
    group: str
    label: str
    description: str
    risk: Literal["standard", "elevated", "critical"]
    delegable: bool


class EffectiveRoleResponse(BaseModel):
    id: uuid.UUID | None
    key: str
    name: str
    source: str


class EffectiveAccessResponse(BaseModel):
    principal_type: Literal["user", "service_account"] = "user"
    principal_id: uuid.UUID
    legacy_role: str | None = None
    account_eligible: bool
    credential_limited: bool
    roles: list[EffectiveRoleResponse]
    groups: list[str]
    permissions: list[str]
    policy_revision: int = Field(ge=1)


class AccessExplanationResponse(BaseModel):
    permission: str
    allowed: bool
    grant_sources: list[str]
    policy_revision: int = Field(ge=1)
    reason: str


class RoleWriteRequest(IAMRequestModel):
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    permissions: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KEY_PATTERN.fullmatch(normalized):
            raise ValueError(
                "Role key must contain lowercase letters, numbers, or hyphens and start with a letter."
            )
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Role name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, values: list[str]) -> list[str]:
        normalized = sorted(
            {value.strip().lower() for value in values if value.strip()}
        )
        unknown = [value for value in normalized if not is_known_permission(value)]
        if unknown:
            raise ValueError(f"Unknown permissions: {', '.join(unknown)}")
        reserved = sorted(set(normalized) & RESERVED_CUSTOM_ROLE_PERMISSION_IDS)
        if reserved:
            raise ValueError(
                "Custom roles cannot contain sealed administrator wildcard permissions."
            )
        return normalized


class RoleUpdateRequest(IAMRequestModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    permissions: list[str] | None = Field(default=None, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Role name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_optional_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("permissions")
    @classmethod
    def validate_optional_permissions(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if values is None:
            return None
        return RoleWriteRequest.validate_permissions(values)

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.description is None and self.permissions is None:
            raise ValueError("At least one role field must be provided.")
        return self


class RoleResponse(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    revision: int
    assignment_count: int = 0
    group_count: int = 0
    created_at: datetime
    updated_at: datetime


class UserRoleAssignmentRequest(IAMRequestModel):
    role_id: uuid.UUID
    expected_role_revision: int | None = Field(default=None, ge=1)


class UserRoleAssignmentResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    role_name: str
    role_revision: int = Field(ge=1)
    source: Literal["local", "oidc"]
    source_key: str
    created_at: datetime


class GroupWriteRequest(IAMRequestModel):
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return RoleWriteRequest.validate_key(value)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Group name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class GroupUpdateRequest(IAMRequestModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Group name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_optional_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.description is None:
            raise ValueError("At least one group field must be provided.")
        return self


class GroupMemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    source: Literal["local", "oidc"]
    source_key: str
    created_at: datetime


class GroupResponse(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    source: Literal["local", "oidc"]
    external_key: str | None
    is_system: bool
    revision: int
    member_count: int
    role_ids: list[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class GroupMemberRequest(IAMRequestModel):
    user_id: uuid.UUID


class GroupRoleRequest(IAMRequestModel):
    role_id: uuid.UUID
    expected_role_revision: int | None = Field(default=None, ge=1)


class GroupRoleAssignmentResponse(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    role_name: str
    role_revision: int = Field(ge=1)
    created_at: datetime


def permission_responses() -> list[PermissionResponse]:
    return [
        PermissionResponse(
            id=permission.id,
            group=permission.group,
            label=permission.label,
            description=permission.description,
            risk=permission.risk,
            delegable=permission.delegable,
        )
        for permission in PERMISSION_DEFINITIONS
    ]
