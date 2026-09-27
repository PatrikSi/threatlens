from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class AdoptIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ReceiverCredentialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")
    expires_at: datetime


class IntegrationEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    enabled: bool
