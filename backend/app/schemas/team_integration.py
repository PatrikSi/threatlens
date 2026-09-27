from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdoptIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ReceiverCredentialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")
    expires_at: datetime

    @field_validator("name")
    @classmethod
    def storage_safe_name(cls, value: str) -> str:
        if not value.strip() or any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            raise ValueError("Credential name must contain storage-safe, nonblank text")
        return value.strip()


class IntegrationEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    enabled: bool
