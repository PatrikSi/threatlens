"""Strict authorization-code/PKCE requests; no implicit or password grants."""

from typing import Literal
from urllib.parse import urlsplit
import uuid
from pydantic import ConfigDict, Field, field_validator

from app.schemas.storage_text import StorageTextInput, validate_storage_text


class OAuthClientCreate(StorageTextInput):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f]+$")
    redirect_uris: list[str] = Field(min_length=1, max_length=5)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Client name must not be blank")
        return value

    @field_validator("redirect_uris")
    @classmethod
    def validate_redirects(cls, values: list[str]) -> list[str]:
        for value in values:
            validate_storage_text(value)
            parsed = urlsplit(value)
            # urlsplit defers malformed/out-of-range port validation until access.
            _ = parsed.port
            if (
                len(value) > 2048
                or "\\" in value
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.query
                or any(ch.isspace() or ord(ch) < 32 for ch in value)
            ):
                raise ValueError(
                    "Redirect URIs must be absolute, without credentials, fragments or queries"
                )
            if parsed.scheme != "https" and not (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            ):
                raise ValueError(
                    "Use HTTPS redirect URIs or explicit loopback HTTP callbacks"
                )
            if not parsed.hostname:
                raise ValueError("Redirect URI host is required")
        return sorted(set(values))


class OAuthAuthorization(StorageTextInput):
    model_config = ConfigDict(extra="forbid")
    client_id: uuid.UUID
    redirect_uri: str = Field(max_length=2048)
    resource: str = Field(max_length=2048)
    response_type: Literal["code"] = "code"
    code_challenge_method: Literal["S256"]
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    scope: str = Field(default="read:mcp read:items", max_length=512)
    state: str = Field(min_length=16, max_length=512, pattern=r"^[^\x00-\x1f]+$")


class OAuthConsent(OAuthAuthorization):
    approve: bool
    current_password: str | None = Field(default=None, max_length=256)
    mfa_code: str | None = Field(default=None, max_length=20)


class OAuthTokenExchange(StorageTextInput):
    model_config = ConfigDict(extra="forbid")
    grant_type: Literal["authorization_code"]
    code: str = Field(min_length=32, max_length=256)
    client_id: uuid.UUID
    redirect_uri: str = Field(max_length=2048)
    resource: str = Field(max_length=2048)
    code_verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")
