from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


OIDCMissingClaimBehavior = Literal["preserve", "remove", "deny"]

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_CLAIM_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_:-]+(?:[.][A-Za-z0-9_:-]+)*$")
_ROLE_SOURCE_KEY_PATTERN = r"^oidc:role:[0-9a-f]{32}$"
_GROUP_SOURCE_KEY_PATTERN = r"^oidc:group:[0-9a-f]{32}$"
_MAX_MAPPINGS_PER_TYPE = 256


class OIDCAccessAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OIDCAccessAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _normalize_name(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank.")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{field_name} cannot contain control characters.")
    return normalized


def _validate_exact_claim_value(value: str) -> str:
    if not value:
        raise ValueError("Claim value cannot be empty.")
    if value != value.strip():
        raise ValueError(
            "Claim value cannot have leading or trailing whitespace because matching "
            "is exact."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Claim value cannot contain control characters.")
    return value


class OIDCRoleValueMappingRequest(OIDCAccessAdminRequest):
    claim_value: str = Field(
        min_length=1,
        max_length=512,
        description="Configured exact, case-sensitive value; not an observed claim.",
    )
    role_id: uuid.UUID = Field(
        description="Identifier of a custom, non-system IAM role."
    )

    @field_validator("claim_value")
    @classmethod
    def validate_claim_value(cls, value: str) -> str:
        return _validate_exact_claim_value(value)


class OIDCGroupValueMappingRequest(OIDCAccessAdminRequest):
    claim_value: str = Field(
        min_length=1,
        max_length=512,
        description="Configured exact, case-sensitive value; not an observed claim.",
    )
    group_id: uuid.UUID = Field(description="Identifier of a non-system IAM group.")

    @field_validator("claim_value")
    @classmethod
    def validate_claim_value(cls, value: str) -> str:
        return _validate_exact_claim_value(value)


class OIDCClaimMappingSetCreateRequest(OIDCAccessAdminRequest):
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    claim_path: str = Field(min_length=1, max_length=255)
    missing_claim_behavior: OIDCMissingClaimBehavior = "preserve"
    enabled: bool = True
    role_mappings: list[OIDCRoleValueMappingRequest] = Field(
        default_factory=list,
        max_length=_MAX_MAPPINGS_PER_TYPE,
    )
    group_mappings: list[OIDCGroupValueMappingRequest] = Field(
        default_factory=list,
        max_length=_MAX_MAPPINGS_PER_TYPE,
    )

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KEY_PATTERN.fullmatch(normalized):
            raise ValueError(
                "Mapping-set key must contain lowercase letters, numbers, or "
                "hyphens and start with a letter."
            )
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_name(value, field_name="Mapping-set name")

    @field_validator("claim_path")
    @classmethod
    def validate_claim_path(cls, value: str) -> str:
        if not _CLAIM_PATH_PATTERN.fullmatch(value):
            raise ValueError(
                "Claim path must contain dot-separated letters, numbers, "
                "underscores, colons, or hyphens."
            )
        return value

    @model_validator(mode="after")
    def validate_unique_values(self):
        _require_unique_claim_values(self.role_mappings, target="role")
        _require_unique_claim_values(self.group_mappings, target="group")
        return self


class OIDCClaimMappingSetUpdateRequest(OIDCAccessAdminRequest):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    claim_path: str | None = Field(default=None, min_length=1, max_length=255)
    missing_claim_behavior: OIDCMissingClaimBehavior | None = None
    enabled: bool | None = None
    role_mappings: list[OIDCRoleValueMappingRequest] | None = Field(
        default=None,
        max_length=_MAX_MAPPINGS_PER_TYPE,
    )
    group_mappings: list[OIDCGroupValueMappingRequest] | None = Field(
        default=None,
        max_length=_MAX_MAPPINGS_PER_TYPE,
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_name(value, field_name="Mapping-set name")

    @field_validator("claim_path")
    @classmethod
    def validate_claim_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return OIDCClaimMappingSetCreateRequest.validate_claim_path(value)

    @model_validator(mode="after")
    def validate_update(self):
        mutable_values = (
            self.name,
            self.claim_path,
            self.missing_claim_behavior,
            self.enabled,
            self.role_mappings,
            self.group_mappings,
        )
        if all(value is None for value in mutable_values):
            raise ValueError("At least one mapping-set field must be provided.")
        if self.role_mappings is not None:
            _require_unique_claim_values(self.role_mappings, target="role")
        if self.group_mappings is not None:
            _require_unique_claim_values(self.group_mappings, target="group")
        return self


class OIDCClaimMappingSetRevisionRequest(OIDCAccessAdminRequest):
    expected_revision: int = Field(ge=1)


class OIDCAccessPolicyCreateRequest(OIDCAccessAdminRequest):
    enabled: bool = False


class OIDCAccessPolicyUpdateRequest(OIDCAccessAdminRequest):
    expected_revision: int = Field(ge=1)
    enabled: bool


class OIDCRoleValueMappingResponse(OIDCAccessAdminResponse):
    id: uuid.UUID
    source_key: str = Field(pattern=_ROLE_SOURCE_KEY_PATTERN, max_length=64)
    claim_value: str = Field(min_length=1, max_length=512)
    role_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class OIDCGroupValueMappingResponse(OIDCAccessAdminResponse):
    id: uuid.UUID
    source_key: str = Field(pattern=_GROUP_SOURCE_KEY_PATTERN, max_length=64)
    claim_value: str = Field(min_length=1, max_length=512)
    group_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class OIDCClaimMappingSetResponse(OIDCAccessAdminResponse):
    id: uuid.UUID
    access_policy_id: uuid.UUID
    key: str = Field(min_length=3, max_length=64, pattern=_KEY_PATTERN.pattern)
    name: str = Field(min_length=1, max_length=120)
    claim_path: str = Field(
        min_length=1,
        max_length=255,
        pattern=_CLAIM_PATH_PATTERN.pattern,
    )
    missing_claim_behavior: OIDCMissingClaimBehavior
    enabled: bool
    revision: int = Field(ge=1)
    role_mappings: list[OIDCRoleValueMappingResponse] = Field(
        default_factory=list,
        max_length=_MAX_MAPPINGS_PER_TYPE,
    )
    group_mappings: list[OIDCGroupValueMappingResponse] = Field(
        default_factory=list,
        max_length=_MAX_MAPPINGS_PER_TYPE,
    )
    created_at: datetime
    updated_at: datetime


class OIDCAccessPolicyResponse(OIDCAccessAdminResponse):
    id: uuid.UUID
    provider_id: uuid.UUID
    enabled: bool
    revision: int = Field(ge=1)
    generation: int = Field(ge=1)
    mapping_sets: list[OIDCClaimMappingSetResponse] = Field(
        default_factory=list,
        max_length=128,
    )
    created_at: datetime
    updated_at: datetime


class OIDCAccessPolicyStateResponse(OIDCAccessAdminResponse):
    configured: bool
    provider_id: uuid.UUID | None = None
    policy: OIDCAccessPolicyResponse | None = None


def _require_unique_claim_values(
    mappings: list[OIDCRoleValueMappingRequest] | list[OIDCGroupValueMappingRequest],
    *,
    target: str,
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for mapping in mappings:
        if mapping.claim_value in seen:
            duplicates.add(mapping.claim_value)
        seen.add(mapping.claim_value)
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(
            f"Exact {target} claim values must be unique within a mapping set: "
            f"{duplicate_list}"
        )
