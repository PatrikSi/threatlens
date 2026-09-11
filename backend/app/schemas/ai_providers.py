from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.core.config import get_settings
from app.services.url_utils import is_fetchable_url


class AIProviderFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=4000)
    model: str = Field(min_length=1, max_length=255)
    temperature: float = Field(default=0.2, ge=0, le=2, allow_inf_nan=False)
    max_completion_tokens: int = Field(default=5000, ge=128, le=8192)
    request_timeout_seconds: int = Field(default=300, ge=5, le=300)
    request_max_retries: int = Field(default=3, ge=0, le=5)
    enabled: bool = True

    @field_validator("name", "model", "base_url", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AIProviderWrite(AIProviderFields):
    api_key: SecretStr | None = Field(default=None, max_length=16384)
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Endpoint must be a valid URL.") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Endpoint must use http or https and include a host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "Endpoint must not contain credentials, query parameters or fragments."
            )
        if "{{" in parsed.netloc:
            raise ValueError("Endpoint host must not contain templates.")
        allow_private = bool(get_settings().allow_private_network_ai)
        if parsed.scheme.lower() != "https" and (
            not allow_private or is_fetchable_url(value, allow_private_network=False)
        ):
            raise ValueError(
                "Public endpoints require HTTPS; private HTTP requires ALLOW_PRIVATE_NETWORK_AI."
            )
        if not is_fetchable_url(value, allow_private_network=allow_private):
            raise ValueError(
                "Endpoint is not allowed by the outbound AI network policy."
            )
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_key_action(self):
        if self.api_key is not None:
            key = self.api_key.get_secret_value()
            if not key.strip() or any(
                ord(char) < 32 or ord(char) > 126 for char in key
            ):
                raise ValueError(
                    "API key must be nonempty printable ASCII without control characters."
                )
            if self.clear_api_key:
                raise ValueError(
                    "Choose either a replacement API key or clear API key."
                )
        return self


class AIProviderCreate(AIProviderWrite):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)


class AIProviderUpdate(AIProviderWrite):
    version: int = Field(ge=1)


class AIProviderResponse(AIProviderFields):
    id: uuid.UUID
    version: int
    api_key_configured: bool
    credential_error: str | None
    created_at: datetime
    updated_at: datetime


class AIProviderListResponse(BaseModel):
    items: list[AIProviderResponse]
    total: int
    limit: int
    offset: int


class AIProviderRoutingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    default_provider_id: uuid.UUID | None = None
    item_enrichment_provider_id: uuid.UUID | None = None
    daily_brief_provider_id: uuid.UUID | None = None
    report_provider_id: uuid.UUID | None = None


class AIProviderRoutingUpdate(AIProviderRoutingResponse):
    pass
