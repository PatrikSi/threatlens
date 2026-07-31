import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.rbac import ROLE_VIEWER

OIDCAuthMethod = Literal["client_secret_basic", "client_secret_post", "none"]
OIDCRole = Literal["admin", "analyst", "viewer"]


class OIDCRoleMapping(BaseModel):
    claim_value: str = Field(min_length=1, max_length=255)
    role: OIDCRole

    @field_validator("claim_value")
    @classmethod
    def normalize_claim_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("claim_value must not be blank")
        return normalized


class OIDCProviderUpdateRequest(BaseModel):
    name: str = Field(default="Company SSO", min_length=1, max_length=100)
    enabled: bool = False
    issuer_url: str = Field(default="", max_length=2048)
    client_id: str = Field(default="", max_length=255)
    client_secret: str | None = Field(
        default=None,
        max_length=4096,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    clear_client_secret: bool = False
    client_auth_method: OIDCAuthMethod = "client_secret_basic"
    public_base_url: str = Field(default="", max_length=2048)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"], max_length=32)
    role_claim: str = Field(default="groups", min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.:-]+$")
    role_mappings: list[OIDCRoleMapping] = Field(default_factory=list, max_length=100)
    default_role: OIDCRole = ROLE_VIEWER
    jit_provisioning_enabled: bool = False
    auto_approve_users: bool = False
    sync_roles_on_login: bool = True

    @field_validator("name", "client_id", "role_claim")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("issuer_url")
    @classmethod
    def normalize_issuer_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("public_base_url")
    @classmethod
    def normalize_public_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("client_secret")
    @classmethod
    def normalize_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for scope in value:
            candidate = str(scope).strip()
            if not candidate or candidate in normalized:
                continue
            if len(candidate) > 128 or any(char.isspace() for char in candidate):
                raise ValueError("Each OIDC scope must be a non-blank value without whitespace")
            normalized.append(candidate)
        if "openid" not in normalized:
            raise ValueError("OIDC scopes must include openid")
        return normalized

    @model_validator(mode="after")
    def validate_configuration(self):
        duplicate_values = sorted(
            claim_value
            for claim_value in {mapping.claim_value for mapping in self.role_mappings}
            if sum(mapping.claim_value == claim_value for mapping in self.role_mappings) > 1
        )
        if duplicate_values:
            raise ValueError(f"Role mapping claim values must be unique: {', '.join(duplicate_values)}")
        if self.enabled and (not self.issuer_url or not self.client_id or not self.public_base_url):
            raise ValueError("Enabled OIDC requires issuer_url, client_id, and public_base_url")
        if self.client_auth_method == "none" and self.client_secret is not None:
            raise ValueError("Public OIDC clients must not store a client secret")
        if self.auto_approve_users and not self.jit_provisioning_enabled:
            raise ValueError("Automatic approval requires JIT provisioning")
        return self


class OIDCProviderResponse(BaseModel):
    id: uuid.UUID | None = None
    configured: bool
    name: str
    enabled: bool
    issuer_url: str
    client_id: str
    has_client_secret: bool
    client_auth_method: OIDCAuthMethod
    public_base_url: str
    callback_url: str
    scopes: list[str]
    role_claim: str
    role_mappings: list[OIDCRoleMapping]
    default_role: OIDCRole
    jit_provisioning_enabled: bool
    auto_approve_users: bool
    sync_roles_on_login: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OIDCPublicSettingsResponse(BaseModel):
    enabled: bool
    provider_name: str | None = None


class OIDCAccountStatusResponse(BaseModel):
    available: bool
    provider_name: str | None = None
    linked: bool
    linked_email: str | None = None
    linked_at: datetime | None = None
    password_login_enabled: bool


class OIDCUnlinkRequest(BaseModel):
    current_password: str = Field(
        min_length=1,
        max_length=256,
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class OIDCProviderTestResponse(BaseModel):
    status: Literal["ok"] = "ok"
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_key_count: int
