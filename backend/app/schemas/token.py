import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.token_scopes import is_scope_allowed, normalize_token_scopes


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    scopes: list[str] = Field(default_factory=list)
    current_password: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description=(
            "Required when creating an API token from a browser cookie session. "
            "Callers already authenticated with an API token can omit it."
        ),
        json_schema_extra={"format": "password", "writeOnly": True},
    )

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        normalized = normalize_token_scopes(value)
        invalid = [scope for scope in normalized if not is_scope_allowed(scope)]
        if invalid:
            raise ValueError(f"Unsupported scopes: {', '.join(invalid)}")
        return normalized

    @model_validator(mode="after")
    def validate_explicit_empty_scope_list(self):
        if "scopes" in self.model_fields_set and not self.scopes:
            raise ValueError("scopes must include at least one value; omit the field to use default scopes")
        return self


class ApiTokenCreateResponse(BaseModel):
    token: str
    token_prefix: str
    expires_at: datetime | None


class ApiTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    token_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
